/**
 * CapCut's editor API as both experimental cloud clients call it: `caption --cloud`
 * (capcut-cloud-asr.ts) and `translate --cloud` / `caption --translate`
 * (capcut-cloud-translate.ts). It holds the headers CapCut's desktop app sends,
 * as captured from one install, and the request discipline both clients share:
 * the time limit, the retries and how a failed call is named. The install's
 * identity is written once here, so when ByteDance stops accepting it there is
 * one place to change; each client keeps the signature captured with its own
 * requests.
 */
/** The editor API host both clients call. */
export declare const CAPCUT_EDITOR_HOST = "editor-api-sg.capcutapi.com";
/** The per-capture part of the headers: app version, capture time and its signature. */
export interface CapcutCapturedSignature {
    appVersion: string;
    time: string;
    sign: string;
    stub: string;
    traceId: string;
}
/** The full header set for one captured signature, in the order the app sends them. */
export declare function capcutEditorHeaders(s: CapcutCapturedSignature): Record<string, string>;
/** How long one request may take, and the pause before its first retry. */
export interface CapcutRequestTiming {
    requestTimeoutMs: number;
    /** Pause before the n-th retry is n times this. */
    retryDelayMs: number;
}
/** A duration for a message: whole seconds from one second up, fractions below. */
export declare const seconds: (ms: number) => number;
/**
 * One request with a time limit. `retry` repeats a call that is safe to repeat
 * when the network fails, it times out, or the service answers 429 or 5xx,
 * pausing n x retryDelayMs before the n-th retry; a call that is not safe to
 * repeat is sent once. Any other answer is returned as is. `step` names the
 * call in the error.
 */
export declare function sendCapcutRequest(url: string, init: RequestInit, step: string, t: CapcutRequestTiming, retry: boolean, timeoutMs?: number): Promise<Response>;
/** The editor API's answer: ret "0" (or 0) on success, data with the result. */
export interface EditorApiReply<T> {
    ret: string | number;
    errmsg: string;
    data?: T;
}
/** POST to CapCut's editor API; an HTTP failure names the step. */
export declare function postEditorApi<T>(url: string, headers: Record<string, string>, body: string, step: string, t: CapcutRequestTiming, retry: boolean): Promise<EditorApiReply<T>>;
/** The editor API answers ret "0" (or 0) on success; any other ret is its refusal. */
export declare function requireRetOk(reply: EditorApiReply<unknown>, step: string): void;
