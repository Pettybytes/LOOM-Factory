"""Disk-backed request coordination for the local CapCut project store."""
import fcntl
import hashlib
import json
import os
from pathlib import Path


def _refuse(code, message):
    return 409, {'ok': False, 'lane': 'capcut', 'error': code, 'reason': message}


def _save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def run_once(root, identity, build):
    """Serialize store writes and reuse a successful tenant/clip/asset result.

    flock covers threads AND processes and releases on process exit. The durable
    running marker survives a crash: an ambiguous export must be inspected,
    never retried automatically into a second project. Known 4xx refusals are
    retryable; an unexpected 5xx remains ambiguous. No session tokens are stored.
    """
    directory = Path(root).expanduser() / 'requests'
    directory.mkdir(parents=True, exist_ok=True)
    identity = list(identity)
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    receipt = directory / (key + '.json')
    with (directory / 'store.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _refuse('handoff_busy', 'A CapCut export is already running. Wait for it to finish, then retry.')
        try:
            if receipt.exists():
                try:
                    saved = json.loads(receipt.read_text())
                except (ValueError, OSError):
                    saved = {}
                if saved.get('identity') != identity or saved.get('state') not in ('complete', 'retryable'):
                    return _refuse('handoff_incomplete', 'The previous CapCut export was interrupted. Ask the operator to inspect it before retrying; no duplicate project was created.')
                if saved.get('state') == 'complete':
                    body = saved['body']
                    if not Path(body.get('draft_file') or '').is_file():
                        return _refuse('handoff_project_missing', 'This version was already exported, but its project has moved or is missing. Ask the operator to locate it before exporting again.')
                    return 200, {**body, 'replayed': True}
            _save(receipt, {'identity': identity, 'state': 'running'})
            status, body = build()
            if status == 200 and body.get('ok'):
                _save(receipt, {'identity': identity, 'state': 'complete', 'body': body})
            elif 400 <= status < 500:
                _save(receipt, {'identity': identity, 'state': 'retryable'})
            return status, body
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
