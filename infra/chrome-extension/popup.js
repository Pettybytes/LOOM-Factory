// LOOM Source Capture — popup logic.
// Reads persisted inspection state from chrome.storage.session (never null),
// renders explicit phases + countdown, and offers:
//   Acquire for Reaction (primary), Capture page media, Inspect Playing Media,
//   Inspect + reload Reel, Cancel.

const out = document.getElementById('out');
const statusEl = document.getElementById('status');
const cancelBtn = document.getElementById('cancel');

const PHASE_LABEL = {
  ready: 'Ready',
  attaching: 'Attaching…',
  active: 'Inspecting playing media…',
  collecting: 'Inspecting playing media…',
  found: 'Media found',
  sending: 'Sending to local LOOM',
  downloading: 'Downloading within limits',
  validating: 'Validating',
  complete: 'Complete',
  no_traffic: 'No new media traffic observed',
  saved: 'Saved to Reaction Library',
  duplicate: 'Already submitted (duplicate)',
  failed: 'Failed',
};

const ACTIVE_PHASES = ['attaching', 'active', 'collecting', 'found', 'sending', 'downloading', 'validating'];

function fmtCountdown(state) {
  if (!state || !state.deadline) return '';
  const sec = Math.max(0, Math.ceil((state.deadline - Date.now()) / 1000));
  return sec + 's';
}

function render(state) {
  state = state || { phase: 'ready', attached: false, candidates: [], counts: {}, result: null, error: null };
  const label = PHASE_LABEL[state.phase] || state.phase;
  let line = label;
  if (state.phase === 'failed' && state.error) line += ': ' + state.error;
  const cd = fmtCountdown(state);
  if (state.attached || state.phase === 'attaching') line += ' · ' + (state.attached ? 'attached' : 'detached');
  statusEl.textContent = line + (cd ? ' · ' + cd : '');
  statusEl.className = (state.attached || ACTIVE_PHASES.includes(state.phase)) ? 'active' : '';
  cancelBtn.hidden = !(state.attached || state.phase === 'attaching');

  if (state.phase === 'saved') {
    renderSaved(state.result);
  } else if (state.phase === 'duplicate') {
    out.textContent = JSON.stringify({ status: 'duplicate', submissionId: (state.result && state.result.submission_id) || null, note: 'already submitted — no new download' }, null, 2);
  } else if (state.phase === 'complete' || state.phase === 'no_traffic') {
    renderResult(state);
  } else if (ACTIVE_PHASES.includes(state.phase)) {
    out.textContent = JSON.stringify({ phase: state.phase, liveCandidateCount: (state.candidates || []).length }, null, 2);
  }
}

function renderSaved(result) {
  const r = result || {};
  const lib = r.library || {};
  out.textContent = JSON.stringify({
    status: r.status || 'acquired',
    destination: r.destination,
    routing: r.routing,
    adapter: r.adapter,
    submissionId: r.submission_id,
    library: {
      filename: lib.filename,
      size_bytes: lib.size_bytes,
      duration_seconds: lib.duration_seconds,
      width: lib.width,
      height: lib.height,
      frame_rate: lib.frame_rate,
      has_audio: lib.has_audio,
      sha256: lib.sha256,
      obsReadyPath: lib.local_path,
    },
  }, null, 2);
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
    rejectionCounts: state.counts || {},
    candidates: c,
  }, null, 2);
}

async function refresh() {
  const o = await chrome.storage.session.get('inspection');
  const s = o.inspection;
  render(s);
  if (s && (s.attached || ACTIVE_PHASES.includes(s.phase))) setTimeout(refresh, 500);
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
    .map(redact).slice(0, 60);
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

async function start(type) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (type === 'acquire') {
    out.textContent = 'Acquiring for reaction…';
    chrome.runtime.sendMessage({ type: 'acquireForReaction', tabId: tab.id, canonicalUrl: tab.url || '' }, (resp) => {
      if (resp && resp.ok === false) { statusEl.textContent = 'Failed: ' + resp.error; statusEl.className = ''; }
      refresh();
    });
  } else {
    out.textContent = (type === 'reload') ? 'Attaching, then reloading the Reel…' : 'Attaching… keep the Reel playing.';
    chrome.runtime.sendMessage({ type: type === 'reload' ? 'startAndReload' : 'startInspection', tabId: tab.id }, (resp) => {
      if (resp && resp.ok === false) { statusEl.textContent = 'Failed: ' + resp.error; statusEl.className = ''; }
      refresh();
    });
  }
}

document.getElementById('acquire').addEventListener('click', () => start('acquire'));
document.getElementById('stage2').addEventListener('click', () => start('inspect'));
document.getElementById('stage2reload').addEventListener('click', () => start('reload'));
cancelBtn.addEventListener('click', () => chrome.runtime.sendMessage({ type: 'stopInspection' }));

refresh();
