# OpenShorts → CapCut coverage — 2026-11-21

Status: hook visually accepted on `LOOM render-1.5 20261121-2022`; full editor parity is NOT accepted. `mapped+tested` means automated serialization/compiler tests passed; it does not mean CapCut visual fidelity is proven. Claude verified native Noto Serif, black bold appearance, native two-line wrapping without an inserted newline, a rounded white card about 72% wide centred about 20% down, and 0–5s timing. Other text roles and styles still require external visual checks. No known property below is declared impossible in CapCut; `missing` describes this integration.

Canonical inventory: local `dashboard/src/remotion/lib/types.ts` (ShortVideoProps and zod schemas), `ManualTrimEditor.jsx` autosave/completion bodies, `lib/richText.js`, `lib/timelineState.js`, `ClipEditor.jsx`, `hooks.py`, `subtitles.py`, and accepted render-1.5 bootstrap/EDL. Upstream information supplied by Claude is noted separately; no upstream code was backported.

| Property | OpenShorts saved location | CapCut target | Status |
|---|---|---|---|
| Video URL/bytes | ShortVideoProps.videoUrl; auto_edit.output_url; clean_source_hash | video material path; byte-identical copied media | mapped+tested; accepted render uses clean pre-hook video |
| Canvas width/height/fps/duration | ShortVideoProps; probed accepted output; persisted visual state | canvas_config, fps, duration, target_timerange | mapped+tested |
| Trim in/out | draft.trim_start/end; timeline.range; correction source_range | source_timerange/target_timerange | mapped+tested; generated render imports its already trimmed 0–26s |
| Cuts, ordering | ClipEditor EDL.segments; correction edl_metadata.segments | ordered video segments | accepted generated pixels retained; independent reconstruction missing for generated output |
| AUTO/FULL/TRACK framing | draft.framing, crop_reframe_changes; persisted visual state | clip.scale/transform and crop keyframes, background blur composition | existing mapping+tests; AUTO render identity tested this run; no new alternate-layout visual claim |
| Split/screencast and dedicated header region | persisted composition / title_overlay.region | multi-track composition | split/screencast source composition remains missing/refused; dedicated Header band now maps over clean already-composed pixels |
| Zoom/center position | EffectSegment.zoom/zoomCenterX/zoomCenterY; auto_edit.edit_plan | transform/scale keyframes | accepted render effects preserved as pixels; independent generated-effect controls missing |
| Brightness/contrast/saturate | EffectSegment; auto_edit.edit_plan/filter_string | adjustment materials/keyframes | native mapping missing; any already baked result retained |
| Flash/punch-in | auto_edit.edit_plan | effect materials/keyframes | native mapping missing; accepted flash/punch-in retained in video |
| Speed/ordering beyond trim | no editable speed field in local ManualTrimEditor or ShortVideoProps; typed future events | speed material and ordered segments | missing; not a currently saved ManualTrimEditor property |
| SFX/transitions/media overlays/commentary | timelineState declared event registry | corresponding audio/video/effect tracks | missing; current editor explicitly refuses those unimplemented event types |
| Natural-language instruction | draft.instruction; correction.instruction | applied generated output | pixels carried; no native reconstruction of arbitrary instruction |
| Clip title (metadata) | draft.title; generated_copy.title | project name | mapped+tested; not an on-video title |
| Social caption (metadata) | draft.social_caption; completion caption | handoff metadata, not timeline text | intentionally not rendered |
| Hook text/duration | auto_edit.generated_copy.hook; hook_settings.duration_seconds; HookConfig.text/displayDurationSec | content.text + target_timerange | mapped+tested; five seconds on target artifact |
| Hook preset/color/card color/opacity | hook_settings.style; hooks.py HOOK_STYLES; HookConfig.style | CLI native template + text-style preset: text_color, background_style, background_color/alpha | mapped+tested: classic and yellow; no-card outline_yellow tested |
| Hook size/position/wrap | hook_settings.size/position; Activity TextOverlayPreview + overlayLayout | CLI preset font_size, transform, line_max_width + force_apply_line_max_width | Activity geometry mapped+tested across S/M/L × top/center/bottom × region; native units/visual box unverified; no inserted newlines |
| Hook font and weight | implicit NotoSerif-Bold; custom mapper font_family/weight | CLI preset font_path and bold → material + content styles | default and alternate fonts tested; weight field serialized, visual weight untested; local HookConfig has no HookFont/weight picker |
| Hook entrance | draft.hook_entrance; HookConfig.entranceAnimation | text animation material | missing |
| Hook/Header rich base and spans | draft.hook_rich/header_rich; correction.edl_metadata equivalents; base color/font and spans start/end/color/font/size multiplier | CLI text-ranges/preset text_ranges (available, not wired) | missing: version bootstrap omits them, CLI base-style preset currently supplies one run |
| On-video title enabled/text | title_overlay.enabled/text; correction.edl_metadata.title_overlay; TitleOverlayConfig | separate text material + segment | mapped+tested through both durable version loaders and authenticated Finish endpoint; saved session Header overrides completed-version Header, including disable/removal; live service reload is Bentley-owned |
| Title font/size/color/background/alignment/position | title_overlay snake_case fields; TitleOverlayConfig camelCase schema | shared text mapper fields | mapped+tested for supplied saved snake_case; direct Remotion-props ingestion missing; visual calibration pending |
| Title region/timing | title_overlay.region; default full clip duration | text timerange and dedicated band geometry | full-duration native text and dedicated band mapped+tested for clean already-composed video via CLI crop + position; visual validation pending; layered/unresolved compositions refused; no saved title timing control |
| Captions text/times | auto_edit.captions; CaptionWord.text/startMs/endMs; ASR start/end seconds | CLI import-ass creates text materials + target_timerange | mapped+tested for both input unit conventions; ASS has centisecond precision; render-1.5 has zero cues, no caption visual proof |
| Caption font/color/uppercase/background/base opacity | auto_edit.caption_settings; draft.caption_*; SubtitleStyle | OpenShorts generate_ass → CLI import-ass --clone-style with explicit role seed; font/fill/background fields | mapped+tested for supplied settings and karaoke dimmed colors; caption background capability flags are not cloned by upstream, visual behavior unverified; latest edited caption persistence incomplete in this fork |
| Caption size/position/border | caption_settings.font_size/alignment/border_*; SubtitleStyle/SubtitlePosition | CLI seed font_size/border_*; import-ass --x/--y | mapped untested visually; source ASS PlayResY conversion used |
| Caption highlight/grouping/animation/effect | caption_settings.highlight_color/max_chars/max_duration/style/effect; SubtitleStyle.highlightColor/animation | ASS inline color spans and timed events; cloned font/background | grouping and word highlight mapped+tested; glow/pop/box animation and inline split-seam positioning missing because CLI import drops those tags |
| Background on/off/color/opacity, alignment | saved title/caption fields and hook palette | shared mapper CLI preset background_*, alignment | fields mapped+tested independently per text role; new caption-card visual fidelity unverified |
| Corner radius / explicit wrap width / explicit weight | renderer defaults; not independent controls in local zod schemas; mapper accepts corner_radius/wrap_width/weight | CLI preset background_round_radius/line_max_width/bold | supplied-value tests; fixed default card padding/radius native-unit conversion still provisional; no current editor persistence for standalone radius/width/weight controls |
| Free positioning/resizing of hook, title, captions | not available in the editor today; no saved x/y/width | no mapping possible from absent saved fields | unavailable today; proposed editor feature: preview drag/resize saved per clip as x/y/width, then mapped; requires Bryan’s separate decision |
| Autosave/auto-start guard/source duration | draft.auto_start/source_duration_seconds/timeline.version | provenance only | not visual properties; never rerun Auto Edit during handoff |
| Upstream per-clip captions | Claude reports f300193 caption_style/caption_words persistence | future source for resolved caption mapper | missing in fork; not backported |
| Upstream pill/fonts/auto placement | Claude reports newer HookStyle/HookFont/hook_placement.py | style catalog + resolved coordinates/font path | missing in fork; JSON style/font catalog extension point added, no guessed pill preset |

Native reference `1121 (1)` has no draft_content.json. Its root draft_info.json and main Timelines draft_info.json agree on background_style=1, flag=23, a font path inside both material and style run, and a real newline. Its 16:9 geometry and font size were NOT copied. The native text has no separate populated font-resource list. The live references are read-only inputs.

This candidate does NOT justify “everything transfers exactly.” Required next integration work is visible above; no manual CapCut styling is intended as a production workflow. The current external check evaluates the generated hook, not a promise of full parity.

## CLI ownership and remaining custom code

Vendored `capcut-cli v0.35.0`, commit `6e7ef130aa7aff191a1a32a68fe2ff727fec4864`;
exact source/build/lock checksums are in `../../vendor/capcut-cli/PROVENANCE.md`.

- CLI owns project `compile --template auto`, native text template instantiation,
  `text-style --preset`, `import-ass --clone-style --style-ref`, caption seed
  removal, `add-cover`, `register --apply`, and `sync-timelines --nested --apply`.
- Deleted the direct `conform_hook_text` draft rewrite, direct content/styles
  construction, check-flag bit manipulation, direct segment transform/scale
  writes, and custom duration/index-registration writes. Removed obsolete
  fontSize=14/y=0.65 mapping. No custom mirror copying remains.
- LOOM retains per-clip settings→CLI preset/argument conversion, Activity pixel
  geometry, font-file resolution, media/source-coordinate checks, generation
  of the cover image through FFmpeg, and removal of archived/missing LOOM-only
  store cards (no CLI equivalent). The neutral native template carries the
  app-created text structure; no manual CapCut styling is required per clip.
- The CLI accepts the background/padding/radius/wrap fields, but does not provide
  a proven CSS-pixels→native-text-layout conversion. Font-unit calibration,
  background expansion and roundness remain subject to the app visual gate.
- ASS import supports inline bold/italic/underline/color/font-size, but omits
  animated transforms, blur/border overrides and inline anchor/position tags.
  Import also does not preserve ASS font-family/background directly; the seed
  supplies these. Its style clone excludes the native check_flag, so background
  fields alone do not establish a visibly working caption card.

## Exact render-1.5 geometry

Source: Oct 3 `final-two/bootstrap.json`, `auto_edit.hook_settings` =
`{size:"M", style:"classic", position:"top", duration_seconds:5}`.
Its saved correction has no enabled title overlay/dedicated Header region.
The Activity component measures a card at x=70.27, y=164.89, width=435.45,
height=79.81 on 576×1024. Top edge **16.103%**, centre **20.000%**, maximum width
**75.600%**, font **24.192px**. Native target transform y=0.6. No old screenshot
or global 28% target is used. Native wrap is requested; the text contains no
inserted newline. This is the requested box, not a claim of CapCut pixel proof.

The browser fixture covers 18 hook preset/region combinations and four Header
cases. Geometry comparisons allow 2px (hook) / 3px (Header). The current
`hooks.py` burn places the hook raster by its top edge and adds transparent
padding; the Activity preview centres the card on its preset anchor. They do
not agree. This lane follows the Activity preview as Bryan requested. The
fixture hashes the exact preview sources so future editor changes fail tests.

Hook acceptance: Claude’s visual check on 2026-11-21 confirmed the 2022 draft opens cleanly, font panel size 11, editable single-string text with native reflow, and unchanged 26-second video. This acceptance does not validate the missing Header/caption/rich-text/animation paths listed above.

## Header/title candidate after hook acceptance

Both durable bootstrap paths forward correction `edl_metadata.title_overlay`.
The authenticated Finish-in-CapCut endpoint additionally forwards the current
session's autosaved `draft.title_overlay`, preserving explicit disable/removal.
This is covered by endpoint tests; no service restart/deployment was performed.

The dedicated video region is implemented with CLI `crop --rect 0,0.09,1,0.82`
and compile item y=-0.18, retaining unit scale and the byte-identical video.
This currently requires an already-composed full-frame working asset. A layered
or moving raw-source composition is refused rather than cropped in the wrong
coordinate system. Other composition support remains a separate gap.

Approved temporary visual fixture (not written to the clip's saved settings):
`HEADER TEST — dedicated band`, Verdana Bold, size 64, white text, no card,
centred, top, dedicated region. On 576×1024 the Activity preview gives Header
centre 71.68px (7%), top 52.48px, width about 460.16px and height 38.39px.
The video begins at 184.32px (18%) and fills the remaining 839.68px (82%).
The existing hook's top anchor becomes 34.4% because it is relative to that
video region. Header remains for all 26 seconds; hook remains 0–5 seconds.

The actual PreviewSurface/TextOverlayPreview components were measured at
1440×1200 and 390×844 viewports. `capcut_header_geometry.json` stores those
measurements and source hashes; tests compare Header/video boxes within 2px.
Header font advances are measured at higher resolution to avoid FreeType's
small-font rounding while retaining the editor's fractional pixel sizes.

Header acceptance: Claude verified `LOOM render-1.5 20261121-2039` on 2026-11-21:
native editable Verdana Bold, panel size 11, white text natively wrapped into two
centred lines at about 7%, lasting 0–26s; black band about 18%; lower video area
about 82% without visible stretching; accepted hook at about 34%, lasting 0–5s.
The temporary Header copy is explicit fixture data only, never a runtime default.
Absent, disabled, or blank saved Header settings produce no Header text, no band,
and no Header-induced hook displacement, even if a stale region value remains.
