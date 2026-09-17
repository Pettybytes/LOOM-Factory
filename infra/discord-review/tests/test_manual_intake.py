"""Deterministic tests for Manual Source Intake V1 (URL validation + dedup + status).

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "discord-review"))

from manual_intake import (  # noqa: E402
    SubmissionError,
    validate_and_canonicalize,
    canonicalize_url,
    submission_fingerprint,
    status_for_platform,
    build_submission,
    validate_destination,
    normalize_destination,
    destination_choices,
    routing_label,
    platform_support_label,
    STATUS_ACCEPTED,
    STATUS_PENDING_PLATFORM,
    STATUS_AWAITING_TENANT,
    DESTINATION_UNASSIGNED,
    CONTENT_FORMATS,
)


class TestValidation(unittest.TestCase):
    def test_youtube_watch(self):
        v = validate_and_canonicalize("https://www.youtube.com/watch?v=qgLaCZyKv_8&si=abc&utm_source=foo")
        self.assertEqual(v["platform"], "youtube")
        self.assertEqual(v["content_id"], "qgLaCZyKv_8")
        self.assertEqual(v["status"], STATUS_ACCEPTED)

    def test_youtu_be_canonicalized(self):
        v = validate_and_canonicalize("https://youtu.be/qgLaCZyKv_8?si=x")
        self.assertEqual(v["canonical_url"], "https://www.youtube.com/watch?v=qgLaCZyKv_8")
        self.assertEqual(v["platform"], "youtube")

    def test_reddit(self):
        v = validate_and_canonicalize("https://www.reddit.com/r/technology/comments/1abc123/title/")
        self.assertEqual(v["platform"], "reddit")
        self.assertEqual(v["content_id"], "1abc123")
        self.assertEqual(v["status"], STATUS_ACCEPTED)

    def test_pending_platforms(self):
        for url, plat in [
            ("https://www.tiktok.com/@user/video/7123456789012345678", "tiktok"),
            ("https://www.instagram.com/reel/ABC123/", "instagram"),
            ("https://clips.twitch.tv/FunnyClip123", "twitch"),
            ("https://kick.com/clip/abc123", "kick"),
        ]:
            v = validate_and_canonicalize(url)
            self.assertEqual(v["platform"], plat)
            self.assertEqual(v["status"], STATUS_PENDING_PLATFORM)


class TestRejections(unittest.TestCase):
    def _rejects(self, url, code):
        with self.assertRaises(SubmissionError) as cm:
            validate_and_canonicalize(url)
        self.assertEqual(cm.exception.code, code)

    def test_bad_scheme(self):
        self._rejects("file:///etc/passwd", "BAD_SCHEME")
        self._rejects("gopher://youtube.com/", "BAD_SCHEME")

    def test_credentials(self):
        self._rejects("https://user:pass@youtube.com/watch?v=x", "CREDENTIALS")

    def test_private_hosts(self):
        for url in [
            "https://127.0.0.1/",
            "https://localhost/x",
            "https://192.168.1.10/watch",
            "https://10.0.0.5/x",
            "https://172.16.0.1/x",
            "https://169.254.169.254/latest/meta-data/",
            "https://[::1]/",
            "https://youtube.com.local/x",
        ]:
            self._rejects(url, "PRIVATE_HOST")

    def test_unsupported_host(self):
        self._rejects("https://evil.com/watch?v=x", "UNSUPPORTED_HOST")
        self._rejects("https://youtube.com.evil.com/x", "UNSUPPORTED_HOST")

    def test_bad_port(self):
        self._rejects("https://youtube.com:8080/watch?v=x", "BAD_PORT")

    def test_malformed(self):
        self._rejects("", "EMPTY_URL")
        self._rejects("https://", "NO_HOST")
        self._rejects("not a url", "BAD_SCHEME")  # no scheme at all


class TestCanonicalization(unittest.TestCase):
    def test_strips_tracking(self):
        a = canonicalize_url("https://www.youtube.com/watch?v=abc&utm_source=x&si=y")
        b = canonicalize_url("https://www.youtube.com/watch?v=abc")
        self.assertEqual(a, b)
        self.assertNotIn("utm_source", a)
        self.assertNotIn("si", a.split("?")[-1])

    def test_strips_fragment_and_trailing_slash(self):
        self.assertEqual(
            canonicalize_url("https://www.reddit.com/r/x/comments/abc/#frag"),
            "https://www.reddit.com/r/x/comments/abc",
        )


class TestDedupAndStatus(unittest.TestCase):
    def test_fingerprint_deterministic(self):
        a = submission_fingerprint("youtube", "https://www.youtube.com/watch?v=abc")
        b = submission_fingerprint("youtube", "https://www.youtube.com/watch?v=abc")
        self.assertEqual(a, b)
        self.assertNotEqual(a, submission_fingerprint("reddit", "https://www.youtube.com/watch?v=abc"))

    def test_status_mapping(self):
        self.assertEqual(status_for_platform("youtube"), STATUS_ACCEPTED)
        self.assertEqual(status_for_platform("reddit"), STATUS_ACCEPTED)
        self.assertEqual(status_for_platform("tiktok"), STATUS_PENDING_PLATFORM)
        self.assertEqual(status_for_platform("twitch"), STATUS_PENDING_PLATFORM)


class TestBuildSubmission(unittest.TestCase):
    ACTIVE = {"dreemzy", "client-a"}

    def _build(self, **kw):
        base = dict(url="https://www.youtube.com/watch?v=abc", destination="dreemzy",
                    content_format="commentary_story", submitter="bryan#1",
                    active_tenants=self.ACTIVE)
        base.update(kw)
        return build_submission(**base)

    def test_valid_payload(self):
        p = self._build(reviewer_note="great moment", time_start_s=10.5, time_end_s=22.5)
        self.assertEqual(p["platform"], "youtube")
        self.assertEqual(p["status"], STATUS_ACCEPTED)
        self.assertEqual(p["content_format"], "commentary_story")
        self.assertEqual(p["qualification_required"], True)
        self.assertEqual(p["profile_id"], "")  # derived server-side, not asked
        self.assertEqual(len(p["duplicate_fingerprint"]), 64)

    def test_unassigned_submission(self):
        p = self._build(destination=DESTINATION_UNASSIGNED)
        self.assertEqual(p["tenant_id"], "")
        self.assertEqual(p["status"], STATUS_AWAITING_TENANT)

    def test_invalid_destination_rejected(self):
        with self.assertRaises(SubmissionError) as cm:
            self._build(destination="unknown-tenant")
        self.assertEqual(cm.exception.code, "INVALID_DESTINATION")

    def test_proof_destination_rejected(self):
        with self.assertRaises(SubmissionError) as cm:
            build_submission(url="https://www.youtube.com/watch?v=abc",
                             destination="proof-tenant", content_format="commentary_story",
                             submitter="b", active_tenants={"proof-tenant"})
        self.assertEqual(cm.exception.code, "INVALID_DESTINATION")

    def test_bad_format(self):
        with self.assertRaises(SubmissionError) as cm:
            self._build(content_format="nonsense")
        self.assertEqual(cm.exception.code, "BAD_FORMAT")

    def test_bad_time(self):
        with self.assertRaises(SubmissionError) as cm:
            self._build(time_start_s=50, time_end_s=10)
        self.assertEqual(cm.exception.code, "BAD_TIME")

    def test_all_formats_allowed(self):
        for f in CONTENT_FORMATS:
            p = self._build(content_format=f)
            self.assertEqual(p["content_format"], f)

    def test_validate_destination_unassigned(self):
        self.assertEqual(validate_destination("", self.ACTIVE), "")
        self.assertEqual(validate_destination("dreemzy", self.ACTIVE), "dreemzy")

    def test_validate_destination_inactive(self):
        with self.assertRaises(SubmissionError) as cm:
            validate_destination("inactive-tenant", self.ACTIVE)
        self.assertEqual(cm.exception.code, "INVALID_DESTINATION")


class TestDestinationUX(unittest.TestCase):
    ACTIVE = {"dreemzy", "client-a", "proof-tenant"}

    def test_normalize_destination(self):
        self.assertEqual(normalize_destination(""), "")
        self.assertEqual(normalize_destination("unassigned"), "")
        self.assertEqual(normalize_destination("UNASSIGNED"), "")
        self.assertEqual(normalize_destination("dreemzy"), "dreemzy")

    def test_destination_choices_leads_unassigned_and_excludes_proof(self):
        choices = destination_choices(self.ACTIVE)
        self.assertEqual(choices[0], {"name": "Unassigned / General", "value": "unassigned"})
        names = [c["name"] for c in choices]
        self.assertIn("dreemzy", names)
        self.assertIn("client-a", names)
        self.assertNotIn("proof-tenant", names)

    def test_routing_and_platform_labels(self):
        self.assertEqual(routing_label(""), "Awaiting tenant assignment")
        self.assertEqual(routing_label("dreemzy"), "Assigned to dreemzy")
        self.assertEqual(platform_support_label("youtube"), "Supported")
        self.assertEqual(platform_support_label("instagram"), "Pending Instagram support")
        self.assertEqual(platform_support_label("tiktok"), "Pending TikTok support")


if __name__ == "__main__":
    unittest.main(verbosity=2)
