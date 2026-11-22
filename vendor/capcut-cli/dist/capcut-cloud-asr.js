/**
 * Experimental, unofficial client for the speech recognition behind CapCut's
 * auto-captions (`caption --cloud`). These are CapCut's private endpoints as
 * its desktop app calls them, not a published API: ByteDance can change or
 * refuse them at any time. The audio is uploaded to ByteDance's servers.
 */
import { createHash, createHmac, randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { CAPCUT_EDITOR_HOST, capcutEditorHeaders, postEditorApi, requireRetOk, seconds, sendCapcutRequest, } from "./capcut-cloud-headers.js";
/** CRC32 lookup table for upload verification. */
const CRC_TABLE = new Uint32Array(256);
for (let i = 0; i < 256; i++) {
    let c = i;
    for (let j = 0; j < 8; j++) {
        c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    CRC_TABLE[i] = c;
}
/** Pure CRC32 hex string calculation (zero external dependencies). */
export function crc32(buf) {
    let crc = 0 ^ -1;
    for (let i = 0; i < buf.length; i++) {
        crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ buf[i]) & 0xff];
    }
    return ((crc ^ -1) >>> 0).toString(16).padStart(8, "0");
}
function sha256(data) {
    return createHash("sha256").update(data).digest("hex");
}
function hmac(key, data, encoding) {
    const h = createHmac("sha256", key).update(data, "utf8");
    return encoding === "hex" ? h.digest("hex") : h.digest();
}
function encodeRfc3986(str) {
    return encodeURIComponent(str).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}
/**
 * Pure TypeScript AWS Signature v4 signer (zero-dependency replacement for `aws4`).
 * Conforms exactly to AWS SigV4 specification with ASCII-ordered canonical headers and queries.
 */
export function signAwsV4(opts, creds, now = new Date()) {
    const method = (opts.method || "GET").toUpperCase();
    const service = opts.service || "vod";
    const region = opts.region || "sg";
    const body = opts.body || "";
    const amzDate = opts.headers?.["X-Amz-Date"] || opts.headers?.["x-amz-date"] || now.toISOString().replace(/[:-]|\.\d{3}/g, "");
    const dateStamp = amzDate.substring(0, 8);
    const headers = { ...(opts.headers || {}) };
    headers["Host"] = opts.host;
    headers["X-Amz-Date"] = amzDate;
    if (creds.session_token) {
        headers["x-amz-security-token"] = creds.session_token;
    }
    // Canonical headers sorted by ASCII code point
    const filtered = Object.keys(headers)
        .map((k) => [k.toLowerCase(), headers[k]])
        .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    const canonicalHeaders = filtered.map(([k, v]) => `${k}:${v.trim().replace(/\s+/g, " ")}\n`).join("");
    const signedHeaders = filtered.map(([k]) => k).join(";");
    // Path & Canonical query (ASCII sorted)
    const urlObj = new URL(`https://${opts.host}${opts.path}`);
    const pathname = urlObj.pathname;
    const queryParams = [];
    urlObj.searchParams.forEach((v, k) => {
        queryParams.push([encodeRfc3986(k), encodeRfc3986(v)]);
    });
    queryParams.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    const canonicalQuery = queryParams.map(([k, v]) => `${k}=${v}`).join("&");
    const payloadHash = sha256(body);
    const canonicalRequest = `${method}\n${pathname}\n${canonicalQuery}\n${canonicalHeaders}\n${signedHeaders}\n${payloadHash}`;
    const credentialScope = `${dateStamp}/${region}/${service}/aws4_request`;
    const stringToSign = `AWS4-HMAC-SHA256\n${amzDate}\n${credentialScope}\n${sha256(canonicalRequest)}`;
    const kDate = hmac(`AWS4${creds.secret_access_key}`, dateStamp);
    const kRegion = hmac(kDate, region);
    const kService = hmac(kRegion, service);
    const kSigning = hmac(kService, "aws4_request");
    const signature = hmac(kSigning, stringToSign, "hex");
    headers["Authorization"] =
        `AWS4-HMAC-SHA256 Credential=${creds.access_key_id}/${credentialScope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
    return { headers, url: urlObj.toString() };
}
export const DEFAULT_CAPCUT_HEADERS = capcutEditorHeaders({
    appVersion: "8.4.0",
    time: "1776674107",
    sign: "4a60da398f60fefd56083e55ac9eda17",
    stub: "e8eff844fa0457999a30b4d5f6572dff",
    traceId: "00-aa077272106864a86c4b8b8601bcffff-aa077272106864a801",
});
/** How long to wait for the recognition: 2 minutes plus half the audio's length, at most 30 minutes. */
export function pollBudgetMs(durationMs) {
    return Math.min(30 * 60_000, 120_000 + Math.max(0, durationMs) / 2);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
/** Step 1: Request upload signature from CapCut Web API. */
async function getUploadSignature(t) {
    const url = `https://${CAPCUT_EDITOR_HOST}/lv/v1/upload_sign?aid=359289&device_platform=windows`;
    const body = JSON.stringify({ biz: "cc_pc_text_recognize", key_version: "v5" });
    const json = await postEditorApi(url, DEFAULT_CAPCUT_HEADERS, body, "upload_sign", t, true);
    requireRetOk(json, "upload_sign");
    return json.data;
}
/** Step 2: Apply for upload node and storage location via ByteDance VOD. */
async function applyUpload(creds, t) {
    const path = `/top/v1?Action=ApplyUploadInner&SpaceName=${creds.space_name}&UseQuic=false&Version=2020-11-19&device_platform=win`;
    const signed = signAwsV4({
        host: creds.domain,
        path,
        service: "vod",
        region: "sg",
        method: "GET",
        headers: { "x-amz-security-token": creds.session_token },
    }, creds);
    const res = await sendCapcutRequest(signed.url, { method: "GET", headers: signed.headers }, "ApplyUploadInner", t, true);
    if (!res.ok) {
        throw new Error(`CapCut ApplyUploadInner HTTP error: ${res.status} ${res.statusText}`);
    }
    const json = (await res.json());
    const node = json.Result?.Results?.[0] || json.Result?.InnerUploadAddress?.UploadNodes?.[0];
    if (!node?.StoreInfos?.[0]) {
        throw new Error("CapCut ApplyUploadInner returned no valid upload store nodes.");
    }
    return {
        oid: node.StoreInfos[0].StoreUri,
        host: node.UploadHost,
        auth: node.StoreInfos[0].Auth,
        sessionKey: node.SessionKey,
        vid: node.Vid,
        uploadId: node.StoreInfos[0].UploadID,
    };
}
/** Step 3: Upload binary audio content to TOS storage bucket. */
async function uploadBinary(applyData, fileBuffer, t) {
    const hash = crc32(fileBuffer);
    const authHeaders = { Authorization: applyData.auth };
    // 3.1 Transfer
    const transferUrl = `https://${applyData.host}/upload/v1/${applyData.oid}?uploadid=${applyData.uploadId}&part_number=0&phase=transfer`;
    // The body takes time to send: one more second per 128 KiB on top of the request limit.
    const transferTimeoutMs = t.requestTimeoutMs + Math.ceil(fileBuffer.length / (128 * 1024)) * 1000;
    const transferRes = await sendCapcutRequest(transferUrl, {
        method: "POST",
        headers: {
            ...authHeaders,
            "Content-Type": "application/octet-stream",
            "X-Upload-Content-CRC32": hash,
        },
        body: new Uint8Array(fileBuffer),
    }, "TOS transfer", t, false, transferTimeoutMs);
    if (!transferRes.ok) {
        throw new Error(`CapCut TOS transfer error: ${transferRes.status} ${transferRes.statusText}`);
    }
    // 3.2 Finish
    const finishUrl = `https://${applyData.host}/upload/v1/${applyData.oid}?uploadmode=part&phase=finish&uploadid=${applyData.uploadId}`;
    const finishBody = `0:${hash}`;
    const finishRes = await sendCapcutRequest(finishUrl, { method: "POST", headers: { ...authHeaders, "Content-Type": "text/plain" }, body: finishBody }, "TOS finish", t, false);
    if (!finishRes.ok) {
        throw new Error(`CapCut TOS finish error: ${finishRes.status} ${finishRes.statusText}`);
    }
    const finishData = (await finishRes.json());
    const key = finishData.data?.key;
    if (!key) {
        throw new Error("CapCut TOS upload finish did not return storage key.");
    }
    return key;
}
/** Step 4: Commit uploaded object to ByteDance VOD. */
async function commitUpload(applyData, storeKey, creds, t) {
    const body = JSON.stringify({
        Functions: [
            {
                Input: { SnapshotTime: 0.0 },
                Name: "Snapshot",
            },
        ],
        SessionKey: applyData.sessionKey,
        SuccessOids: [storeKey],
    });
    const path = "/top/v1?Action=CommitUploadInner&SpaceName=cc_pc_text_recognize_sg&Version=2020-11-19&device_platform=win";
    const signed = signAwsV4({
        host: creds.domain,
        path,
        service: "vod",
        region: "sdwdmwlll",
        method: "POST",
        body,
        headers: {
            "Content-Type": "application/json",
            "x-amz-security-token": creds.session_token,
        },
    }, creds);
    const res = await sendCapcutRequest(signed.url, { method: "POST", headers: signed.headers, body }, "CommitUploadInner", t, false);
    if (!res.ok) {
        throw new Error(`CapCut CommitUploadInner HTTP error: ${res.status} ${res.statusText}`);
    }
    const setCookie = res.headers.get("set-cookie");
    let cookie;
    if (setCookie?.includes("odin_tt=")) {
        cookie = setCookie
            .split(";")
            .find((c) => c.trim().startsWith("odin_tt="))
            ?.trim();
    }
    return { cookie };
}
/** Step 5: Dispatch ASR task to CapCut speech recognition engine. */
async function createSttTask(vid, md5, duration, t, cookie) {
    const url = "https://editor-api-sg.capcutapi.com/lv/v1/common_task/new?device_brand=Apple%20Inc.&device_id=7522322460692940299&iid=7630752366099744533&aid=359289&region=VN&device_platform=windows&babi_param=%7B%22feature_entrance%22%3A%22editor%22%2C%22feature_entrance_detail%22%3A%22editor-elements-captions-subtitle_recognition%22%2C%22feature_key%22%3A%22subtitle_recognition%22%2C%22scenario%22%3A%22video_editor%22%7D&app_name=CapCut&device_type=&channel=capcutpc_web_product&os_version=10.0.19045&version_code=8.4.0&version_name=8.4.0";
    const payloadObj = {
        cap_json: {
            adjust_endtime: 200,
            audio: vid,
            audio_type: "vid",
            caption_type: 0,
            client_request_id: randomUUID(),
            duration,
            enable_cache: true,
            enter_from: "asr",
            max_lines: 1,
            md5,
            pack_options: { need_attribute: true },
            songs_info: [{ end_time: duration, id: "", start_time: 0 }],
            words_per_line: 16,
        },
    };
    const body = JSON.stringify({
        bind_id: randomUUID(),
        can_queue: true,
        enter_from: "asr",
        tasks: [
            {
                context: randomUUID(),
                payload: JSON.stringify(payloadObj),
                req_key: "cc_audio_subtitle_asr",
                task_version: "v3",
            },
        ],
    });
    const headers = {
        ...DEFAULT_CAPCUT_HEADERS,
        "device-time": "1776678395",
        sign: "3df5bc111ef176f926d0b6c66f9f7eb4",
        "x-khronos": "1776678395",
        "x-ss-stub": "4b0dc88314b75c0076c15eb3cc096ed1",
    };
    if (cookie)
        headers["Cookie"] = cookie;
    const json = await postEditorApi(url, headers, body, "createSttTask", t, false);
    requireRetOk(json, "createSttTask");
    const task = json.data?.tasks?.[0];
    if (!task)
        throw new Error("CapCut createSttTask returned no task details.");
    return { id: task.id, token: task.token };
}
/** Step 6: Poll ASR task until completion and parse recognized utterances & words. */
async function pollSttStatus(taskId, token, t, cookie, opts) {
    const url = `https://${CAPCUT_EDITOR_HOST}/lv/v1/common_task/query?aid=359289&device_platform=windows`;
    const body = JSON.stringify({
        tasks: [{ id: taskId, req_key: "cc_audio_subtitle_asr", task_version: "v3", token }],
    });
    const queryHeaders = {
        ...DEFAULT_CAPCUT_HEADERS,
        "device-time": "1776678404",
        sign: "5f384ae9f9123089816212dcaffa067b",
        "x-ss-stub": "71303c871aabb30c9e7b634d2e6d8b5a",
        "x-khronos": "1776678404",
    };
    if (cookie)
        queryHeaders["Cookie"] = cookie;
    const deadline = Date.now() + t.pollBudgetMs;
    for (;;) {
        const json = await postEditorApi(url, queryHeaders, body, "poll query", t, true);
        if (json.ret === "1014") {
            throw new Error("CapCut Cloud ASR returned error 1014 (System Busy / Security Token). Try running without --cloud or use local whisper.");
        }
        requireRetOk(json, "poll");
        const task = json.data?.tasks?.[0];
        if (!task)
            throw new Error("CapCut poll query returned empty task list.");
        const progress = task.progress || 0;
        const status = task.status;
        opts?.onProgress?.(progress, status);
        if (status === "succeed" || status === "success" || progress === 100) {
            const rawPayload = task.payload || task.detail_info;
            if (!rawPayload)
                return { utterances: [] };
            try {
                return JSON.parse(rawPayload);
            }
            catch (e) {
                throw new Error(`Failed to parse CapCut ASR payload JSON: ${e.message}`);
            }
        }
        if (status === "failed") {
            throw new Error(`CapCut Cloud ASR processing failed: ${task.err_msg || "Unknown error"}`);
        }
        if (Date.now() + t.pollIntervalMs > deadline)
            break;
        await sleep(t.pollIntervalMs);
    }
    throw new Error(`CapCut Cloud ASR timed out after ${seconds(t.pollBudgetMs)} seconds (the wait grows with the audio's length).`);
}
/**
 * Upload the audio to CapCut's cloud speech recognition and return its
 * sentence cues and word timings (milliseconds from the service, as µs).
 */
export async function transcribeAudioCapcutCloud(audioPath, opts) {
    if (!existsSync(audioPath)) {
        throw new Error(`Audio file not found: ${audioPath}`);
    }
    const durationMs = Math.round(opts.durationMs);
    if (!Number.isFinite(durationMs) || durationMs <= 0) {
        throw new Error(`CapCut Cloud ASR needs the audio length in ms (got ${opts.durationMs}).`);
    }
    const fileBuffer = readFileSync(audioPath);
    const md5 = createHash("md5").update(fileBuffer).digest("hex");
    // 1. Sign
    const t = {
        requestTimeoutMs: opts.requestTimeoutMs ?? 60_000,
        retryDelayMs: opts.retryDelayMs ?? 1500,
        pollIntervalMs: opts.pollIntervalMs ?? 2000,
        pollBudgetMs: opts.pollBudgetMs ?? pollBudgetMs(durationMs),
    };
    const creds = await getUploadSignature(t);
    // 2. Apply upload
    const applyData = await applyUpload(creds, t);
    // 3. Upload binary
    const storeKey = await uploadBinary(applyData, fileBuffer, t);
    // 4. Commit upload
    const { cookie } = await commitUpload(applyData, storeKey, creds, t);
    // 5. Create STT task
    const taskInfo = await createSttTask(applyData.vid, md5, durationMs, t, cookie);
    // 6. Poll status
    const payload = await pollSttStatus(taskInfo.id, taskInfo.token, t, cookie, opts);
    const utterances = payload.utterances || [];
    const cues = [];
    const words = [];
    for (const u of utterances) {
        const cueWords = [];
        if (Array.isArray(u.words)) {
            for (const w of u.words) {
                const text = (w.text || "").trim();
                if (!text)
                    continue;
                const startUs = Math.round((w.start_time || 0) * 1000);
                const endUs = Math.round((w.end_time || 0) * 1000);
                const wordObj = { word: text, startUs, endUs };
                cueWords.push(wordObj);
                words.push(wordObj);
            }
        }
        const cueStartUs = Math.round((u.start_time || 0) * 1000);
        const cueEndUs = Math.round((u.end_time || 0) * 1000);
        cues.push({
            startUs: cueStartUs,
            endUs: cueEndUs,
            text: (u.text || "").trim(),
            words: cueWords.length > 0 ? cueWords : undefined,
        });
    }
    return {
        cues,
        words,
        engine: "capcut-cloud",
        engine_name: payload.model || "capcut-big_asr",
        duration_ms: durationMs,
    };
}
