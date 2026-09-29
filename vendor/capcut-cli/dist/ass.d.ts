/** Segment-level defaults a Dialogue's Style line carries into the import. */
export interface AssStyleSeed {
    fontSize?: number;
    color?: string;
    alignment?: number;
    bold?: boolean;
    italic?: boolean;
}
export interface AssCue {
    index: number;
    startUs: number;
    endUs: number;
    text: string;
    style?: string;
    spans?: AssSpanStyle[];
    styleSeed?: AssStyleSeed;
}
export declare function parseAss(content: string): AssCue[];
export interface AssSpanStyle {
    start: number;
    end: number;
    bold?: boolean;
    italic?: boolean;
    underline?: boolean;
    color?: string;
    size?: number;
}
export interface AssStyleDef {
    name: string;
    fontName: string;
    fontSize: number;
    color: string;
    alpha: number;
    secondaryColor?: string;
    bold: boolean;
    italic: boolean;
    underline: boolean;
    alignment: number;
    borderStyle: 1 | 3;
    outlineWidth: number;
    outlineColor: string;
    outlineAlpha: number;
    shadowDistance: number;
    backColor: string;
    backAlpha: number;
}
export type AssStyleBase = Omit<AssStyleDef, "name">;
export interface AssWord {
    word: string;
    startUs: number;
    endUs: number;
}
export interface AssEvent {
    startUs: number;
    endUs: number;
    text: string;
    style: string;
    spans?: AssSpanStyle[];
    words?: AssWord[];
}
export interface AssDocument {
    title?: string;
    playResX: number;
    playResY: number;
    styles: AssStyleDef[];
    events: AssEvent[];
}
/** "#RRGGBB" + opacity -> Style-line "&HAABBGGRR" (alpha byte 00 = opaque). */
export declare function formatAssColor(hex: string, alpha?: number): string;
/** "#RRGGBB" -> inline override "&HBBGGRR&" (the \c / \1c form). */
export declare function formatAssInlineColor(hex: string): string;
/**
 * Any ASS colour form back to "#RRGGBB": &HBBGGRR&, &HAABBGGRR, or bare hex
 * digits, case-insensitive. 8 digits carry alpha (returned as 0..1 opacity).
 * Returns null when the value is not hex.
 */
export declare function parseAssColor(raw: string): {
    color: string;
    alpha?: number;
} | null;
/** H:MM:SS.cc — centisecond rounding carried so 1.9999s emits 0:00:02.00. */
export declare function assTime(us: number): string;
export declare function defaultAssStyle(name: string): AssStyleDef;
export declare function renderAss(doc: AssDocument): string;
export interface TextMaterialLike {
    content: string;
    alignment?: number;
    font_name?: string;
    has_border?: boolean;
    border_color?: string;
    border_width?: number;
    border_alpha?: number;
    has_shadow?: boolean;
    shadow_color?: string;
    shadow_distance?: number;
    shadow_alpha?: number;
    background_color?: string;
    background_alpha?: number;
    [key: string]: unknown;
}
export declare function assStyleFromMaterial(mat: TextMaterialLike): {
    style: AssStyleBase;
    spans: AssSpanStyle[];
};
