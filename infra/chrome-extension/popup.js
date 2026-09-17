// LOOM Source Capture — popup logic.
// Reads the persisted inspection state from chrome.storage.session (never null),
// renders explicit phases + a 12s countdown + attached/detached status, and
// offers: stage-1 page capture, stage-2 network probe, and attach-then-reload.

const out = document.getElementById('out');
const statusEl = document.getElementById('status');
const cancelBtn = document.getElementById('cancel');

const PHASE_LABEL = {
  ready: 'Ready',
  attaching: 'Attaching…',
  active: 'Inspection active — replay/reload the Reel now',
  collecting: 'Collecting…',
  complete: 'Complete',
  no_traffic: 'No new media traffic observed',
  failed: 'Failed',
};

function fmtCountdown(state) {
  if (!state || !state.deadline) return '';
  const sec = Math.max(0, Math.ceil((state.deadline - Date.now()) / 1000));
  return sec + 's';
}

function render(state) {
  state = state || { phase: 'ready', attached: false, candidates: [], counts: {}, error: null };
  const label = PHASE_LABEL[state.phase] || state.phase;
  let line = label;
  if (state.phase === 'failed' && state.error) line += ': ' + state.error;
  const cd = fmtCountdown(state);
  const attached = state.attached ? 'attached' : 'detached';
  statusEl.textContent = line + (cd ? ' · ' + cd : '') + ' · ' + attached;
  statusEl.className = (state.attached || state.phase === 'attaching') ? 'active' : '';
  cancelBtn.hidden = !(state.attached || state.phase === 'attaching');

  if (state.phase === 'complete' || state.phase === 'no_traffic') {
    renderResult(state);
  } else if (state.phase === 'active' || state.phase === 'collecting' || state.phase === 'attaching') {
    out.textContent = JSON.stringify({ liveCandidateCount: (state.candidates || []).length }, null, 2);
  }
}

function renderResult(state) {
  const c = state.candidates || [];
  const hosts = [...new Set(c.map((x) => x.host))];
  const types = [...new Set(c.map((x) => x.type))];
  out.textContent = JSON.stringify({
    result: state.phase === 'no_traffic' ? 'no_media_traffic_observed' : 'complete',
    candidateCount: c.length,
    candidateHostnames: hosts,
    inferredMediaTypes: types,
    summary: {
      directMp4: types.includes('direct_video'),
      manifestMpdM3u8: types.includes('manifest'),
      segmented: types.includes('segment'),
      separateAudioVideo: types.includes('segment') && types.includes('audio'),
    },
    ffmpegMuxRequired: types.includes('segment') || types.includes('manifest') || types.includes('audio'),
    rejectionCounts: state.counts || {},
    candidates: c, // redacted (no signed query strings)
  }, null, 2);
}

async function refresh() {
  const o = await chrome.storage.session.get('inspection');
  const s = o.inspection;
  render(s);
  if (s && (s.attached || s.phase === 'attaching')) setTimeout(refresh, 500);
}

function captureStage1() {
  const v = document.querySelector('video');
  const pageUrl = location.href;
  const src = v ? (v.currentSrc || v.src || '') : '';
  const isBlob = src.startsWith('blob:');
  const redact = (u) => { try { const x = new URL(u); return x.protocol + '//' + x.host + x.pathname; } catch { return '(unparseable)'; } };
  const mediaResourceEntries = (performance.getEntriesByType('resource') || [])
    .map((e) => e.name)
    .filter((n) => /^https:\/\//.test(n))
    .filter((n) => /\.(mp4|m4v|webm|m3u8|mpd|m4s|fmp4|ts|m4a|aac|mp3)(\?|$)/i.test(n.split('?')[0]))
    .map(redact)
    .slice(0, 60);
  const buffered = [];
  if (v && v.buffered) { for (let i = 0; i < v.buffered.length; i++) buffered.push([v.buffered.start(i), v.buffered.end(i)]); }
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
    note: isBlob ? 'Blob/MSE detected — direct source unavailable; network inspection required' : (src ? '' : 'No video element / no source yet'),
  };
}

document.getElementById('stage1').addEventListener('click', async () => {
  statusEl.textContent = '';
  statusEl.className = '';
  out.textContent = 'Capturing…';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: captureStage1 });
    out.textContent = JSON.stringify(results[0].result, null, 2);
  } catch (e) {
    out.textContent = 'Error: ' + (e && e.message ? e.message : e);
  }
});

async function start(reload) {
  out.textContent = reload ? 'Attaching, then reloading the Reel…' : 'Attaching… keep the Reel playing.';
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  chrome.runtime.sendMessage({ type: reload ? 'startAndReload' : 'startInspection', tabId: tab.id }, (resp) => {
    if (resp && resp.ok === false) {
      statusEl.textContent = 'Failed: ' + resp.error;
      statusEl.className = '';
    }
    refresh();
  });
}

document.getElementById('stage2').addEventListener('click', () => start(false));
document.getElementById('stage2reload').addEventListener('click', () => start(true));
cancelBtn.addEventListener('click', () => chrome.runtime.sendMessage({ type: 'stopInspection' }));

refresh();
