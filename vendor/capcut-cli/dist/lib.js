/**
 * Public library entry point for capcut-cli.
 *
 * The CLI lives in `index.ts` (it runs `main()` on import, so it is not
 * importable as a library). This module re-exports the stable, side-effect-free
 * core so other tools can read, inspect, lint, and write CapCut/JianYing drafts
 * programmatically:
 *
 *   import { loadDraft, saveDraft, lintDraft } from "capcut-cli";
 *
 *   const { draft, filePath } = loadDraft("./draft_content.json");
 *   const issues = lintDraft(draft);
 *   saveDraft(filePath, draft);
 */
export { commandNames, GLOBAL_OPTION_SPECS } from "./command-specs.js";
export { runDoctor } from "./doctor.js";
export { extractCodeUnitStyleRanges, extractStyleRanges, extractText, findDraft, findMaterial, findMaterialGlobal, findSegment, getMaterialTypes, getTracksByType, loadDraft, saveDraft, updateTextContent, } from "./draft.js";
export { CJK_SCRIPT_LIMITS, captionLimits, captionScript, DEFAULT_LINT_OPTIONS, lintDraft, lintExitCode, scriptLimitsExcept, summarize, } from "./lint.js";
export { runCommand } from "./runner.js";
export { groupingDefaults, wordSeparator } from "./script.js";
export { fromStoredOffset, rangesLookDoubled, repairDoubledRanges, storedTextLength, toStoredOffset, } from "./text-offsets.js";
export { framesFor, quantizeToFrame } from "./time.js";
export { detectVersion } from "./version.js";
