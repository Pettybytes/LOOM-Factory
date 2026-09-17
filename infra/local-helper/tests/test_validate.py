"""Deterministic tests for local-helper URL validation.

Run:  python3 -m unittest discover -s infra/local-helper/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "local-helper"))
sys.path.insert(0, str(_REPO_ROOT / "infra" / "acquisition"))

from validate import (  # noqa: E402
    ValidationError, validate_media_url, validate_redirect, validate_candidate,
    is_credential_url, has_non_standard_port,
)


class TestMediaUrl(unittest.TestCase):
    def test_https_required(self):
        with self.assertRaises(ValidationError) as cm:
            validate_media_url("http://scontent.cdninstagram.com/v/a.mp4")
        self.assertEqual(cm.exception.code, "BAD_SCHEME")

    def test_approved_host(self):
        self.assertEqual(validate_media_url("https://scontent-det1-1.cdninstagram.com/v/a.mp4"),
                         "https://scontent-det1-1.cdninstagram.com/v/a.mp4")

    def test_suffix_confusion_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            validate_media_url("https://cdninstagram.com.evil.com/v/a.mp4")
        self.assertEqual(cm.exception.code, "HOST_NOT_ALLOWED")

    def test_unapproved_host_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            validate_media_url("https://evil.com/v/a.mp4")
        self.assertEqual(cm.exception.code, "HOST_NOT_ALLOWED")

    def test_credentials_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            validate_media_url("https://user:pass@scontent.cdninstagram.com/v/a.mp4")
        self.assertEqual(cm.exception.code, "CREDENTIALS")

    def test_non_standard_port_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            validate_media_url("https://scontent.cdninstagram.com:8080/v/a.mp4")
        self.assertEqual(cm.exception.code, "BAD_PORT")

    def test_standard_ports_allowed(self):
        self.assertTrue(validate_media_url("https://scontent.cdninstagram.com/v/a.mp4"))
        self.assertTrue(validate_media_url("https://scontent.cdninstagram.com:443/v/a.mp4"))


class TestRedirect(unittest.TestCase):
    def test_redirect_to_approved_host(self):
        self.assertEqual(validate_redirect("https://video.cdninstagram.com/v/b.mp4"),
                         "https://video.cdninstagram.com/v/b.mp4")

    def test_redirect_outside_allowlist_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            validate_redirect("https://evil.com/b.mp4")
        self.assertEqual(cm.exception.code, "HOST_NOT_ALLOWED")


class TestCandidate(unittest.TestCase):
    def test_valid_pair(self):
        validate_candidate("https://www.instagram.com/reel/DdZS1QRhrXE",
                           "https://scontent.cdninstagram.com/v/a.mp4")

    def test_bad_source_scheme(self):
        with self.assertRaises(ValidationError) as cm:
            validate_candidate("http://www.instagram.com/reel/x",
                               "https://scontent.cdninstagram.com/v/a.mp4")
        self.assertEqual(cm.exception.code, "BAD_SOURCE")


class TestHelpers(unittest.TestCase):
    def test_is_credential_url(self):
        self.assertTrue(is_credential_url("https://u:p@host/x"))
        self.assertFalse(is_credential_url("https://host/x"))

    def test_has_non_standard_port(self):
        self.assertTrue(has_non_standard_port("https://host:8080/x"))
        self.assertFalse(has_non_standard_port("https://host/x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
