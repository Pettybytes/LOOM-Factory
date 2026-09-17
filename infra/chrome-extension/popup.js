// LOOM Source Capture — popup logic.
// Stage 1: read the active tab's <video> element + resource timeline (activeTab+scripting).
// Stage 2: user-initiated chrome.debugger network probe (bounded, auto-detach).

const out = document.getElementById('out');
const statusEl = document.getElementById('status');

function redact(u) {
  try { const x = new URL(u); return x.protocol + '//' + x.host + x.pathname; }
  catch { return '(unparseable)'; }
}

function captureStage1() {
  const v = document.querySelector('video');
  const pageUrl = location.href;
  const src = v ? (v.currentSrc || v.src || '') : '';
  const isBlob = src.startsWith('blob:');
  const mediaResourceEntries = (performance.getEntriesByType('resource') || [])
    .map((e) => e.name)
    .filter((n) => /^https:\/\//.test(n))
    .filter((n) => /\.(mp4|m4v|webm|m3u8|mpd|m4s|fmp4|ts|m4a|aac|mp3)(\?|$)/i.test(n.split('?')[0]))
    .map(redact)
    .slice(0, 60);
  const buffered = [];
  if (v && v.buffered) {
    for (let i = 0; i < v.buffered.length; i++) {
      buffered.push([v.buffered.start(i), v.buffered.end(i)]);
    }
  }
  const sources = v ? Array.from(v.querySelectorAll('source')).map((s) => s.src).filter(Boolean).map(redact) : [];
  return {
    found: !!v,
    pageUrl,
    videoSrc: src ? (isBlob ? 'blob:…(MSE)' : src) : '(empty)',
    isDirectUrl: /^https?:\/\//.test(src),
    isBlobOrMse: isBlob || src === '',
    bufferedRanges: buffered,
    sourceElements: sources,
    mediaResourceEntries,
    note: isBlob
      ? 'Blob/MSE detected — direct source unavailable; network inspection required'
      : (src ? '' : 'No video element / no source yet'),
  };
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error('Could not resolve the active tab');
  return tab;
}

document.getElementById('stage1').addEventListener('click', async () => {
  out.textContent = 'Capturing…';
  statusEl.textContent = '';
  try {
    const tab = await getActiveTab();
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: captureStage1 });
    out.textContent = JSON.stringify(results[0].result, null, 2);
  } catch (e) {
    out.textContent = 'Error: ' + (e && e.message ? e.message : e);
  }
});

document.getElementById('stage2').addEventListener('click', async () => {
  statusEl.textContent = '';
  statusEl.className = '';
  try {
    const tab = await getActiveTab();
    out.textContent = 'Starting bounded network inspection (12s)… keep the Reel playing.';
    chrome.runtime.sendMessage({ type: 'startInspection', tabId: tab.id }, (resp) => {
      if (resp && resp.ok === false) {
        statusEl.textContent = 'Inspection failed: ' + resp.error;
        out.textContent = JSON.stringify(resp, null, 2);
        return;
      }
      poll();
    });
  } catch (e) {
    out.textContent = 'Error: ' + (e && e.message ? e.message : e);
  }
});

function poll() {
  chrome.runtime.sendMessage({ type: 'getStatus' }, (resp) => {
    if (!resp) { statusEl.textContent = 'Inspection ended (no response).'; return; }
    if (resp.active) {
      statusEl.textContent = 'Inspection active… (' + (resp.candidates || []).length + ' candidate(s))';
      statusEl.className = 'active';
      setTimeout(poll, 750);
    } else {
      statusEl.textContent = '';
      statusEl.className = '';
      renderResults(resp.candidates || []);
    }
  });
}

function renderResults(candidates) {
  if (!candidates.length) {
    out.textContent = JSON.stringify({ result: 'no media candidates', candidates: [] }, null, 2);
    return;
  }
  const hosts = [...new Set(candidates.map((c) => c.host))];
  const types = [...new Set(candidates.map((c) => c.type))];
  const hasMuxedMp4 = types.includes('direct_video');
  const hasManifest = types.includes('manifest');
  const hasSegments = types.includes('segment');
  const hasAudio = types.includes('audio');
  out.textContent = JSON.stringify({
    candidateCount: candidates.length,
    candidateHostnames: hosts,
    inferredMediaTypes: types,
    summary: {
      directMp4: hasMuxedMp4,
      manifestMpdM3u8: hasManifest,
      segmented: hasSegments,
      separateAudioVideo: hasSegments && hasAudio,
    },
    ffmpegMuxRequired: hasSegments || hasManifest || hasAudio,
    candidates: candidates, // redacted already (no signed query strings)
  }, null, 2);
}
