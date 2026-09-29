"""LOOM -> CapCut RUNNER (Phase 2B "Finish in CapCut" lane).

The mapper in ``capcut_handoff`` is pure and knows nothing about the disk. This
runner is the operational half: it takes the state LOOM already holds for the
CURRENT version and turns it into an editable CapCut project, then reports where
that project landed.

Pipeline (one direction only — there is no import back from CapCut)::

    current LOOM version state
      -> resolve WORKING coordinates (never ORIGINAL ones)
      -> capcut_handoff.build_capcut_spec()          (the committed mapper)
      -> safe local media handoff (copy into a scratch workspace)
      -> vendored capcut-cli `compile`               (optional external tool)
      -> <CapCut draft store>/<name>                 (a real, openable project)

DESIGN CONSTRAINTS (all deliberate)
-----------------------------------
* **Never a required dependency.** If the CLI or the draft store is absent this
  raises ``CapCutHandoffUnavailable`` and LOOM carries on exactly as before.
  Nothing here is imported by LOOM's normal edit/render path.
* **The vendor is pinned, never HEAD.** The CLI is resolved through
  ``capcut_handoff.capcut_cli_path()`` (env -> vendored -> PATH) and invoked as a
  child process. No global npm install is needed or attempted.
* **The current version is authoritative.** Version selection delegates to the
  SAME accepted helpers the editor bootstrap uses (``adapter``), so there is one
  source of truth and no chance of a second, divergent notion of "current".
  An earlier render/correction/source is never silently substituted.
* **Working coordinates only.** CapCut receives the WORKING asset, so every time
  it is given is relative to THAT asset. ORIGINAL source timestamps are
  translated once and never leak into the spec.
* **Failure is isolated by construction.** This module only READS LOOM state and
  writes to its own scratch workspace plus the CapCut draft store. It never
  writes a LOOM version, approval, score, session, SourceWindow or GHL state,
  and never moves or deletes the working asset.
* **No invented CapCut features.** No URL protocol, no CapCut API, no sync, no
  auto-export. The project is written where the installed CapCut app already
  looks for projects; opening it stays a human action.

COPY LIFECYCLE (the media handoff)
----------------------------------
The working asset is **copied** (never moved, never re-downloaded) into a
per-handoff scratch workspace, and ``compile`` copies it again into the draft's
own ``assets/`` folder — a real byte copy, verified, not a symlink. That makes
the generated draft self-contained: it opens with no missing media even if the
scratch copy is gone. So on SUCCESS the staged copy is removed again (scratch is
scratch, and this deliberately does not become a second durable store); on
FAILURE the workspace is left in place for forensics. The original working asset
is untouched in both cases.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Callable

import capcut_handoff as ch

# The installed CapCut Pro app looks here for projects (verified on this host:
# CapCut Pro 9.2.0, 7 existing projects + root_meta_info.json). This is the SAME
# location the vendored CLI defaults to for macOS, so it is measured, not
# invented. CAPCUT_DRAFT_DIR overrides it (the donor's own convention).
CAPCUT_DRAFT_DIR_ENV = "CAPCUT_DRAFT_DIR"
CAPCUT_MACOS_DRAFT_DIR = "~/Movies/CapCut/User Data/Projects/com.lveditor.draft"

# Scratch space for the media handoff. Transient by design.
DEFAULT_HANDOFF_ROOT = "~/Movies/LOOM-CapCut-Handoff"

COMPILE_TIMEOUT_SECONDS = 240
# Tolerance when checking a working-coordinate range against the real asset.
WORKING_EPSILON = 0.05
DRAFT_NAME_PREFIX = "LOOM "

# The editor executables the vendored CLI itself guards against (its own
# `editorProcesses()` list). Measured 2026-09-28: while CapCut 9.2.0 was open,
# `compile` refused with "refused [editor-open]" — so the lane must pre-flight it
# rather than let a refused write leave a half-built project behind.
EDITOR_PROCESS_BASENAMES = ("CapCut", "JianyingPro")


def capcut_editor_running() -> list[str]:
    """Running CapCut/JianYing editors, using the donor's own detection rule.

    POSIX `ps -axo comm=` matched on the exact executable basename (never a
    substring of the whole listing — that is how the CLI itself once detected
    npx and refused every write, donor issue #99). Best-effort: a failure to
    inspect processes reports "none running" rather than blocking the lane.
    """
    try:
        listing = subprocess.run(["ps", "-axo", "comm="], capture_output=True,
                                 text=True, timeout=5).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[str] = []
    for line in listing.splitlines():
        name = os.path.basename(line.strip())
        if name in EDITOR_PROCESS_BASENAMES and name not in found:
            found.append(name)
    return found


def is_managed_draft_store(store: str) -> bool:
    """Whether a store is one CapCut itself owns (a ``com.lveditor.draft`` path).

    Mirrors the vendored CLI's own ``isManagedDraftPath`` rule: the editor-open
    guard protects MANAGED drafts, because only those can be pointed at by a
    running app. A scratch directory is outside that contract.
    """
    return "/com.lveditor.draft/" in (str(store or "").replace("\\", "/") + "/")


def _discard_created_draft(target: str, store: str) -> bool:
    """Remove a draft directory that THIS run created when a compile failed.

    A refused `compile` can still leave an empty project directory behind AND
    register it in the store's `root_meta_info.json`, which would show the
    operator a broken project. Only the directory this run created is removed,
    and only a now-dangling index entry is dropped (an entry whose folder still
    exists is never touched).
    """
    if not os.path.isdir(target):
        return False
    try:
        shutil.rmtree(target)
    except OSError:
        return False
    index = os.path.join(store, "root_meta_info.json")
    if os.path.isfile(index):
        try:
            with open(index, encoding="utf-8") as fh:
                meta = json.load(fh)
            rows = meta.get("all_draft_store")
            if isinstance(rows, list):
                kept = [r for r in rows
                        if os.path.isdir(str(r.get("draft_fold_path") or ""))]
                if len(kept) != len(rows):
                    meta["all_draft_store"] = kept
                    with open(index, "w", encoding="utf-8") as fh:
                        json.dump(meta, fh, indent=1)
        except (OSError, ValueError):
            pass
    return True


def _unavailable(message: str):
    return ch.CapCutHandoffUnavailable(message)


# ---------------------------------------------------------------------------
# Lane availability: the CLI and the draft store
# ---------------------------------------------------------------------------
def capcut_draft_store() -> str:
    """The real CapCut project store, or "" when there is none on this machine.

    Never guesses: an env override wins (the donor's documented
    ``CAPCUT_DRAFT_DIR``), otherwise the platform's real location is used only
    if it actually exists. "" means "CapCut is not installed here", which the
    caller must report as an unavailable lane rather than write somewhere
    arbitrary.
    """
    override = (os.environ.get(CAPCUT_DRAFT_DIR_ENV) or "").strip()
    if override:
        return override if os.path.isdir(override) else ""
    candidate = os.path.expanduser(CAPCUT_MACOS_DRAFT_DIR)
    return candidate if os.path.isdir(candidate) else ""


def capcut_invocation(cli_path: str = "") -> list[str]:
    """The command prefix that runs the pinneable CLI, or [] when unavailable.

    ``capcut_cli_path()`` can resolve to either a vendored *directory* or an
    executable on ``PATH``; both shapes are honoured here rather than changing
    that accepted contract. [] means "lane unavailable" and is never an error.
    """
    resolved = (cli_path or "").strip() or ch.capcut_cli_path()
    if not resolved:
        return []
    if os.path.isdir(resolved):
        entry = os.path.join(resolved, "dist", "index.js")
        if not os.path.isfile(entry):
            return []
        node = shutil.which("node")
        return [node, entry] if node else []
    if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
        return [resolved]
    return []


def vendored_template_dir(cli_path: str = "") -> str:
    """The bundled ``_init`` template that belongs to the resolved CLI.

    Passed explicitly to ``compile`` so the template is always the one vendored
    next to the CLI, never whatever happens to sit in a parent directory.
    """
    resolved = (cli_path or "").strip() or ch.capcut_cli_path()
    root = resolved if os.path.isdir(resolved) else os.path.dirname(resolved)
    candidate = os.path.join(root, "templates", "_init")
    return candidate if os.path.isdir(candidate) else ""


# ---------------------------------------------------------------------------
# WORKING coordinates — the proven contract
# ---------------------------------------------------------------------------
def resolve_working_coords(*, original_start: float, original_end: float,
                           timebase_origin: float = 0.0,
                           working_duration: float | None = None) -> dict[str, Any]:
    """ORIGINAL source seconds -> WORKING seconds, failing closed on nonsense.

    ORIGINAL is the source's own timeline (a Kick VOD's minute 30). WORKING is
    the durable asset's 0-based timeline, where working t=0 is
    ``timebase_origin`` in the source. CapCut is handed the WORKING asset, so
    this is the ONLY translation allowed, and it happens exactly once.

    Guards (each one is a real way to corrupt a draft):
      * an origin ABOVE the window start would need a negative seek — refused,
        because that means the two coordinate systems were mixed up;
      * a range starting at/after the working asset's end would seek past the
        media (the "minute-30 seek inside a one-minute asset" bug) — refused;
      * a range ending beyond the working asset (past tolerance) is refused.
    """
    try:
        start = float(original_start)
        end = float(original_end)
        origin = float(timebase_origin or 0.0)
    except (TypeError, ValueError):
        raise _unavailable("working-coordinate inputs must be numbers") from None
    if not (end > start):
        raise _unavailable(f"invalid source window {start!r}..{end!r}")
    if start < 0 or origin < 0:
        raise _unavailable("source window and timebase origin must be >= 0")

    working_start = round(start - origin, 3)
    working_end = round(end - origin, 3)
    if working_start < 0:
        raise _unavailable(
            f"timebase origin {origin}s is beyond the window start {start}s — "
            "ORIGINAL and WORKING coordinates have been mixed up")
    duration = round(working_end - working_start, 3)

    if working_duration is not None:
        try:
            asset = float(working_duration)
        except (TypeError, ValueError):
            asset = 0.0
        if asset > 0:
            if working_start >= asset - WORKING_EPSILON:
                raise _unavailable(
                    f"window start {working_start}s is outside the working asset "
                    f"({asset}s) — an ORIGINAL timestamp leaked into WORKING "
                    "coordinates")
            if working_end > asset + WORKING_EPSILON:
                raise _unavailable(
                    f"window end {working_end}s exceeds the working asset "
                    f"({asset}s)")
    return {"start": working_start, "end": working_end, "duration": duration,
            "timebase_origin": origin, "original": {"start": start, "end": end}}


def working_coordinates(*, working_start: float, working_end: float,
                        timebase_origin: float = 0.0,
                        working_duration: float | None = None) -> dict[str, Any]:
    """Validate a range that is ALREADY expressed in working seconds.

    No translation happens here — that is the point. LOOM's durable editor range
    is working-relative by contract (``boundary_plan.recommended_clip_*`` is read
    downstream as relative to the durable working asset, with the ORIGINAL pair
    preserved separately), so the origin is provenance for the report, never
    something to subtract again.

    Guards, each one a real way to corrupt a draft:
      * end must exceed start;
      * the range must start inside the working asset (an ORIGINAL timestamp
        would start past its end — the "minute-30 seek inside a one-minute
        asset" bug);
      * it must not run past the working asset (past tolerance).
    """
    try:
        start = float(working_start)
        end = float(working_end)
        origin = float(timebase_origin or 0.0)
    except (TypeError, ValueError):
        raise _unavailable("working-coordinate inputs must be numbers") from None
    if not (end > start):
        raise _unavailable(f"invalid working range {start!r}..{end!r}")
    if start < 0 or origin < 0:
        raise _unavailable("working range and timebase origin must be >= 0")
    if working_duration is not None:
        try:
            asset = float(working_duration)
        except (TypeError, ValueError):
            asset = 0.0
        if asset > 0:
            if start >= asset - WORKING_EPSILON:
                raise _unavailable(
                    f"window start {start}s is outside the working asset "
                    f"({asset}s) — an ORIGINAL timestamp leaked into WORKING "
                    "coordinates")
            if end > asset + WORKING_EPSILON:
                raise _unavailable(
                    f"window end {end}s exceeds the working asset ({asset}s)")
    return {"start": round(start, 3), "end": round(end, 3),
            "duration": round(end - start, 3), "timebase_origin": origin}


def working_coordinates_for(bootstrap: dict[str, Any],
                            acquisition: dict[str, Any] | None = None,
                            original_window: dict[str, Any] | None = None,
                            ) -> dict[str, Any]:
    """Working coordinates for the version being exported.

    The coordinate system is decided by WHAT THE CALLER SUPPLIED, never inferred
    from a version label. Inferring it is exactly how a range gets translated
    twice: LOOM's durable range is already working-relative, so translating it
    again would silently double the offset.

      * ``original_window`` given -> ORIGINAL source seconds, translated exactly
        once through the recorded ``working_timebase_origin_s``.
      * otherwise -> ``bootstrap['source_range']`` is already working-relative
        and passes through with the origin recorded as provenance.
    """
    acq = acquisition if isinstance(acquisition, dict) else {}
    origin = acq.get("working_timebase_origin_s") or 0.0
    working_duration = acq.get("working_source_duration_s") or None

    if original_window:
        try:
            original_start = float(original_window.get("start"))
            original_end = float(original_window.get("end"))
        except (TypeError, ValueError):
            raise _unavailable("the supplied original window is not numeric") from None
        coords = resolve_working_coords(
            original_start=original_start, original_end=original_end,
            timebase_origin=origin, working_duration=working_duration)
        return coords

    source_range = bootstrap.get("source_range") or {}
    try:
        start = float(source_range.get("start"))
        end = float(source_range.get("end"))
    except (TypeError, ValueError):
        raise _unavailable("edit state has no usable source_range") from None
    coords = working_coordinates(working_start=start, working_end=end,
                                timebase_origin=origin,
                                working_duration=working_duration)
    # ORIGINAL provenance, when the records carry it, is reported — not applied.
    original = None
    if acq.get("original_start_s") is not None and acq.get("original_end_s") is not None:
        try:
            original = {"start": float(acq["original_start_s"]),
                        "end": float(acq["original_end_s"])}
        except (TypeError, ValueError):
            original = None
    if original:
        coords["original"] = original
    return coords


# ---------------------------------------------------------------------------
# The authoritative current version
# ---------------------------------------------------------------------------
#: Version prefixes that denote the ORIGINAL source rather than a LOOM-produced
#: render. ``source-1.0`` is the accepted source-review version string.
SOURCE_VERSION_PREFIXES = ("source",)


def _version_contract():
    """The accepted version-selection helpers, or a clean lane refusal.

    Imported lazily and defensively: the adapter pulls in LOOM's acquisition
    stack, which in some process layouts can fail to import (a module/package
    name collision outside this lane). A handoff must never let that surface as
    an unexpected exception, and must certainly never export a guess about which
    version is current — so it becomes a truthful "lane unavailable" instead.
    """
    try:
        import adapter
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(
            "could not consult LOOM's version contract: "
            f"{type(exc).__name__}: {exc}") from None
    return adapter


def is_source_version(version: Any) -> bool:
    """Whether a version string denotes the original source, not a render."""
    parsed = _version_contract().parse_asset_version(version)
    return bool(parsed) and parsed[0].lower() in SOURCE_VERSION_PREFIXES


def resolve_current_version(media_rows: Any, requested_version: Any):
    """(version, mode, reason) for the version the operator is CURRENTLY viewing.

    Delegates to the accepted ``adapter`` helpers rather than re-deriving the
    rule, so this cannot drift from what the editor itself boots with:
    a pinned/requested version is honoured and NEVER silently substituted
    (``version_not_found`` when it is unknown), and ``''``/``latest``/``*``
    resolves to the numerically-newest parseable version — numeric, so
    ``render-1.10`` correctly beats ``render-1.9``.

    One correction on top of that, MEASURED here: the accepted ordering compares
    the version's prefix alphabetically, and ``"source" > "render"``, so a
    genuinely unpinned ``latest`` request can hand back ``source-1.0`` — the
    ORIGINAL source — in preference to a later render of it. Exporting that would
    be exactly the silent original-source export this lane must never do, so an
    unpinned request prefers the newest version that is NOT a source version when
    any exists. An explicitly pinned ``source-1.0`` is still honoured: that is
    the operator asking for the source, which is a different thing entirely.
    """
    import adapter  # lazy: keeps this module importable without adapter's env
    rows = [r for r in (media_rows or []) if isinstance(r, dict)]
    try:
        version, mode, reason = adapter.resolve_asset_version_selection(
            rows, requested_version)
    except Exception as exc:  # noqa: BLE001
        # Without the accepted contract there is no trustworthy answer about
        # which version is current, so refuse rather than export a guess.
        raise _unavailable(
            "could not resolve the current version from LOOM's durable state: "
            f"{type(exc).__name__}") from None
    if mode == adapter.ASSET_VERSION_MODE_LATEST and version and is_source_version(version):
        derived = [r for r in rows
                   if not is_source_version(r.get("asset_version"))]
        newest_derived = adapter.newest_media_version(derived)
        if newest_derived:
            return newest_derived, mode, reason
    return version, mode, reason

def select_current_correction(corrections: Any, version: Any) -> dict[str, Any]:
    """The NEWEST correction recorded for ``version`` (never an earlier one).

    Corrections are immutable and one is appended per completed edit, so a
    version can legitimately have several; taking whichever row the API happened
    to return first is how a stale trim reaches the draft. Ordering is by
    ``created_at`` with the row id as a deterministic tie-break.
    """
    rows = [c for c in (corrections or [])
            if isinstance(c, dict) and c.get("target_asset_version") == version]
    if not rows:
        return {}

    def key(row: dict[str, Any]):
        return (str(row.get("created_at") or ""), str(row.get("id") or ""))

    return max(rows, key=key)


# ---------------------------------------------------------------------------
# Media handoff (copy, never move)
# ---------------------------------------------------------------------------
def staging_name_for(media_path: str) -> str:
    ext = os.path.splitext(media_path)[1] or ".mp4"
    return f"working{ext.lower()}"


def stage_working_media(media_path: str, *, workspace: str) -> dict[str, Any]:
    """Copy the working asset into the handoff workspace; the original is never touched."""
    if not media_path or not os.path.isfile(media_path):
        raise _unavailable(
            f"no local working media for the handoff (got {media_path!r}); CapCut "
            "cannot open a draft with missing media")
    media_dir = os.path.join(workspace, "media")
    os.makedirs(media_dir, exist_ok=True)
    staged = os.path.join(media_dir, staging_name_for(media_path))
    try:
        shutil.copy2(media_path, staged)
    except OSError as exc:
        raise _unavailable(f"could not stage the working media: {exc}") from None
    before = os.path.getsize(media_path)
    after = os.path.getsize(staged)
    if after != before:
        raise _unavailable("staged media is not byte-identical to the working asset")
    return {"source_path": media_path, "staged_path": staged,
            "bytes": after, "workspace": workspace}


# ---------------------------------------------------------------------------
# Draft naming
# ---------------------------------------------------------------------------
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_draft_name(raw: str, *, store: str) -> str:
    """A plain, unique folder name for the draft inside the CapCut store.

    ``compile`` refuses a path-shaped ``spec.name`` outright (it becomes a
    directory inside the store), so a candidate title containing ``/`` would
    otherwise abort the handoff. Sanitising here keeps that a non-event, and the
    name is made unique so an existing project is never overwritten.
    """
    base = _UNSAFE_NAME.sub(" ", str(raw or "")).strip(" .-_") or "handoff"
    base = re.sub(r"\s+", " ", base)[:60]
    name = f"{DRAFT_NAME_PREFIX}{base}"
    if not os.path.isdir(store):
        return name
    taken = set(os.listdir(store))
    if name not in taken:
        return name
    for suffix in range(2, 100):
        candidate = f"{name} {suffix}"
        if candidate not in taken:
            return candidate
    raise _unavailable("could not find a free draft name in the CapCut store")


def draft_name_for(asset_version: Any, *, store: str) -> str:
    """A recognisable, version-truthful default draft name."""
    version = str(asset_version or "current")
    stamp = time.strftime("%Y%m%d-%H%M")
    return safe_draft_name(f"{version} {stamp}", store=store)


# ---------------------------------------------------------------------------
# The handoff
# ---------------------------------------------------------------------------
def _default_invoke(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              text=True, cwd=os.path.dirname(cmd[0]) or None)
    except subprocess.TimeoutExpired:
        raise _unavailable(f"capcut-cli timed out after {timeout}s") from None
    except OSError as exc:
        raise _unavailable(f"could not run capcut-cli: {exc}") from None
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_handoff(*, bootstrap: dict[str, Any], media_path: str,
                acquisition: dict[str, Any] | None = None,
                original_window: dict[str, Any] | None = None,
                caption_blocks: Any = None,
                asset_version: str = "",
                draft_name: str = "",
                aspect: str = ch.DEFAULT_ASPECT, fps: int = ch.DEFAULT_FPS,
                hook: str = "", title: str = "",
                cli_path: str = "", draft_store: str = "",
                template: str = "auto",
                handoff_root: str = "", reveal: bool = False,
                force_write: bool = False,
                timeout: int = COMPILE_TIMEOUT_SECONDS,
                invoke: Callable[[list[str], int], tuple[int, str, str]] | None = None,
                ) -> dict[str, Any]:
    """Build the editable CapCut project for the CURRENT version.

    Returns a report on success. Raises ``CapCutHandoffUnavailable`` on any
    failure — including an absent CLI — having changed NO LOOM state.
    """
    invocation = capcut_invocation(cli_path)
    if not invocation:
        raise _unavailable(
            "the CapCut lane is unavailable: no vendored capcut-cli and none on "
            "PATH (set LOOM_CAPCUT_CLI to enable 'Finish in CapCut')")
    store = draft_store or capcut_draft_store()
    if not store or not os.path.isdir(store):
        raise _unavailable(
            "no CapCut draft store found on this machine; the project would not "
            "be recognised by the installed app")

    coords = working_coordinates_for(bootstrap, acquisition, original_window)

    # Pre-flight the app-state guard BEFORE anything is staged or written. The
    # CLI refuses to write a MANAGED draft while the editor is open (so the app
    # cannot overwrite the change); catching it here means a refused handoff
    # leaves no half-built project in the operator's CapCut at all. The check is
    # scoped to managed stores exactly as the CLI scopes it, so writing to any
    # other directory is not blocked by the operator having the app open.
    if not force_write and is_managed_draft_store(store):
        running = capcut_editor_running()
        if running:
            raise _unavailable(
                f"{' / '.join(running)} is running. Close CapCut, then try "
                "'Finish in CapCut' again — writing while the app is open can be "
                "overwritten by it")

    root = os.path.expanduser(handoff_root or DEFAULT_HANDOFF_ROOT)
    workspace = os.path.join(root, f"handoff-{time.strftime('%Y%m%d-%H%M%S')}-"
                                   f"{os.getpid()}")
    os.makedirs(workspace, exist_ok=True)
    staged = stage_working_media(media_path, workspace=workspace)

    # The mapper is handed WORKING coordinates, so nothing downstream can see an
    # ORIGINAL timestamp.
    working_bootstrap = {**bootstrap,
                         "source_range": {"start": coords["start"],
                                          "end": coords["end"]}}
    spec = ch.build_capcut_spec(working_bootstrap, media_path=staged["staged_path"],
                               aspect=aspect, fps=fps, hook=hook, title=title)
    spec["name"] = safe_draft_name(spec.get("name") or "", store=store)

    caption_state: dict[str, Any] = {"included": False,
                                     "reason": "captions unavailable for this version"}
    blocks = caption_blocks if caption_blocks is not None else (
        bootstrap.get("captions") or bootstrap.get("transcript") or [])
    cues = ch.normalize_caption_blocks(blocks)
    if cues:
        srt_path = ch.write_srt(cues, os.path.join(workspace, "captions.srt"))
        operation = ch.build_caption_operation(blocks, srt_path)
        if operation:
            spec["operations"] = [operation]
            caption_state = {"included": True, "count": len(cues),
                             "srt": srt_path, "track": ch.CAPTION_TRACK_NAME}
        else:
            caption_state = {"included": False,
                             "reason": "captions unavailable for this version"}

    spec_path = ch.write_spec(spec, os.path.join(workspace, "spec.json"))

    target = os.path.join(store, draft_name or draft_name_for(asset_version, store=store))
    if os.path.exists(target):
        target = os.path.join(store, safe_draft_name(os.path.basename(target), store=store))

    cmd = list(invocation) + ["compile", spec_path, "--out", target]
    if force_write:
        cmd += ["--force-write"]
    # Template choice is MEASURED, not cosmetic. The vendored `_init` template
    # declares CapCut 6.5.0, while the operator's real store holds 7.0.0
    # projects — and the CLI correctly REFUSES that mismatch (donor issues
    # #67/#111: CapCut lists the new draft with a 00:00 duration and then will
    # not open it). `auto` seeds the draft from the store's newest project, so
    # the generated draft carries the same schema markers as the app that will
    # open it. An explicit directory is still honoured when a caller passes one.
    if template:
        cmd += ["--template", os.path.abspath(template)
                if os.path.isdir(template) else template]
    cmd += ["--drafts", store]

    runner = invoke or _default_invoke
    created_target = not os.path.exists(target)
    try:
        code, out, err = runner(cmd, timeout)
        if code != 0:
            raise _unavailable(
                f"capcut-cli compile failed (exit {code}): "
                f"{(err or out or '').strip()[:400] or 'no output'}")

        try:
            result = json.loads((out or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise _unavailable(
                "capcut-cli returned output that is not the expected JSON report: "
                f"{(out or '').strip()[:200]!r}") from None
        if not isinstance(result, dict) or not result.get("ok"):
            raise _unavailable(
                f"capcut-cli reported a failed compile: {str(result)[:300]}")

        # Trust the draft on disk, not the CLI's own claim about it.
        produced = result.get("file_path") or os.path.join(target, "draft_content.json")
        if not os.path.isfile(produced):
            raise _unavailable(
                f"capcut-cli reported success but produced no draft at {produced!r}")
    except BaseException:
        # A refused or failed compile can leave a half-built project (and an
        # index entry) behind. Undo only what this run created, then re-raise:
        # LOOM state itself is untouched either way.
        if created_target:
            _discard_created_draft(target, store)
        raise

    # Success: the draft carries its own byte-identical copy of the media, so the
    # scratch copy has done its job. Keep the small spec/SRT/report for audit.
    try:
        os.remove(staged["staged_path"])
    except OSError:
        pass

    report = {
        "ok": True,
        "lane": "capcut",
        "asset_version": asset_version or bootstrap.get("asset_version") or "",
        "version_mode": bootstrap.get("version_mode") or "",
        "draft_name": os.path.basename(target),
        "draft_path": target,
        "draft_file": produced,
        "draft_store": store,
        "reveal_path": os.path.dirname(target),
        "workspace": workspace,
        "spec_path": spec_path,
        "coordinates": {
            "original": coords.get("original"),
            "working": {"start": coords["start"], "end": coords["end"],
                        "duration": coords["duration"]},
            "capcut": {"sourceStart": coords["start"], "duration": coords["duration"]},
        },
        "media": {"source_path": staged["source_path"],
                  "staged_path": staged["staged_path"], "bytes": staged["bytes"],
                  "draft_copy": "copied into the draft by the CLI (self-contained)"},
        "captions": caption_state,
        "carried": ch.handoff_report(working_bootstrap)["carried"] + (
            [f"{caption_state['count']} caption block(s)"] if caption_state["included"] else []),
        "unsupported": ch.handoff_report(working_bootstrap)["unsupported"],
        "manual_open_required": True,
        "cli": invocation,
        "template": template,
    }
    with open(os.path.join(workspace, "handoff-report.json"), "w",
              encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    if reveal:
        reveal_draft(target)
    return report


def reveal_draft(path: str) -> bool:
    """Show the project in Finder (macOS only). Never opens the project itself.

    Deliberately explicit and best-effort: there is no supported way to make
    CapCut open a project, and inventing one is out of scope, so the operator's
    single manual action is "open CapCut and pick this project".
    """
    if os.uname().sysname != "Darwin":
        return False
    try:
        subprocess.run(["open", "-R", path], capture_output=True, timeout=15)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
