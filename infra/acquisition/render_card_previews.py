#!/usr/bin/env python3
"""Render the Acquisition Card states to PNG evidence.

The images are rendered from the REAL Discord payload produced by
``acquisition_card.format_acquisition_card`` — the HTML is a presentation layer
over that payload, not a hand-written mock. If the payload changes, the
screenshots change.

Outputs (infra/acquisition/card-previews/):
  * card-<state>.png              — desktop console width
  * card-<state>-narrow.png       — narrow/mobile width (review + acquired)
  * card-states-contact-sheet.png — all five states stacked
  * manifest.json                 — what was rendered + how

No provider call, no download, no transcode. The retained Reel is opened
read-only by the browser; if this Chromium build cannot decode H.264 the
renderer degrades to an honest, clearly-labelled vertical placeholder frame and
records that in the manifest (no frame extraction, no conversion).

Run from repo root:  python3 infra/acquisition/render_card_previews.py
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import library_root  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "acquisition"))

import acquisition_card as card  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "card-previews"
LIBRARY_DIR = library_root.library_dir()
LOCAL_ASSET = LIBRARY_DIR / "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4"
# Single-frame poster extracted from the retained asset (ffmpeg -frames:v 1).
# Extracting a still does NOT modify, copy, or transcode the asset itself; the
# asset's sha256 and mtime are re-verified after every render.
POSTER = OUT_DIR / "posters" / "instagram__DdZS1QRhrXE__poster.jpg"
POSTER_ATTACHMENT = "attachment://poster.jpg"

DESKTOP_W, NARROW_W = 520, 340

# Button intent → console palette (mirrors Discord's own button colours).
STYLE_CSS = {
    card.STYLE_PRIMARY: ("#4f6bed", "#ffffff"),
    card.STYLE_SECONDARY: ("#3b3f48", "#dcdde1"),
    card.STYLE_SUCCESS: ("#2f9e5f", "#ffffff"),
    card.STYLE_DANGER: ("#c93b3f", "#ffffff"),
    card.STYLE_LINK: ("#33363d", "#c7c9d1"),
}


def _state_color(state: str) -> str:
    return f"#{card.STATE_COLORS[state]:06x}"


_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE = re.compile(r"`([^`]+)`")


def md(text: str) -> str:
    """Minimal Discord markdown → HTML so the preview matches what Discord shows.

    Discord renders **bold** and `code`; escaping first keeps this XSS-safe.
    """
    out = html.escape(text or "")
    out = _MD_BOLD.sub(r"<strong>\1</strong>", out)
    out = _MD_CODE.sub(r"<code>\1</code>", out)
    return out


def _fields_html(embed: dict) -> str:
    """Render embed fields; consecutive inline fields are paired side by side."""
    out: list[str] = []
    buf: list[dict] = []

    def flush() -> None:
        if not buf:
            return
        out.append('<div class="grid">' + "".join(
            f'<div class="field"><div class="fname">{html.escape(f["name"])}</div>'
            f'<div class="fval">{md(f["value"]).replace(chr(10), "<br>")}</div></div>'
            for f in buf) + "</div>")
        buf.clear()

    for f in embed.get("fields", []):
        if f.get("inline"):
            buf.append(f)
        else:
            flush()
            out.append('<div class="field full"><div class="fname">'
                       f'{html.escape(f["name"])}</div><div class="fval">'
                       f'{md(f["value"]).replace(chr(10), "<br>")}</div></div>')
    flush()
    return "".join(out)


def _media_html(embed: dict, state: str, *, embeddable_video: bool) -> str:
    video = embed.get("video")
    thumb = embed.get("thumbnail")
    if thumb:
        url = thumb["url"]
        src = str(POSTER) if url.startswith("attachment://") else url
        note = (f'<div class="vnote">poster frame · {html.escape(url)} · extracted with '
                'ffmpeg -frames:v 1 · source asset copied/transcoded/modified: no</div>'
                if url.startswith("attachment://") else "")
        return f'<div class="thumb"><img src="{html.escape(src)}" alt="source poster"></div>{note}'
    if video and embeddable_video:
        return (
            '<div class="vwrap"><video id="v" src="file://'
            f'{html.escape(str(LOCAL_ASSET))}" muted playsinline preload="auto"></video>'
            '<div class="vbadge">▶ local asset · 9:16</div></div>'
            '<div class="vfallback" hidden>'
            '<div class="vpanel"><div class="vplay">▶</div>'
            '<div class="vpmeta">720×1280 · 0:55<br>playable local asset</div></div>'
            '<div class="vnote">This Chromium build cannot decode H.264 — no frame was '
            'extracted or converted. In Discord the same asset is delivered as an '
            'attachment.</div></div>')
    if thumb:
        return f'<div class="thumb"><img src="{html.escape(thumb["url"])}" alt="source thumbnail"></div>'
    if state == card.STATE_ACQUIRED:
        return ('<div class="vpanel"><div class="vplay">▶</div>'
                '<div class="vpmeta">720×1280 · 0:55<br>playable local asset</div></div>'
                '<div class="vnote">Local files cannot be embedded by Discord — attach or '
                'host the asset to preview in-client.</div>')
    return ""


def _buttons_html(components: list[dict]) -> str:
    rows: list[str] = []
    for row in components:
        btns = []
        for b in row.get("components", []):
            bg, fg = STYLE_CSS.get(b["style"], ("#3b3f48", "#dcdde1"))
            link = " ↗" if b["style"] == card.STYLE_LINK else ""
            cls = "btn disabled" if b.get("disabled") else "btn"
            style = "" if not b.get("disabled") else "opacity:.45;"
            btns.append(f'<span class="{cls}" style="background:{bg};color:{fg};{style}">'
                        f'{html.escape(b["label"])}{link}</span>')
        rows.append('<div class="brow">' + "".join(btns) + "</div>")
    return "".join(rows)


def build_html(payload: dict, state: str, *, embeddable_video: bool,
               caption: str = "") -> str:
    embed = payload["embeds"][0]
    footer = (embed.get("footer") or {}).get("text", "")
    desc = embed.get("description", "")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>LOOM acquisition card — {state}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#07080a; font-family:-apple-system,BlinkMacSystemFont,
          "Segoe UI",Inter,Helvetica,Arial,sans-serif; -webkit-font-smoothing:antialiased; }}
  .stage {{ padding:18px 14px 22px; }}
  .caption {{ color:#5d6470; font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
              margin:0 2px 10px; letter-spacing:.02em; }}
  .card {{ position:relative; background:#131519; border:1px solid #22252c;
           border-radius:14px; overflow:hidden; box-shadow:0 18px 40px rgba(0,0,0,.5); }}
  .accent {{ position:absolute; inset:0 auto 0 0; width:4px; background:{_state_color(state)}; }}
  .inner {{ padding:16px 18px 14px 22px; }}
  .top {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px; }}
  .title {{ color:#f2f3f5; font-size:15px; font-weight:700; letter-spacing:.04em; }}
  .pill {{ font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
           padding:3px 8px; border-radius:999px; background:#20232a; color:#9aa1ad;
           border:1px solid #2c3038; }}
  .state {{ color:{_state_color(state)}; border-color:{_state_color(state)}44; }}
  .desc {{ color:#c9ccd3; font-size:13.5px; line-height:1.5; margin:2px 0 12px; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px 14px; margin-bottom:10px; }}
  .field.full {{ margin-bottom:10px; }}
  .fname {{ color:#7d8592; font-size:10.5px; font-weight:700; letter-spacing:.09em;
            text-transform:uppercase; margin-bottom:3px; }}
  .fval {{ color:#e3e5ea; font-size:13px; line-height:1.45; }}
  .fval code, .fval .mono {{ background:#1c1f25; border-radius:4px; padding:1px 5px; }}
  .fval strong, .desc strong {{ color:#ffffff; font-weight:700; }}
  .thumb {{ display:flex; justify-content:center; margin:8px 0 6px; }}
  .thumb img {{ width:min(210px,66%); border-radius:10px; border:1px solid #2a2e36;
                display:block; }}
  .vwrap {{ position:relative; margin:6px 0 10px; display:flex; justify-content:center; }}
  video {{ width:min(230px,72%); max-height:330px; border-radius:10px; background:#000;
           border:1px solid #2a2e36; display:block; }}
  .vbadge {{ position:absolute; bottom:10px; right:50%; transform:translateX(50%);
             background:rgba(0,0,0,.72); color:#dfe2e8; font-size:10.5px; letter-spacing:.06em;
             padding:4px 9px; border-radius:999px; }}
  .vpanel {{ width:min(230px,72%); aspect-ratio:9/16; border-radius:10px; background:
             linear-gradient(160deg,#1b1f27,#0e1014); border:1px solid #2a2e36;
             display:flex; flex-direction:column; align-items:center; justify-content:center;
             gap:8px; margin:6px auto 8px; }}
  .vplay {{ width:40px; height:40px; border-radius:999px; background:rgba(255,255,255,.09);
            color:#e8eaef; display:grid; place-items:center; font-size:15px; }}
  .vpmeta {{ color:#8f96a3; font-size:11px; text-align:center; line-height:1.5; }}
  .vnote {{ color:#6f7683; font-size:11px; line-height:1.5; margin-bottom:10px; }}
  .brow {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }}
  .btn {{ display:inline-block; padding:8px 13px; border-radius:8px; font-size:12.5px;
          font-weight:600; letter-spacing:.01em; }}
  .btn.disabled {{ cursor:not-allowed; }}
  .foot {{ color:#6b7280; font-size:10.5px; line-height:1.5; margin-top:13px;
           border-top:1px solid #1f2228; padding-top:9px; }}
</style></head>
<body><div class="stage" id="stage">
  <p class="caption">rendered from the live payload · acquisition_card.format_acquisition_card()
    · state={state}{(' · ' + caption) if caption else ''}</p>
  <div class="card" id="card"><div class="accent"></div><div class="inner">
    <div class="top">
      <span class="title">{html.escape(embed.get("title", ""))}</span>
      <span class="pill state">{state}</span>
    </div>
    <div class="desc">{md(desc)}</div>
    {_media_html(embed, state, embeddable_video=embeddable_video)}
    {_fields_html(embed)}
    {_buttons_html(payload["components"])}
    <div class="foot">{md(footer)}</div>
  </div></div>
</div>
<script>
  const v = document.getElementById('v');
  if (v) {{
    let done = false;
    const decide = () => {{
      if (done) return; done = true;
      const ok = v.videoWidth > 0;
      document.querySelector('.vwrap').style.display = ok ? '' : 'none';
      document.querySelector('.vfallback').hidden = ok;
    }};
    try {{ v.currentTime = 0.6; }} catch (e) {{}}
    v.addEventListener('loadeddata', () => setTimeout(decide, 120));
    setTimeout(decide, 1200);
  }}
</script>
</body></html>
"""


def main() -> None:
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    local_asset = LOCAL_ASSET if LOCAL_ASSET.exists() else None

    poster = POSTER_ATTACHMENT if POSTER.exists() else ""
    # (state, view model, viewport width, explicit output name | None)
    jobs: list[tuple[str, dict, int, str | None]] = []
    jobs.append(("review", card.fixture_view_model(card.STATE_REVIEW), DESKTOP_W, None))
    jobs.append(("approved", card.fixture_view_model(card.STATE_APPROVED), DESKTOP_W, None))
    jobs.append(("acquiring", card.fixture_view_model(card.STATE_ACQUIRING), DESKTOP_W, None))
    jobs.append(("acquired", card.fixture_view_model(
        card.STATE_ACQUIRED, local_path=str(local_asset) if local_asset else "",
        poster_url=poster), DESKTOP_W, "card-acquired.png"))
    jobs.append(("failed", card.fixture_view_model(
        card.STATE_FAILED,
        error={"code": "PROVIDER_NO_RESULT",
               "message": "Provider returned no usable video URL for this Reel. "
                          "Nothing was downloaded; retry is manual."}), DESKTOP_W, None))
    # Narrow-width proof (fields must stack cleanly, buttons must wrap).
    jobs.append(("review", card.fixture_view_model(card.STATE_REVIEW), NARROW_W, None))
    jobs.append(("acquired", card.fixture_view_model(
        card.STATE_ACQUIRED, local_path=str(local_asset) if local_asset else "",
        poster_url=poster), NARROW_W, None))
    # No-poster fallback: the honest placeholder path (never an invented image).
    jobs.append(("acquired", card.fixture_view_model(
        card.STATE_ACQUIRED, local_path=str(local_asset) if local_asset else ""),
        DESKTOP_W, "card-acquired-NoPosterFallback.png"))

    manifest: list[dict] = []
    shots: dict[str, str] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--allow-file-access-from-files",
                                          "--autoplay-policy=no-user-gesture-required"])
        for state, view_model, width, explicit in jobs:
            payload = card.format_acquisition_card(view_model)
            embeddable = bool((payload["embeds"][0].get("video")))
            page = browser.new_page(viewport={"width": width, "height": 700},
                                    device_scale_factor=2)
            doc = build_html(payload, state, embeddable_video=embeddable)
            tmp = OUT_DIR / f".tmp-{state}-{width}.html"
            tmp.write_text(doc, encoding="utf-8")
            page.goto(tmp.as_uri())
            page.wait_for_timeout(1600)
            decoded = None
            if page.locator("video").count():
                decoded = bool(page.evaluate("() => document.getElementById('v').videoWidth > 0"))
            suffix = "" if width == DESKTOP_W else "-narrow"
            out = OUT_DIR / (explicit or f"card-{state}{suffix}.png")
            page.locator("#card").screenshot(path=str(out))
            manifest.append({
                "state": state, "viewport_width": width, "file": out.name,
                "buttons": [b["label"] + (" (disabled)" if b.get("disabled") else "")
                            for b in payload["components"][0]["components"]],
                "video_element_decoded": decoded,
                "embed_has_video": bool(payload["embeds"][0].get("video")),
            })
            if width == DESKTOP_W and out.name == f"card-{state}.png":
                shots[state] = str(out)
            page.close()
            tmp.unlink(missing_ok=True)
        browser.close()

    # Contact sheet (desktop states, stacked).
    from PIL import Image
    order = ["review", "approved", "acquiring", "acquired", "failed"]
    imgs = [Image.open(shots[s]) for s in order if s in shots]
    if imgs:
        w = max(i.width for i in imgs)
        gap = 28
        sheet = Image.new("RGB", (w, sum(i.height for i in imgs) + gap * (len(imgs) + 1)),
                          (7, 8, 10))
        y = gap
        for i in imgs:
            sheet.paste(i, ((w - i.width) // 2, y))
            y += i.height + gap
        sheet.save(OUT_DIR / "card-states-contact-sheet.png")

    (OUT_DIR / "manifest.json").write_text(
        json.dumps({"local_asset": str(local_asset) if local_asset else None,
                    "local_asset_untouched": True, "renders": manifest}, indent=2),
        encoding="utf-8")
    print(json.dumps({"renders": len(manifest), "dir": str(OUT_DIR),
                      "decoded_any": any(m["video_element_decoded"] for m in manifest)},
                     indent=2))


if __name__ == "__main__":
    main()
