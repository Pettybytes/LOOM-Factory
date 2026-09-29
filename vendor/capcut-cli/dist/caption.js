import { spawnSync } from "node:child_process";
import { randomUUID as uuid } from "node:crypto";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { alignScript, tokenizeScript } from "./align.js";
import { buildEmphasisRanges, DEFAULT_KEYWORD_SIZE, KARAOKE_HIGHLIGHT_COLOR, setTextRanges, } from "./decorators.js";
import { findSegment } from "./draft.js";
import { captionStyleFromPreset } from "./preset.js";
import { captionScript, groupingDefaults, wordSeparator } from "./script.js";
import { parseSrt } from "./srt.js";
import { storedTextLength } from "./text-offsets.js";
/**
 * Generate captions by running whisper on the project's audio and emitting
 * real CapCut caption-track segments (not text-segment mimics — fixes the
 * import-srt pain documented in pyJianYingDraft #148).
 *
 * Whisper integration is shell-out; user supplies the binary path. We support
 * the canonical whisper.cpp output naming convention (`<base>.srt`).
 *
 * Architecture:
 *   1. Resolve audio file (either --audio or extracted from --from-segment)
 *   2. Spawn whisper CLI; capture SRT output
 *   3. Parse SRT into caption cues
 *   4. Create captions track + real subtitle material per cue
 */
export function captionDraft(draft, opts) {
    if (opts.styleRef && opts.preset) {
        throw new Error("--style-ref and --preset are mutually exclusive. Pass one style source.");
    }
    if (opts.karaoke && opts.wordReveal)
        throw new Error("--karaoke and --word-reveal are mutually exclusive.");
    if (opts.minScriptMatch !== undefined &&
        (!Number.isFinite(opts.minScriptMatch) || opts.minScriptMatch < 0 || opts.minScriptMatch > 1)) {
        throw new Error("--min-script-match must be a number in range 0..1.");
    }
    if (opts.minScriptMatch !== undefined && opts.scriptText === undefined) {
        throw new Error("--min-script-match requires --script <file>.");
    }
    if (opts.audioStream !== undefined && (!Number.isInteger(opts.audioStream) || opts.audioStream < 0)) {
        throw new Error("--audio-stream must be a zero-based non-negative integer.");
    }
    const sourceAudio = resolveAudio(draft, opts);
    const selectedAudio = opts.audioStream === undefined
        ? undefined
        : extractAudioStream(sourceAudio, opts.audioStream, opts.ffmpegCmd ?? "ffmpeg");
    let transcription;
    try {
        transcription = runWhisper(selectedAudio?.path ?? sourceAudio, opts);
    }
    finally {
        selectedAudio?.cleanup();
    }
    const recognizedWords = transcription.words.length > 0 ? transcription.words : wordsFromCues(transcription.cues);
    // The transcript's script decides how words join into a cue (no space inside
    // Chinese or Japanese) and how many of them make one when the caller set no
    // --max-words / --max-chars; the script file's wording when one was given,
    // since that is the text the cues will carry.
    const textScript = captionScript(opts.scriptText !== undefined ? opts.scriptText : recognizedWords.map((word) => word.word).join(""));
    const separator = wordSeparator(textScript);
    const grouping = groupingDefaults(textScript);
    let scriptReport;
    let cues;
    if (opts.scriptText !== undefined) {
        const lines = tokenizeScript(opts.scriptText);
        if (lines.length === 0)
            throw new Error("--script file contains no words.");
        if (recognizedWords.length === 0) {
            throw new Error("Whisper produced no words to align the script to. Check the audio is not silent.");
        }
        const aligned = alignScript(lines, recognizedWords);
        scriptReport = aligned.report;
        if (opts.minScriptMatch !== undefined && aligned.report.match_ratio < opts.minScriptMatch) {
            throw new Error(`Script match ${aligned.report.match_ratio.toFixed(3)} is below --min-script-match ${opts.minScriptMatch}. ` +
                `No captions were written; check that --script belongs to this audio.`);
        }
        cues = opts.karaoke
            ? groupWords(aligned.words, opts.maxWords ?? grouping.karaokeMaxWords, opts.maxChars ?? grouping.karaokeMaxChars, (opts.maxGapMs ?? 500) * 1000, separator)
            : // One cue per script line — the author's chunking — split only when a
                // line outgrows --max-chars (words and gaps never split a line).
                aligned.lines.flatMap((line) => groupWords(line, Number.POSITIVE_INFINITY, opts.maxChars ?? grouping.lineMaxChars, Number.POSITIVE_INFINITY, separator));
    }
    else {
        cues =
            opts.karaoke || opts.wordReveal
                ? groupWords(recognizedWords, opts.maxWords ?? grouping.karaokeMaxWords, opts.maxChars ?? grouping.karaokeMaxChars, (opts.maxGapMs ?? 500) * 1000, separator)
                : transcription.cues;
    }
    if (cues.length === 0) {
        throw new Error("Whisper produced no cues. Check the audio file is not silent and the model name is valid.");
    }
    const trackName = opts.trackName ?? "captions";
    const styleSource = opts.styleRef ? findSegment(draft, opts.styleRef) : null;
    const baseStyle = styleSource
        ? extractTextStyle(draft, styleSource.segment.material_id)
        : opts.preset
            ? captionStyleFromPreset(opts.preset)
            : defaultCaptionStyle();
    let track = draft.tracks.find((t) => t.type === "text" && t.name === trackName);
    if (!track) {
        track = {
            id: uuid(),
            type: "text",
            name: trackName,
            attribute: 0,
            segments: [],
        };
        draft.tracks.push(track);
    }
    if (!Array.isArray(draft.materials.texts))
        draft.materials.texts = [];
    const highlightWords = opts.highlightWords ?? [];
    const keywordColor = opts.keywordColor ?? KARAOKE_HIGHLIGHT_COLOR;
    const keywordSize = opts.keywordSize ?? DEFAULT_KEYWORD_SIZE;
    const colorCycle = opts.colorCycle ?? [];
    let created = 0;
    let keywordMatches = 0;
    for (let cueIndex = 0; cueIndex < cues.length; cueIndex++) {
        const cue = cues[cueIndex];
        // --color-cycle rotates the BASE colour per cue; keyword emphasis sits on top.
        const cycleColor = colorCycle.length > 0 ? colorCycle[cueIndex % colorCycle.length] : undefined;
        const cueStyle = cycleColor === undefined ? baseStyle : { ...baseStyle, font_color: cycleColor };
        const emphasis = (text, presetRanges) => buildEmphasisRanges(text, {
            words: highlightWords,
            color: keywordColor,
            sizeMultiplier: keywordSize,
            baseSize: Number(cueStyle.font_size ?? 15),
            baseColor: String(cueStyle.font_color ?? "#FFFFFF"),
            baseBold: Boolean(cueStyle.bold ?? false),
            presetRanges,
        });
        if (opts.wordReveal && cue.words && cue.words.length > 0) {
            for (let wordIndex = 0; wordIndex < cue.words.length; wordIndex++) {
                const visible = cue.words
                    .slice(0, wordIndex + 1)
                    .map((word) => word.word)
                    .join(separator);
                const word = cue.words[wordIndex];
                const next = cue.words[wordIndex + 1];
                const endUs = next?.startUs ?? cue.endUs;
                addCaptionSegment(draft, track, visible, word.startUs, Math.max(word.startUs + 1, endUs), cueStyle);
                created++;
            }
        }
        else if (opts.karaoke && cue.words && cue.words.length > 0) {
            const fullText = cue.words.map((word) => word.word).join(separator);
            let cursor = 0;
            let cueMatches = 0;
            for (const word of cue.words) {
                const start = cursor;
                const end = start + word.word.length;
                const segmentId = addCaptionSegment(draft, track, fullText, word.startUs, word.endUs, cueStyle);
                const karaokeRange = {
                    start,
                    end,
                    font_color: KARAOKE_HIGHLIGHT_COLOR,
                    font_size: Number(cueStyle.font_size ?? 15) * 1.08,
                    bold: true,
                };
                if (highlightWords.length > 0 || cycleColor !== undefined) {
                    // Karaoke ranges are built first; keyword matches override those words.
                    const { ranges, matches } = emphasis(fullText, [karaokeRange]);
                    setTextRanges(draft, segmentId, ranges);
                    cueMatches = matches;
                }
                else {
                    setTextRanges(draft, segmentId, [karaokeRange]);
                }
                cursor = end + separator.length;
                created++;
            }
            keywordMatches += cueMatches;
        }
        else {
            const segmentId = addCaptionSegment(draft, track, cue.text, cue.startUs, cue.endUs, cueStyle);
            if (highlightWords.length > 0) {
                const { ranges, matches } = emphasis(cue.text);
                if (ranges.length > 0)
                    setTextRanges(draft, segmentId, ranges);
                keywordMatches += matches;
            }
            created++;
        }
    }
    return {
        ok: true,
        cues: created,
        track_name: trackName,
        first_cue: { start_us: cues[0].startUs, text: cues[0].text },
        last_cue: { start_us: cues[cues.length - 1].startUs, text: cues[cues.length - 1].text },
        source_audio: sourceAudio,
        engine: opts.whisperCmd ? "shell" : "whisper-cli",
        engine_name: transcription.engine,
        caption_script: textScript,
        words: transcription.words.length,
        karaoke: opts.karaoke ?? false,
        word_reveal: opts.wordReveal || undefined,
        audio_stream: opts.audioStream,
        // undefined when the flags are off, so JSON output stays byte-identical.
        keyword_matches: highlightWords.length > 0 ? keywordMatches : undefined,
        color_cycle: colorCycle.length > 0 ? colorCycle.length : undefined,
        script: scriptReport,
    };
}
export function buildAudioStreamInvocation(audio, stream, output) {
    return ["-y", "-i", audio, "-map", `0:a:${stream}`, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", output];
}
function extractAudioStream(audio, stream, command) {
    const dir = mkdtempSync(join(tmpdir(), "capcut-caption-audio-"));
    const output = join(dir, "selected.wav");
    const cleanup = () => {
        try {
            rmSync(dir, { recursive: true, force: true });
        }
        catch {
            /* ignore */
        }
    };
    const args = buildAudioStreamInvocation(audio, stream, output);
    try {
        const r = spawnSync(command, args, { encoding: "utf-8", timeout: 300_000, maxBuffer: 16 * 1024 * 1024 });
        if (r.error || r.status !== 0 || !existsSync(output)) {
            const detail = r.stderr || r.error?.message || `ffmpeg exited ${r.status}`;
            throw new Error(`Could not extract audio stream ${stream} from ${audio}: ${detail}\n` + `Tried: ${command} ${args.join(" ")}`);
        }
        return { path: output, cleanup };
    }
    catch (error) {
        cleanup();
        throw error;
    }
}
function addCaptionSegment(draft, track, text, startUs, endUs, baseStyle) {
    const matId = uuid();
    const content = JSON.stringify({
        text,
        styles: [{ ...baseStyle, range: [0, storedTextLength(text)] }],
    });
    draft.materials.texts.push({
        id: matId,
        type: "text",
        content,
        font_size: baseStyle.font_size ?? 15,
        text_color: baseStyle.font_color ?? "#FFFFFF",
        alignment: 1,
        sub_type: 1,
        caption_template_info: { category_id: "", category_name: "", effect_id: "", is_new: false, resource_id: "" },
    });
    const segmentId = uuid();
    track.segments.push({
        id: segmentId,
        material_id: matId,
        target_timerange: { start: startUs, duration: Math.max(1, endUs - startUs) },
        source_timerange: { start: 0, duration: Math.max(1, endUs - startUs) },
        speed: 1,
        volume: 1,
        visible: true,
        clip: { alpha: 1, rotation: 0, scale: { x: 1, y: 1 }, transform: { x: 0, y: -0.6 } },
        extra_material_refs: [],
        render_index: 0,
    });
    return segmentId;
}
function resolveAudio(draft, opts) {
    if (opts.audio) {
        if (!existsSync(opts.audio))
            throw new Error(`Audio file not found: ${opts.audio}`);
        return opts.audio;
    }
    if (opts.fromSegment) {
        const found = findSegment(draft, opts.fromSegment);
        if (!found)
            throw new Error(`Segment not found: ${opts.fromSegment}`);
        if (found.track.type !== "audio")
            throw new Error(`--from-segment must be on an audio track (got ${found.track.type})`);
        const mat = draft.materials.audios.find((m) => m.id === found.segment.material_id);
        if (!mat?.path)
            throw new Error(`Audio segment ${opts.fromSegment} has no resolvable material path`);
        if (!existsSync(mat.path))
            throw new Error(`Audio material path doesn't exist on disk: ${mat.path}`);
        return mat.path;
    }
    throw new Error("Missing --audio <path> or --from-segment <id>. Provide one.");
}
function detectEngine(opts) {
    if (opts.whisperEngine && opts.whisperEngine !== "auto")
        return opts.whisperEngine;
    const command = (opts.whisperCmd ?? "whisper").toLowerCase();
    if (command.includes("faster-whisper"))
        return "faster-whisper";
    if (command.includes("whisper-cli") || /(^|[/\\])main(?:\.exe)?$/.test(command))
        return "whisper-cpp";
    return "openai";
}
export function buildWhisperInvocation(engine, audio, model, language, outputDir, json) {
    const prefix = join(outputDir, "transcript");
    if (engine === "whisper-cpp") {
        return {
            args: ["-m", model, "-f", audio, "-l", language, json ? "-oj" : "-osrt", "-of", prefix],
            prefix,
            extension: json ? ".json" : ".srt",
        };
    }
    return {
        args: [
            audio,
            "--model",
            model,
            "--language",
            language,
            "--output_format",
            json ? "json" : "srt",
            "--output_dir",
            outputDir,
            ...(json ? ["--word_timestamps", "True"] : []),
        ],
        prefix,
        extension: json ? ".json" : ".srt",
    };
}
function runWhisper(audio, opts) {
    const cmd = opts.whisperCmd ?? "whisper";
    const model = opts.whisperModel ?? "base";
    const language = opts.language ?? "auto";
    const engine = detectEngine(opts);
    const tmpdirPath = mkdtempSync(join(tmpdir(), "capcut-caption-"));
    try {
        const json = opts.karaoke === true || opts.wordReveal === true;
        const invocation = buildWhisperInvocation(engine, audio, model, language, tmpdirPath, json);
        const { args, prefix, extension } = invocation;
        const r = spawnSync(cmd, args, { encoding: "utf-8", timeout: 300_000, maxBuffer: 16 * 1024 * 1024 });
        if (r.error || r.status !== 0) {
            const stderr = r.stderr || r.error?.message || `whisper exited ${r.status}`;
            throw new Error(`${engine} CLI failed: ${stderr}\nTried: ${cmd} ${args.join(" ")}\n` +
                "Select the matching dialect with --whisper-engine openai|whisper-cpp|faster-whisper and pass its model via --whisper-model.");
        }
        const outputPath = readdirSync(tmpdirPath)
            .filter((name) => name.endsWith(extension))
            .map((name) => join(tmpdirPath, name))[0] ?? `${prefix}${extension}`;
        if (!existsSync(outputPath)) {
            throw new Error(`${engine} finished but no ${extension} output was found. stdout: ${r.stdout?.slice(0, 200)}`);
        }
        if (json) {
            const parsed = parseWhisperJson(readFileSync(outputPath, "utf-8"));
            return { ...parsed, engine };
        }
        const cues = parseSrt(readFileSync(outputPath, "utf-8")).map((cue) => ({
            startUs: cue.startUs,
            endUs: cue.endUs,
            text: cue.text,
        }));
        return { cues, words: [], engine };
    }
    finally {
        try {
            rmSync(tmpdirPath, { recursive: true, force: true });
        }
        catch {
            /* ignore */
        }
    }
}
export function parseWhisperJson(raw) {
    const parsed = JSON.parse(raw);
    const cues = [];
    const words = [];
    const segments = Array.isArray(parsed.segments) ? parsed.segments : [];
    for (const value of segments) {
        const segment = value;
        const startUs = Math.round(Number(segment.start ?? 0) * 1_000_000);
        const endUs = Math.round(Number(segment.end ?? segment.start ?? 0) * 1_000_000);
        const text = String(segment.text ?? "").trim();
        const segmentWords = [];
        if (Array.isArray(segment.words)) {
            for (const item of segment.words) {
                const word = item;
                const textValue = String(word.word ?? word.text ?? "").trim();
                if (!textValue)
                    continue;
                const parsedWord = {
                    word: textValue,
                    startUs: Math.round(Number(word.start ?? segment.start ?? 0) * 1_000_000),
                    endUs: Math.round(Number(word.end ?? segment.end ?? 0) * 1_000_000),
                };
                segmentWords.push(parsedWord);
                words.push(parsedWord);
            }
        }
        if (text)
            cues.push({ startUs, endUs, text, words: segmentWords.length ? segmentWords : undefined });
    }
    const transcription = Array.isArray(parsed.transcription) ? parsed.transcription : [];
    for (const value of transcription) {
        const item = value;
        const offsets = (item.offsets ?? {});
        const startUs = Math.round(Number(offsets.from ?? 0) * 1000);
        const endUs = Math.round(Number(offsets.to ?? offsets.from ?? 0) * 1000);
        const text = String(item.text ?? "").trim();
        if (text)
            cues.push({ startUs, endUs, text });
    }
    if (cues.length === 0 && typeof parsed.text === "string") {
        cues.push({ startUs: 0, endUs: 1_000_000, text: parsed.text.trim() });
    }
    return { cues, words: words.length > 0 ? words : wordsFromCues(cues) };
}
export function wordsFromCues(cues) {
    const words = [];
    for (const cue of cues) {
        const parts = cue.text.trim().split(/\s+/).filter(Boolean);
        if (parts.length === 0)
            continue;
        const duration = Math.max(parts.length, cue.endUs - cue.startUs);
        for (let index = 0; index < parts.length; index++) {
            words.push({
                word: parts[index],
                startUs: cue.startUs + Math.round((duration * index) / parts.length),
                endUs: cue.startUs + Math.round((duration * (index + 1)) / parts.length),
            });
        }
    }
    return words;
}
/**
 * Group timed words into cues: a cue closes when the next word would exceed
 * maxWords, would push the joined text past maxChars, or starts after a gap
 * longer than maxGapUs. `separator` is what joins the words into the cue's
 * text — a space for Latin and Korean, nothing for Chinese and Japanese
 * (wordSeparator in script.ts) — and counts towards maxChars like any other
 * character.
 */
export function groupWords(words, maxWords = 4, maxChars = 28, maxGapUs = 500_000, separator = " ") {
    const cues = [];
    let group = [];
    const flush = () => {
        if (group.length === 0)
            return;
        cues.push({
            startUs: group[0].startUs,
            endUs: group[group.length - 1].endUs,
            text: group.map((word) => word.word).join(separator),
            words: group,
        });
        group = [];
    };
    for (const word of words) {
        const candidate = [...group, word];
        const gap = group.length === 0 ? 0 : word.startUs - group[group.length - 1].endUs;
        if (group.length > 0 &&
            (candidate.length > maxWords ||
                candidate.map((item) => item.word).join(separator).length > maxChars ||
                gap > maxGapUs)) {
            flush();
        }
        group.push(word);
    }
    flush();
    return cues;
}
function extractTextStyle(draft, materialId) {
    const mat = draft.materials.texts.find((m) => m.id === materialId);
    if (!mat)
        return defaultCaptionStyle();
    try {
        const parsed = JSON.parse(mat.content);
        if (Array.isArray(parsed.styles) && parsed.styles.length > 0) {
            const s = parsed.styles[0];
            return { ...s, range: undefined };
        }
    }
    catch {
        /* ignore */
    }
    return defaultCaptionStyle();
}
function defaultCaptionStyle() {
    return {
        font_color: "#FFFFFF",
        font_size: 15,
        bold: false,
        italic: false,
        underline: false,
    };
}
