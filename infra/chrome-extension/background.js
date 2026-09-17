// LOOM Source Capture — background service worker.
// User-initiated, bounded chrome.debugger Network probe (CDP).
// Safety: attach ONLY on explicit user click; active tab only; bounded 12s
// window; auto-detach on timeout/navigation/close; never reads request/response
// bodies, cookies, auth headers, or unrelated traffic; redacts signed tokens.

let activeTabId = null;
let collected = [];
let timer = null;

const EXCLUDE_MIME = /image\/|text\/html|text\/css|application\/javascript|application\/json/;
const MEDIA_MIME = /video\/|audio\/|mpegurl|dash\+xml|octet-stream/;

function isApprovedHost(hostname) {
  const h = (hostname || '').toLowerCase().replace(/\.$/, '');
  const exact = ['instagram.com', 'cdninstagram.com', 'fbcdn.net', 'akamaihd.net'];
  const suffixes = ['.cdninstagram.com', '.fbcdn.net', '.akamaihd.net', '.instagram.com'];
  return exact.includes(h) || suffixes.some((s) => h.endsWith(s));
}

function redact(url) {
  try { const u = new URL(url); return u.protocol + '//' + u.host + u.pathname; }
  catch { return '(unparseable)'; }
}

function classify(url) {
  const path = (url.split('?')[0] || '').toLowerCase();
  if (/\.(mp4|m4v|webm)$/.test(path)) return 'direct_video';
  if (/\.(m3u8|mpd)$/.test(path)) return 'manifest';
  if (/\.(m4s|fmp4|ts)$/.test(path)) return 'segment';
  if (/\.(m4a|aac|mp3|ogg|opus)$/.test(path)) return 'audio';
  return null;
}

function isCandidate(url, mimeType) {
  if (!/^https:\/\//i.test(url)) return false;
  try { if (!isApprovedHost(new URL(url).hostname)) return false; } catch { return false; }
  const mt = (mimeType || '').toLowerCase();
  if (mt) {
    if (EXCLUDE_MIME.test(mt)) return false;
    if (MEDIA_MIME.test(mt)) return true;
  }
  return classify(url) !== null;
}

function finish() {
  if (timer) { clearTimeout(timer); timer = null; }
  if (activeTabId !== null) {
    try { chrome.debugger.detach({ tabId: activeTabId }); } catch (e) {}
    activeTabId = null;
  }
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (source.tabId !== activeTabId) return; // only our tab
  if (method === 'Network.requestWillBeSent') {
    const url = params && params.request && params.request.url;
    if (!url || !isCandidate(url)) return;
    const rec = { host: new URL(url).hostname, type: classify(url) || 'media', mimeType: null, redacted: redact(url) };
    collected.push(rec);
  } else if (method === 'Network.responseReceived') {
    const resp = params && params.response;
    const url = resp && resp.url;
    const mime = resp && resp.mimeType;
    if (!url || !isCandidate(url, mime)) return;
    const key = redact(url);
    const existing = collected.find((c) => c.redacted === key);
    if (existing) { existing.mimeType = mime; existing.type = existing.type || classify(url) || 'media'; }
    else collected.push({ host: new URL(url).hostname, type: classify(url) || 'media', mimeType: mime, redacted: key });
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'startInspection') {
    if (activeTabId !== null) { sendResponse({ ok: false, error: 'inspection already active' }); return; }
    const tabId = msg.tabId;
    activeTabId = tabId;
    collected = [];
    chrome.debugger.attach({ tabId }, '1.3', () => {
      if (chrome.runtime.lastError) {
        const err = chrome.runtime.lastError.message;
        activeTabId = null;
        sendResponse({ ok: false, error: err });
        return;
      }
      chrome.debugger.sendCommand({ tabId }, 'Network.enable', {}, () => {
        timer = setTimeout(finish, 12000);
        sendResponse({ ok: true });
      });
    });
    return true; // async
  }
  if (msg.type === 'stopInspection') { finish(); sendResponse({ ok: true }); return; }
  if (msg.type === 'getStatus') {
    sendResponse({
      active: activeTabId !== null,
      candidates: collected.map((c) => ({ host: c.host, type: c.type, mimeType: c.mimeType, redacted: c.redacted })),
    });
    return;
  }
});

// Auto-detach on tab close / navigation.
chrome.tabs.onRemoved.addListener((tabId) => { if (tabId === activeTabId) finish(); });
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (tabId === activeTabId && changeInfo.status === 'loading') finish();
});
