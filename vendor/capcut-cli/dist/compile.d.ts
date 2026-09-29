import { type TextRangeInput, type TextStyleOptions } from "./decorators.js";
export interface CompileItem {
    ref?: string;
    path?: string;
    text?: string;
    start: number;
    duration?: number;
    volume?: number;
    fontSize?: number;
    color?: string;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    type?: "video" | "photo";
    sourceStart?: number;
    speed?: number;
    opacity?: number;
    rotation?: number;
    scale?: number;
}
export interface CompileTrack {
    type: "video" | "audio" | "text";
    name?: string;
    items: CompileItem[];
}
export interface CompileSpec {
    name?: string;
    width?: number;
    height?: number;
    fps?: number;
    ratio?: string;
    tracks: CompileTrack[];
    operations?: CompileOperation[];
}
export type CompileOperation = {
    op: "transition";
    target: string;
    slug: string;
    duration?: number;
    jianying?: boolean;
} | {
    op: "filter";
    slug: string;
    start: number;
    duration: number;
    intensity?: number;
    trackName?: string;
    jianying?: boolean;
} | {
    op: "effect";
    slug: string;
    start: number;
    duration: number;
    params?: number[];
    trackName?: string;
    jianying?: boolean;
} | {
    op: "keyframe";
    target: string;
    property: string;
    time: number;
    value: number;
    easing?: string;
} | {
    op: "audio-fade";
    target: string;
    fadeIn?: number;
    fadeOut?: number;
} | {
    op: "text-style";
    target: string;
    style: TextStyleOptions;
} | {
    op: "text-ranges";
    target: string;
    ranges: TextRangeInput[];
} | {
    op: "template";
    path: string;
    start: number;
    duration: number;
    text?: string;
    ref?: string;
} | {
    op: "captions";
    path: string;
    trackName?: string;
    styleRef?: string;
    timeOffset?: number;
};
export interface CompileOptions {
    templateDir: string;
    outDir: string;
    specDir: string;
    /** Store seeding mode handed to initDraft (see InitOptions.seed). */
    seed?: "auto" | "always" | "off";
}
export interface CompileResult {
    ok: boolean;
    name: string;
    draft_path: string;
    /** Where the draft's skeleton came from (the store's newest project, or a template directory). */
    template: import("./factory.js").TemplateReport;
    file_path: string;
    tracks: number;
    segments: number;
    duration_us: number;
    warnings: string[];
    refs: Record<string, string>;
}
export interface CompilePlan {
    ok: boolean;
    name: string;
    canvas: {
        width: number;
        height: number;
        fps: number;
        ratio: string;
    };
    tracks: number;
    items: number;
    operations: number;
    refs: string[];
    media: string[];
}
export declare function parseSpec(raw: string): CompileSpec;
export declare function validateSpec(spec: unknown): asserts spec is CompileSpec;
/**
 * Substitute {{key}} placeholders from a JSONL row into every string value of
 * a parsed spec (or any JSON-shaped value). Returns a new tree; the input is
 * never mutated. Row values must be strings, numbers, or booleans; a
 * placeholder with no matching row key is an error, because silently building
 * a draft whose title reads "{{title}}" is the mass-production failure mode.
 */
export declare function substitutePlaceholders<T>(value: T, row: Record<string, unknown>): T;
export declare function planCompile(spec: CompileSpec, specDir: string): CompilePlan;
export declare function compileDraft(spec: CompileSpec, opts: CompileOptions): CompileResult;
