/** Positive durations use at least one frame; negative durations keep their signed rounding. */
export declare function framesFor(us: number, fps?: number): number;
/** Quantize to the nearest frame without collapsing a positive duration to zero. */
export declare function quantizeToFrame(us: number, fps?: number): number;
export declare function secondsToUs(s: number): number;
export declare function formatTime(us: number): string;
export declare function formatDuration(us: number): string;
export declare function parseTimeInput(input: string): number;
export declare function srtTime(us: number): string;
export declare function vttTime(us: number): string;
