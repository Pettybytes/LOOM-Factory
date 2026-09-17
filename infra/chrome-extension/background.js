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
const IG_ID_RE = /instagram\.com\/(?:reels?|p|tv|stories)\/([A-Za-z0-9_-]+)/;
const TYPE_PRIORITY = { manifest: 0, direct_video: 1, mp4: 2, segment: 3, audio: 4 };
const INSUFFICIENT_FMP4_SET = 'INSUFFICIENT_FMP4_SET';   // timeout must not force partial assembly

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
  if (/\.(m4v|webm)$/.test(path)) return 'direct_video';   // unambiguous standalone container
  if (/\.mp4$/.test(path)) return 'mp4';                    // AMBIGUOUS (standalone OR fMP4 fragment)
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

function mostRecent(items) {
  if (!items || !items.length) return null;
  return items.reduce((a, b) => ((b.order || 0) >= (a.order || 0) ? b : a));
}

function representationOf(c) {
  if (c.representation === 'video' || c.representation === 'audio') return c.representation;
  const mt = (c.mimeType || '').toLowerCase();
  if (mt.startsWith('audio/')) return 'audio';
  if (mt.startsWith('video/')) return 'video';
  if (c.type === 'audio') return 'audio';
  return 'video';
}

function fragmentRole(c) {
  if (c.role) return c.role;
  if (c.type === 'segment') return 'media';
  if (c.type === 'direct_video') return 'standalone';
  if (c.type === 'mp4') {
    if (c.rangeStart !== null && c.rangeStart !== undefined) return 'media';
    if (c.range) return 'media';
    return 'init';
  }
  if (c.type === 'manifest') return 'manifest';
  return 'unknown';
}

function parseRangeHeader(v) {
  const m = /^bytes=(\d+)-(\d*)$/.exec((v || '').trim());
  if (!m) return null;
  return [parseInt(m[1], 10), m[2] ? parseInt(m[2], 10) : null];
}

function parseContentRange(v) {
  const m = /^bytes\s+(\d+)-(\d+)\/(\d+)$/.exec((v || '').trim());
  if (!m) return null;
  return [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
}

function persistedCandidate(c) {
  // Sanitized candidate for persistence: redacted URL + range metadata only.
  // Never the full signed URL, cookies, or auth headers.
  return {
    host: c.host || '',
    type: c.type,
    representation: representationOf(c),
    role: fragmentRole(c),
    redacted: c.redacted !== undefined ? c.redacted : redact(c.fullUrl || ''),
    range: c.range || null,
    rangeStart: (c.rangeStart !== undefined) ? c.rangeStart : null,
    rangeEnd: (c.rangeEnd !== undefined) ? c.rangeEnd : null,
    rangeTotal: (c.rangeTotal !== undefined) ? c.rangeTotal : null,
    order: c.order,
  };
}

function orderAndValidateRanges(fragments) {
  if (!fragments || !fragments.length) return null;
  if (fragments.some((f) => f.rangeStart === null || f.rangeStart === undefined)) return null;
  const base = redact(fragments[0].fullUrl || '');
  if (fragments.some((f) => redact(f.fullUrl || '') !== base)) return null;
  const ordered = [...fragments].sort((a, b) => (a.rangeStart - b.rangeStart) || ((a.order || 0) - (b.order || 0)));
  const total = ordered[0].rangeTotal;
  let prevEnd = null;
  for (let i = 0; i < ordered.length; i++) {
    const f = ordered[i];
    const start = f.rangeStart;
    const end = f.rangeEnd;
    if (end === null || end === undefined) {
      if (i !== ordered.length - 1) return null;
      continue;
    }
    if (start > end) return null;
    const ft = f.rangeTotal;
    if (total !== null && total !== undefined && ft !== null && ft !== undefined && ft !== total) return null;
    if (prevEnd !== null) {
      if (start <= prevEnd) return null;
      if (start > prevEnd + 1) return null;
    }
    prevEnd = end;
  }
  return ordered;
}

function planRepresentation(fragments) {
  if (!fragments || !fragments.length) return null;
  const inits = fragments.filter((f) => fragmentRole(f) === 'init');
  const medias = fragments.filter((f) => fragmentRole(f) === 'media');
  const standalones = fragments.filter((f) => fragmentRole(f) === 'standalone');
  if (standalones.length && !inits.length && !medias.length) {
    const best = mostRecent(standalones);
    return { init_url: '', segments: [best.fullUrl], standalone: true };
  }
  if (!inits.length) return null;
  if (!medias.length) return null;
  const init = mostRecent(inits);
  const ordered = orderAndValidateRanges(medias);
  if (!ordered) return null;
  return { init_url: init.fullUrl, segments: ordered.map((f) => f.fullUrl) };
}

function buildFmp4Set(candidates) {
  const media = (candidates || []).filter((c) => c.type !== 'manifest');
  if (!media.length) return { payload: null, error: INSUFFICIENT_FMP4_SET };
  const video = media.filter((c) => representationOf(c) === 'video');
  const audio = media.filter((c) => representationOf(c) === 'audio');
  const videoPlan = planRepresentation(video);
  if (!videoPlan) return { payload: null, error: INSUFFICIENT_FMP4_SET };
  let audioPlan = null;
  if (audio.length) {
    audioPlan = planRepresentation(audio);
    if (!audioPlan) return { payload: null, error: INSUFFICIENT_FMP4_SET };
  }
  return {
    payload: {
      init_url: videoPlan.init_url,
      segments: videoPlan.segments,
      audio_init_url: (audioPlan || {}).init_url || '',
      audio_segments: (audioPlan || {}).segments || [],
      has_audio: !!(audioPlan && audioPlan.init_url && audioPlan.segments.length),
    },
    error: null,
  };
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
  let url, mime = '', rt = '', fromCache = false, rangeHdr = null, contentRangeHdr = null;
  if (method === 'Network.requestWillBeSent') {
    const r = params && params.request;
    url = r && r.url; rt = (params && params.type) || '';
    const h = (r && r.headers) || {};
    rangeHdr = h['Range'] || h['range'] || null;
  } else if (method === 'Network.responseReceived') {
    const r = params && params.response;
    url = r && r.url; mime = (r && r.mimeType) || '';
    fromCache = !!(r && (r.fromDiskCache || r.fromMemoryCache));
    const h = (r && r.headers) || {};
    contentRangeHdr = h['Content-Range'] || h['content-range'] || null;
  }
  if (!url) return;
  const reason = classifyRequest(url, mime, rt);
  mem.counts[reason] = (mem.counts[reason] || 0) + 1;
  if (reason === 'accepted') {
    const key = redact(url);
    const type = classifyMedia(url) || 'media';

    let range = null, rangeStart = null, rangeEnd = null, rangeTotal = null;
    if (rangeHdr) {
      range = rangeHdr;
      const pr = parseRangeHeader(rangeHdr);
      if (pr) { rangeStart = pr[0]; rangeEnd = pr[1]; }
    }
    if (contentRangeHdr) {
      const pc = parseContentRange(contentRangeHdr);
      if (pc) { rangeStart = pc[0]; rangeEnd = pc[1]; rangeTotal = pc[2]; }
    }

    // Distinct ranged requests: same base URL + different byte range -> distinct.
    const idKey = key + '#' + ((rangeStart !== null && rangeStart !== undefined) ? rangeStart : 'none');
    let c = mem.accepted.find((a) => a.idKey === idKey);
    if (!c) {
      c = { idKey, fullUrl: url, host: new URL(url).hostname, type,
            mimeType: mime || null, fromCache,
            range, rangeStart, rangeEnd, rangeTotal,
            order: mem.accepted.length };
      mem.accepted.push(c);
      mem.candidates.push(persistedCandidate(c));
    } else {
      if (mime) c.mimeType = mime;
      if (fromCache) c.fromCache = true;
      if (rangeHdr) c.range = rangeHdr;
      if (contentRangeHdr) {
        const pc = parseContentRange(contentRangeHdr);
        if (pc) { c.rangeStart = pc[0]; c.rangeEnd = pc[1]; c.rangeTotal = pc[2]; }
      }
      const pi = mem.candidates.findIndex((p) => p.redacted === key && p.rangeStart === c.rangeStart);
      if (pi >= 0) mem.candidates[pi] = persistedCandidate(c);
    }

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
  // A DASH/HLS manifest early-exits — it fully describes init + ordered segments.
  const manifest = mostRecent(mem.accepted.filter((c) => c.type === 'manifest'));
  if (manifest) {
    acquireSent = true;
    mem.fullCandidate = manifest.fullUrl;
    onHandoff({ manifest_url: manifest.fullUrl });
    return;
  }
  // NO early exit on an ambiguous .mp4 / direct-video request — collect the
  // bounded window, then assemble a validated fMP4 set at timeout.
  if (!timedOut) return;

  const set = buildFmp4Set(mem.accepted);
  if (set.payload) {
    acquireSent = true;
    mem.fullCandidate = set.payload.init_url || set.payload.segments[0] || null;
    onHandoff({
      init_url: set.payload.init_url,
      segments: set.payload.segments,
      audio_init_url: set.payload.audio_init_url,
      audio_segments: set.payload.audio_segments,
    });
    return;
  }
  // Fallback: a single ambiguous/standalone candidate -> helper byte-signature
  // classifies ftyp (standalone, keep) vs moof (fragment, reject).
  const singles = mem.accepted.filter((c) => c.type === 'mp4' || c.type === 'direct_video');
  if (singles.length === 1) {
    acquireSent = true;
    mem.fullCandidate = singles[0].fullUrl;
    onHandoff({ candidate_url: singles[0].fullUrl, media_type: singles[0].type });
    return;
  }
  finishAcquire('failed', INSUFFICIENT_FMP4_SET + ': no assemblable fMP4 set (missing init/segments or invalid byte ranges)');
}

async function onHandoff(payload) {
  stopTimer();
  clearPhaseTimers();
  await detach();
  mem.deadline = null;
  await persist('sending', {});
  phaseTimers.push(setTimeout(() => { if (acquireMode) persist('downloading', {}); }, 1500));
  phaseTimers.push(setTimeout(() => { if (acquireMode) persist('assembling', {}); }, 4500));
  phaseTimers.push(setTimeout(() => { if (acquireMode) persist('validating', {}); }, 7000));
  await postToHelper(payload);
}

function buildBody(payload) {
  const body = { canonical_url: acquireCanonical, actor: ACTOR };
  if (payload.manifest_url) body.manifest_url = payload.manifest_url;
  if (payload.init_url) body.init_url = payload.init_url;
  if (payload.segments && payload.segments.length) body.segments = payload.segments;
  if (payload.audio_init_url) body.audio_init_url = payload.audio_init_url;
  if (payload.audio_segments && payload.audio_segments.length) body.audio_segments = payload.audio_segments;
  if (payload.candidate_url) body.candidate_url = payload.candidate_url;
  return body;
}

async function postToHelper(payload) {
  try {
    const resp = await fetch(HELPER_URL + '/acquire', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + HELPER_TOKEN },
      body: JSON.stringify(buildBody(payload)),
    });
    let data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (data && data.ok && data.status === 'acquired') {
      mem.result = data;
      await finishAcquire('saved', undefined);
    } else if (data && data.ok && data.status === 'duplicate') {
      mem.result = data;
      await finishAcquire('duplicate', 'already submitted — no new download');
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

        // Cached/autoplay: only if NO media request appeared within ~2.5s,
        // stimulate a fresh load via a cache-bypass reload of the SAME content
        // (content-ID locked by the onUpdated handler; re-inspected via onDetach).
        // We never reload mid-collection — that would disrupt ranged requests.
        if (acquireMode) {
          phaseTimers.push(setTimeout(() => {
            if (acquireMode && !acquireSent && mem.accepted.length === 0) {
              chrome.debugger.sendCommand({ tabId }, 'Page.reload', { ignoreCache: true }, () => {});
            }
          }, 2500));
        }
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
