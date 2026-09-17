// LOOM Source Capture — background service worker.
// Two user-initiated modes:
//   (a) feasibility probe: bounded chrome.debugger Network inspection.
//   (b) Acquire for Reaction: attach + observe (NO auto-reload — the Reel is
//       already playing), capture the first unambiguous direct-MP4's FULL signed
//       URL (volatile only), detach immediately, POST to the localhost helper.
//
// Navigation safety (mirrors infra/acquisition/browser_flow.py):
//   - an expected SAME-content reload is tolerated and re-inspected
//   - a DIFFERENT content id fails closed (never acquire an adjacent/other post)
//
// The full signed URL lives only in `mem.accepted[].fullUrl` / `mem.fullCandidate`
// (volatile); it is never persisted.

importScripts('config.js');

const KEY = 'inspection';
const WINDOW_MS = 12000;   // fail-closed maximum (not a wait)
const HELPER_URL = (self.LOOM_HELPER_URL || 'http://127.0.0.1:8787').replace(/\/+$/, '');
const HELPER_TOKEN = self.LOOM_HELPER_TOKEN || '';
const ACTOR = self.LOOM_ACTOR || 'bryan';

let activeTabId = null;
let timer = null;
let selfReload = false;      // feasibility "Inspect + reload" only
let acquireMode = false;
let acquireCanonical = '';
let expectedContentId = '';
let acquireSent = false;
let phaseTimers = [];
let mem = { candidates: [], counts: emptyCounts(), startedAt: null, deadline: null,
            accepted: [], fullCandidate: null, result: null };

const NON_MEDIA_RT = new Set(['Document', 'Stylesheet', 'Script', 'Font', 'Manifest',
  'TextTrack', 'EventSource', 'WebSocket', 'Ping', 'Preflight']);
const IMAGE_EXT_RE = /\.(jpg|jpeg|png|webp|gif|avif|heic|bmp)(\?|$)/i;
const AD_HOST_RE = /(analytics|telemetry|tracking|ads\.|doubleclick)/i;
const AD_PATH_RE = /(\/analytics|\/telemetry|\/tracking|\/ads|\/tr)/i;
const IG_ID_RE = /instagram\.com\/(?:reel|p|tv|stories)\/([A-Za-z0-9_-]+)/;
const TYPE_PRIORITY = { direct_video: 0, manifest: 1, segment: 2, audio: 3 };

function emptyCounts() {
  return { scheme_rejected: 0, hostname_rejected: 0, resource_type_rejected: 0,
    content_type_rejected: 0, image_rejected: 0, analytics_ad_rejected: 0, accepted: 0 };
}

function isApprovedHost(hostname) {
  const h = (hostname || '').toLowerCase().replace(/\.$/, '');
  const exact = ['instagram.com', 'cdninstagram.com', 'fbcdn.net', 'akamaihd.net'];
  const suffixes = ['.cdninstagram.com', '.fbcdn.net', '.akamaihd.net', '.instagram.com'];
  return exact.includes(h) || suffixes.some((s) => h.endsWith(s));
}

function classifyMedia(url) {
  const path = (url.split('?')[0] || '').toLowerCase();
  if (/\.(mp4|m4v|webm)$/.test(path)) return 'direct_video';
  if (/\.(m3u8|mpd)$/.test(path)) return 'manifest';
  if (/\.(m4s|fmp4|ts)$/.test(path)) return 'segment';
  if (/\.(m4a|aac|mp3|ogg|opus)$/.test(path)) return 'audio';
  return null;
}

function isMediaCandidate(url, mimeType) {
  const mt = (mimeType || '').toLowerCase();
  if (mt) {
    if (/image\/|text\/html|text\/css|application\/javascript|application\/json/.test(mt)) return false;
    if (/video\/|audio\/|mpegurl|dash\+xml|octet-stream/.test(mt)) return true;
  }
  return classifyMedia(url) !== null;
}

function classifyRequest(url, mimeType, resourceType) {
  if (!/^https:\/\//i.test(url)) return 'scheme_rejected';
  if (resourceType === 'Image') return 'image_rejected';
  if (resourceType && NON_MEDIA_RT.has(resourceType)) return 'resource_type_rejected';
  let host, path;
  try { const u = new URL(url); host = u.hostname; path = u.pathname; } catch { return 'content_type_rejected'; }
  if (AD_HOST_RE.test(host) || AD_PATH_RE.test(path)) return 'analytics_ad_rejected';
  if (!isApprovedHost(host)) return 'hostname_rejected';
  if ((mimeType || '').toLowerCase().startsWith('image/') || IMAGE_EXT_RE.test(path)) return 'image_rejected';
  if (isMediaCandidate(url, mimeType)) return 'accepted';
  return 'content_type_rejected';
}

function instagramContentId(url) {
  const m = (url || '').match(IG_ID_RE);
  return m ? m[1] : '';
}

function classifyNavigation(newUrl, expectedId) {
  const newId = instagramContentId(newUrl);
  if (!newId || !expectedId) return 'unknown';
  return newId === expectedId ? 'same_content' : 'different_content';
}

function selectBestCandidate(accepted) {
  if (!accepted || !accepted.length) return null;
  return [...accepted].sort((a, b) => {
    const pa = (TYPE_PRIORITY[a.type] !== undefined) ? TYPE_PRIORITY[a.type] : 9;
    const pb = (TYPE_PRIORITY[b.type] !== undefined) ? TYPE_PRIORITY[b.type] : 9;
    if (pa !== pb) return pa - pb;
    return b.order - a.order;
  })[0];
}

function redact(url) {
  try { const u = new URL(url); return u.protocol + '//' + u.host + u.pathname; } catch { return '(unparseable)'; }
}

function snapshot(phase, extra) {
  return {
    phase,
    attached: activeTabId !== null,
    startedAt: mem.startedAt,
    deadline: mem.deadline,
    candidates: mem.candidates,       // redacted only
    counts: mem.counts,
    result: mem.result,               // redacted helper result
    error: (extra && extra.error !== undefined) ? extra.error : null,
  };
}

async function persist(phase, extra) {
  await chrome.storage.session.set({ [KEY]: snapshot(phase, extra) });
}

function clearPhaseTimers() { phaseTimers.forEach((t) => clearTimeout(t)); phaseTimers = []; }
function stopTimer() { if (timer) { clearTimeout(timer); timer = null; } }

async function detach() {
  if (activeTabId !== null) {
    try { chrome.debugger.detach({ tabId: activeTabId }); } catch (e) {}
    activeTabId = null;
  }
}

async function finish(phase, error) {
  stopTimer(); clearPhaseTimers(); await detach();
  mem.deadline = null; acquireMode = false;
  await persist(phase || (mem.candidates.length === 0 ? 'no_traffic' : 'complete'), { error: error || null });
}

async function finishAcquire(phase, error) {
  clearPhaseTimers(); acquireMode = false; mem.deadline = null;
  await persist(phase, { error: error || null });
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (source.tabId !== activeTabId) return;
  let url, mime = '', rt = '', fromCache = false;
  if (method === 'Network.requestWillBeSent') {
    const r = params && params.request;
    url = r && r.url; rt = (params && params.type) || '';
  } else if (method === 'Network.responseReceived') {
    const r = params && params.response;
    url = r && r.url; mime = (r && r.mimeType) || '';
    fromCache = !!(r && (r.fromDiskCache || r.fromMemoryCache));
  }
  if (!url) return;
  const reason = classifyRequest(url, mime, rt);
  mem.counts[reason] = (mem.counts[reason] || 0) + 1;
  if (reason === 'accepted') {
    const key = redact(url);
    const existing = mem.candidates.find((c) => c.redacted === key);
    if (existing) { if (mime) existing.mimeType = mime; if (fromCache) existing.fromCache = true; }
    else mem.candidates.push({ host: new URL(url).hostname, type: classifyMedia(url) || 'media', mimeType: mime || null, fromCache, redacted: key });
    const acc = mem.accepted.find((a) => a.redacted === key);
    if (acc) { if (mime) acc.mimeType = mime; if (fromCache) acc.fromCache = true; }
    else mem.accepted.push({ fullUrl: url, host: new URL(url).hostname, type: classifyMedia(url) || 'media',
      mimeType: mime || null, fromCache, redacted: key, order: mem.accepted.length });

    if (acquireMode) maybeHandoff(false);
  }
  if (acquireMode) persist('collecting', {});
});

function maybeHandoff(timedOut) {
  if (acquireSent) return;                       // exactly once
  if (!mem.accepted.length) {
    if (timedOut) finishAcquire('failed', 'no media candidate captured — replay the Reel and retry Acquire for Reaction');
    return;
  }
  const best = selectBestCandidate(mem.accepted);
  if (best.type === 'direct_video' || timedOut) {
    acquireSent = true;
    mem.fullCandidate = best.fullUrl;
    onHandoff(best.fullUrl);
  }
  // else: a manifest/segment is present but not an unambiguous MP4 — wait.
}

async function onHandoff(candidateUrl) {
  stopTimer();
  clearPhaseTimers();
  await detach();
  mem.deadline = null;
  await persist('sending', {});
  phaseTimers.push(setTimeout(() => { if (acquireMode) persist('downloading', {}); }, 1500));
  phaseTimers.push(setTimeout(() => { if (acquireMode) persist('validating', {}); }, 6000));
  await postToHelper(candidateUrl);
}

async function postToHelper(candidateUrl) {
  try {
    const resp = await fetch(HELPER_URL + '/acquire', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + HELPER_TOKEN },
      body: JSON.stringify({
        canonical_url: acquireCanonical,
        candidate_url: candidateUrl,
        candidate_host: (() => { try { return new URL(candidateUrl).hostname; } catch { return ''; } })(),
        mime_type: 'video/mp4',
        media_type: 'direct_video',
        actor: ACTOR,
      }),
    });
    let data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (data && data.ok) {
      mem.result = data;
      await finishAcquire('saved', undefined);
    } else {
      await finishAcquire('failed', (data && (data.message || data.error)) || ('helper HTTP ' + resp.status));
    }
  } catch (e) {
    await finishAcquire('failed', 'helper unreachable: ' + (e && e.message ? e.message : e));
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'startInspection') { startInspection(msg.tabId, false).then(sendResponse); return true; }
  if (msg.type === 'startAndReload') { startInspection(msg.tabId, true).then(sendResponse); return true; }
  if (msg.type === 'acquireForReaction') {
    acquireMode = true;
    acquireCanonical = msg.canonicalUrl || '';
    expectedContentId = instagramContentId(acquireCanonical);
    acquireSent = false;
    mem.fullCandidate = null;
    mem.result = null;
    startInspection(msg.tabId, false).then(sendResponse);
    return true;
  }
  if (msg.type === 'stopInspection') { finish().then(() => sendResponse({ ok: true })); return true; }
  if (msg.type === 'getState') {
    chrome.storage.session.get(KEY).then((o) => sendResponse(o[KEY] || { phase: 'ready', attached: false, candidates: [], counts: emptyCounts(), error: null }));
    return true;
  }
});

async function startInspection(tabId, reload) {
  const cur = await chrome.storage.session.get(KEY);
  if (cur[KEY] && cur[KEY].attached) return { ok: false, error: 'inspection already active' };
  activeTabId = tabId;
  mem = { candidates: [], counts: emptyCounts(), startedAt: Date.now(), deadline: null,
          accepted: [], fullCandidate: null, result: null };
  await persist('attaching', {});
  return await new Promise((resolve) => {
    chrome.debugger.attach({ tabId }, '1.3', () => {
      if (chrome.runtime.lastError) {
        const err = chrome.runtime.lastError.message;
        activeTabId = null;
        persist('failed', { error: err });
        resolve({ ok: false, error: err });
        return;
      }
      chrome.debugger.sendCommand({ tabId }, 'Network.enable', {}, () => {
        mem.startedAt = Date.now();
        mem.deadline = mem.startedAt + WINDOW_MS;
        persist(reload ? 'collecting' : 'active', {});
        if (reload) { selfReload = true; try { chrome.tabs.reload(tabId); } catch (e) {} }

        timer = setTimeout(() => {
          if (acquireMode && !acquireSent) maybeHandoff(true);
          else if (!acquireMode) finish();
        }, WINDOW_MS);
        resolve({ ok: true });
      });
    });
  });
}

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === activeTabId) finish('failed', 'tab closed');
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (tabId !== activeTabId || changeInfo.status !== 'loading') return;

  if (acquireMode && !acquireSent) {
    const cls = changeInfo.url ? classifyNavigation(changeInfo.url, expectedContentId) : 'unknown';
    if (cls === 'different_content') {
      finishAcquire('failed', 'navigated away from the selected Reel (different content)');
    }
    // 'same_content' / 'unknown': tolerate + re-inspect (via onDetach reattach)
    return;
  }

  // feasibility mode: preserve the "Inspect + reload" self-reload path
  if (selfReload) { selfReload = false; return; }
  finish('failed', 'tab navigated');
});

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId !== activeTabId) return;
  const tabId = source.tabId;
  activeTabId = null;
  if (acquireMode && !acquireSent) {
    setTimeout(() => {
      if (acquireMode && !acquireSent) reattach(tabId);
    }, 800);
  }
});

function reattach(tabId) {
  chrome.debugger.attach({ tabId }, '1.3', () => {
    if (chrome.runtime.lastError) {
      finishAcquire('failed', 're-attach failed: ' + chrome.runtime.lastError.message);
      return;
    }
    activeTabId = tabId;
    chrome.debugger.sendCommand({ tabId }, 'Network.enable', {}, () => {
      if (acquireMode && !acquireSent) persist('collecting', {});
    });
  });
}
