// LOOM Source Capture — popup logic.
// Injects a read-only capture function into the ACTIVE tab (Instagram only)
// and reports whether the playing Reel exposes a DIRECT media URL or a blob:/MSE
// source. No cookies, history, or passwords are ever read. Nothing is downloaded.

function capture() {
  const pageUrl = location.href;
  const v = document.querySelector('video');
  if (!v) {
    return { found: false, reason: 'no <video> element found', pageUrl };
  }
  const src = v.currentSrc || v.src || '';
  const isDirect = /^https?:\/\//.test(src);
  const sources = Array.from(v.querySelectorAll('source'))
    .map((s) => s.src || '')
    .filter(Boolean);
  return {
    found: true,
    pageUrl,
    videoSrc: src || '(empty)',
    isDirectUrl: isDirect,
    isBlobOrMse: src.startsWith('blob:') || src === '',
    poster: v.poster || '',
    sources,
  };
}

document.getElementById('capture').addEventListener('click', async () => {
  const out = document.getElementById('out');
  out.textContent = 'Capturing…';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      out.textContent = 'Could not resolve the active tab.';
      return;
    }
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: capture,
    });
    const r = results && results[0] && results[0].result;
    out.textContent = JSON.stringify(r, null, 2);
  } catch (e) {
    out.textContent = 'Error: ' + (e && e.message ? e.message : e);
  }
});
