/** JS string code-unit index -> the offset CapCut stores. */
export declare function toStoredOffset(text: string, codeUnitIdx: number): number;
/** Stored offset -> JS string code-unit index. */
export declare function fromStoredOffset(text: string, storedOffset: number): number;
/** The stored offset one past the last character — a full-span range's end. */
export declare function storedTextLength(text: string): number;
/**
 * True when every stored range is the pre-0.19.1 doubled form.
 *
 * Deliberately narrow, because this is what licenses an automatic repair. The
 * decisive signal is `max(end) === 2 * text.length`: for any non-empty text
 * that offset is past the end as code units, so the draft cannot be a valid
 * code-unit draft, and it is exactly where the old writer's trailing block
 * landed. Every offset must also be even — the doubling writer could not emit
 * an odd one — and inside `[0, 2n]`. An app-authored draft ends at `n` and a
 * repaired one ends at `n`, so neither is ever touched twice.
 */
export declare function rangesLookDoubled(text: string, ranges: ReadonlyArray<readonly [number, number]>): boolean;
/**
 * Halve doubled ranges back to code units. Returns null when the ranges are
 * already code units, so callers can tell "repaired" from "nothing to do".
 */
export declare function repairDoubledRanges(text: string, ranges: ReadonlyArray<readonly [number, number]>): Array<[number, number]> | null;
