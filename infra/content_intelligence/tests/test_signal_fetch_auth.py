"""The signal/media fetch must use the SAME auth args as the preview path.

Root cause: fetch_media_and_signals built its yt-dlp command without _auth_args(),
so credentialed sources failed and every signal-dependent field (hook, payoff,
action_activity, reaction_resolution) stayed null while confidence sat at 22.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import content_intelligence.pipeline as pl  # noqa: E402


class _Proc:
    returncode = 0
    stderr = ""
    stdout = ""


class SignalFetchAuthTests(unittest.TestCase):
    def test_signal_fetch_uses_shared_auth_args(self):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = list(cmd)
            return _Proc()

        # the import is FUNCTION-LOCAL, so the patch target is the defining module
        with mock.patch("content_intelligence.bounded_preview._auth_args",
                        lambda: ["--AUTH-SENTINEL"]), \
             mock.patch("subprocess.run", fake_run):
            try:
                pl.fetch_media_and_signals("https://example.invalid/x")
            except Exception:
                pass            # no media produced by the fake; we assert on the COMMAND
        self.assertIn("--AUTH-SENTINEL", captured.get("cmd", []))

    def test_auth_args_are_imported_not_redefined(self):
        """The args must come from bounded_preview so both media paths stay in step."""
        import inspect
        src = inspect.getsource(pl.fetch_media_and_signals)
        self.assertIn("from .bounded_preview import _auth_args", src)
        self.assertNotIn("def _auth_args", src)      # never redefined locally

    def test_command_stays_bounded(self):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = list(cmd)
            return _Proc()

        with mock.patch("subprocess.run", fake_run):
            try:
                pl.fetch_media_and_signals("https://example.invalid/x", max_duration=600)
            except Exception:
                pass
        cmd = captured.get("cmd", [])
        self.assertIn("--match-filter", cmd)          # duration bound preserved
        self.assertIn("worstvideo+worstaudio/worst", cmd)   # lowest quality preserved


if __name__ == "__main__":
    unittest.main()
