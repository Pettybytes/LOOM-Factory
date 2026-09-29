# Vendored donor: capcut-cli (LOOM "Finish in CapCut" lane)

This directory is a **vendored, pinned** copy of a third-party tool. LOOM does not
track the donor's `HEAD`, and the donor is **never required** for normal LOOM
operation — it is an *optional* finishing lane only.

## Provenance

| Field | Value |
|---|---|
| Upstream URL | https://github.com/renezander030/capcut-cli |
| Pinned commit | `a21cc9ce65ef169920f0e18cb130777a13bee838` |
| Upstream version | `0.26.0` |
| License | **MIT** — `Copyright (c) 2026 Rene Zander` (see `LICENSE`, kept verbatim) |
| Vendored on | 2026-09-28 |
| Audited in | `~/Desktop/LOOM-EDITOR-REUSE-AUDIT.md` §2G / §10 |

Attribution: the CapCut/JianYing draft format knowledge and the `compile` spec
contract are the work of Rene Zander (`capcut-cli`, MIT). The vendored tree is
unmodified upstream source plus its own build output.

## What is vendored, and why

| Path | Why it is here |
|---|---|
| `dist/` | The **runtime**. The `compile` path executes from here. Self-contained JavaScript; no npm packages needed at runtime. |
| `dist/enums.json` | Read at runtime relative to the compiled module (`src/enums.ts` resolves `enums.json` next to itself). Copied from `src/enums.json` by upstream's own build step. |
| `templates/_init` | The bundled draft template `compile` seeds a new project from (`--template`, default resolution). Without it `compile` refuses: *"No draft_info.json or draft_content.json found in template"*. |
| `package.json` | Runtime-required: the CLI reads its own version from it (`--version` fails `ENOENT` without it). |
| `src/` | The pinned source of truth that `dist/` was compiled from. Kept so the vendored artifact stays **auditable and rebuildable** rather than an opaque blob — `dist` is a build artifact of the pinned SHA and would otherwise be unexplainable. |
| `tsconfig.json`, `package-lock.json` | Build reproducibility (exact toolchain pin). |
| `LICENSE` | MIT notice retention — required by the licence. |

Deliberately **not** vendored: `node_modules` (the runtime needs none — proven by
running `compile` from a directory containing only `dist/` + `templates/`), the
donor's `test/`, `docs/`, `examples/`, `wasm/`, `media/`, `hooks/`, `python/`,
and the donor's other command surfaces.

## Build recipe (how `dist/` was produced)

Built **outside** this repo from the pinned checkout, with a **local** (never
global) toolchain — `typescript@7.0.2` exactly as pinned in `package-lock.json`:

```bash
git clone https://github.com/renezander030/capcut-cli && cd capcut-cli
git checkout a21cc9ce65ef169920f0e18cb130777a13bee838
npm ci --ignore-scripts          # devDeps only; no global install
npx tsc                          # -> dist/
node -e "import('node:fs').then(fs=>fs.writeFileSync('dist/enums.json',JSON.stringify(JSON.parse(fs.readFileSync('src/enums.json','utf-8')))))"
```

`dist/` was then copied here. Re-running the recipe against the same SHA
reproduces the same JavaScript.

## Runtime requirements

* **Node ≥ 18** (upstream `engines`). Verified on the local `node` v22.22.3.
* **ffprobe** on `PATH` — `compile` probes media for dimensions/duration
  (present at `/opt/homebrew/bin/ffprobe` on this host).
* **No npm packages.** Verified by running the compile path from a directory
  containing only `dist/`, `templates/`, `package.json` and `LICENSE`.

Note: this vendored tree is **plain compiled JavaScript**, so it does *not*
depend on Node's TypeScript type-stripping. Running the donor's `src/*.ts`
directly was measured to fail (`ERR_MODULE_NOT_FOUND` on `./app-versions.js`,
because upstream's source uses `.js` specifiers intended for `tsc` output).

## Licence obligations

MIT permits commercial reuse. The `LICENSE` file is retained in this directory
and the upstream copyright notice is preserved verbatim. No donor code is copied
into LOOM's own modules; `infra/discord-review/capcut_handoff.py` reuses only the
**spec format** and documents that attribution in its own docstring.
