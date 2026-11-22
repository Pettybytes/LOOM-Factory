export interface ServeOptions {
    queuePath?: string;
    cliPath: string;
    failFast?: boolean;
    workers?: number;
    retries?: number;
    timeoutMs?: number;
    backoffMs?: number;
    maxBufferBytes?: number;
}
export interface JobInput {
    id?: string;
    cmd: string;
    args?: string[];
    project?: string;
    timeoutMs?: number;
    retries?: number;
}
export interface JobResult {
    id?: string;
    ok: boolean;
    cmd: string;
    args: string[];
    status: number | null;
    stdout?: unknown;
    stderr?: string;
    attempts?: number;
    duration_ms?: number;
    deduplicated?: boolean;
    timed_out?: boolean;
    overflow?: boolean;
}
export interface ServeSummary {
    succeeded: number;
    failed: number;
    deduplicated: number;
}
/**
 * Bounded JSONL job runner for n8n/Make/Coze/cron.
 *
 * Jobs for different projects may run in parallel; jobs targeting the same
 * project are serialized so two writers never race. An optional stable `id`
 * deduplicates retries from external orchestrators. Each job has bounded
 * output capture, configurable timeout, retry/backoff, and one JSON result
 * line. Capture thresholds are checked every 25ms and when the child exits;
 * they are not hard disk quotas. Locks and IDs last for this queue invocation.
 */
export declare function serveQueue(opts: ServeOptions): Promise<ServeSummary>;
