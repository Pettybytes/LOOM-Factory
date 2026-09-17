"""Deterministic tests for local-helper acquisition logic (no real download).

Run:  python3 -m unittest discover -s infra/local-helper/tests -v
"""

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for d in ("local-helper", "acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / d))

import acquire as acq  # noqa: E402


# --- fake opener/response for download tests (no network) ---
class _FakeResponse:
    def __init__(self, chunks, status=200, content_type="video/mp4"):
        self._chunks = list(chunks)
        self._i = 0
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, n):
        if self._i >= len(self._chunks):
            return b""
        c = self._chunks[self._i]
        self._i += 1
        return c

    def close(self):
        pass


class _FakeOpener:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    def open(self, req, timeout=None):
        if self._exc:
            raise self._exc
        return self._resp


class _TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)


class TestDownload(_TempDir):
    URL = "https://scontent.cdninstagram.com/v/a.mp4"

    def test_success_returns_temp_and_bytes(self):
        resp = _FakeResponse([b"hello", b" world"])
        path, written, status, ct = acq.download_one_video(self.URL, self._tmp.name,
                                                           max_bytes=100, timeout=5,
                                                           opener=_FakeOpener(resp))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(written, 11)
        self.assertEqual(status, 200)
        self.assertEqual(ct, "video/mp4")

    def test_byte_ceiling_cleans_temp(self):
        resp = _FakeResponse([b"x" * 100])
        with self.assertRaises(acq.DownloadError) as cm:
            acq.download_one_video(self.URL, self._tmp.name,
                                   max_bytes=10, timeout=5, opener=_FakeOpener(resp))
        self.assertEqual(cm.exception.code, "BYTE_LIMIT")
        # temp file cleaned (only the empty dir remains)
        self.assertEqual(os.listdir(self._tmp.name), [])

    def test_timeout_cleans_temp(self):
        with self.assertRaises(acq.DownloadError) as cm:
            acq.download_one_video(self.URL, self._tmp.name,
                                   max_bytes=100, timeout=5,
                                   opener=_FakeOpener(exc=socket.timeout("timed out")))
        self.assertEqual(cm.exception.code, "DOWNLOAD_FAILED")
        self.assertEqual(os.listdir(self._tmp.name), [])

    def test_redirect_off_allowlist_rejected(self):
        # validate_redirect rejects a hop outside the approved hosts.
        from validate import validate_redirect, ValidationError
        with self.assertRaises(ValidationError):
            validate_redirect("https://evil.com/x.mp4")


class TestParseFfprobe(unittest.TestCase):
    def _valid(self):
        return {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 1136,
                 "avg_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "4.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        }

    def test_valid(self):
        m = acq.parse_ffprobe_result(self._valid(), actual_size_bytes=1000,
                                     max_bytes=100 * 1024 * 1024, max_duration=300)
        self.assertEqual(m["duration_seconds"], 4.5)
        self.assertTrue(m["has_audio"])
        self.assertEqual(m["width"], 640)
        self.assertEqual(m["frame_rate"], 30.0)

    def test_no_video(self):
        j = {"streams": [{"codec_type": "audio"}], "format": {"duration": "1"}}
        with self.assertRaises(acq.DownloadError) as cm:
            acq.parse_ffprobe_result(j, actual_size_bytes=10, max_bytes=100, max_duration=300)
        self.assertEqual(cm.exception.code, "NO_VIDEO")

    def test_duration_limit(self):
        j = self._valid()
        j["format"]["duration"] = "301"
        with self.assertRaises(acq.DownloadError) as cm:
            acq.parse_ffprobe_result(j, actual_size_bytes=10, max_bytes=100, max_duration=300)
        self.assertEqual(cm.exception.code, "DURATION_LIMIT")

    def test_size_limit(self):
        with self.assertRaises(acq.DownloadError) as cm:
            acq.parse_ffprobe_result(self._valid(), actual_size_bytes=1000,
                                     max_bytes=100, max_duration=300)
        self.assertEqual(cm.exception.code, "SIZE_LIMIT")

    def test_honest_absence_of_audio(self):
        j = self._valid()
        j["streams"] = [s for s in j["streams"] if s["codec_type"] != "audio"]
        m = acq.parse_ffprobe_result(j, actual_size_bytes=1000,
                                     max_bytes=100 * 1024 * 1024, max_duration=300)
        self.assertFalse(m["has_audio"])


class TestChecksum(_TempDir):
    def test_sha256(self):
        import hashlib
        p = os.path.join(self._tmp.name, "f.bin")
        with open(p, "wb") as f:
            f.write(b"abc")
        self.assertEqual(acq.checksum_sha256(p), hashlib.sha256(b"abc").hexdigest())


class TestPayloads(unittest.TestCase):
    URL = "https://www.instagram.com/reel/DdZS1QRhrXE"

    def test_acquisition_payload_no_signed_url(self):
        job = acq.build_acquisition_payload(
            canonical_url=self.URL, candidate_host="https://scontent.cdninstagram.com/v/a.mp4?sig=SECRET",
            actor="bryan", max_bytes=100, max_duration=300)
        self.assertEqual(job["authorization_type"], "acquire_for_reaction")
        self.assertEqual(job["destination"], "personal_reaction_library")
        self.assertEqual(job["adapter"], "chrome_network_capture")
        self.assertEqual(job["status"], "running")
        # signed query string must NOT appear anywhere in the payload
        blob = str(job)
        self.assertNotIn("sig=SECRET", blob)

    def test_duplicate_clicks_same_dedup_key(self):
        j1 = acq.build_acquisition_payload(canonical_url=self.URL, candidate_host="h",
                                           actor="bryan", max_bytes=100, max_duration=300)
        j2 = acq.build_acquisition_payload(canonical_url=self.URL, candidate_host="h",
                                           actor="bryan", max_bytes=100, max_duration=300)
        self.assertEqual(j1["dedup_key"], j2["dedup_key"])

    def test_library_entry(self):
        rec = acq.build_library_entry(
            canonical_url=self.URL, filename="instagram__DdZS1QRhrXE__abc.mp4",
            digest="a" * 64, size_bytes=1000, duration_seconds=4.5,
            content_type="video/mp4", local_path="/tmp/x.mp4", job_id="j")
        self.assertTrue(rec["is_non_production"])
        self.assertEqual(rec["asset_hash"], "a" * 64)
        self.assertEqual(rec["destination"], "personal_reaction_library")

    def test_library_filename_deterministic(self):
        f1 = acq.library_filename(self.URL, "a" * 64)
        f2 = acq.library_filename(self.URL, "a" * 64)
        self.assertEqual(f1, f2)
        self.assertTrue(f1.startswith("instagram__DdZS1QRhrXE__"))


class TestSignature(_TempDir):
    def _write(self, data):
        p = os.path.join(self._tmp.name, "f.bin")
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_ftyp(self):
        self.assertEqual(acq.read_signature(self._write(b"\x00\x00\x00\x18ftypmp42")), "ftyp")

    def test_moof(self):
        self.assertEqual(acq.read_signature(self._write(b"\x00\x00\x02\xbcmoof\x00\x00\x00\x10mfhd")), "moof")

    def test_styp(self):
        self.assertEqual(acq.read_signature(self._write(b"\x00\x00\x00\x18stypmsdh")), "styp")

    def test_html(self):
        self.assertEqual(acq.read_signature(self._write(b"<!DOCTYPE html><html></html>")), "html")

    def test_empty(self):
        self.assertEqual(acq.read_signature(self._write(b"")), "empty")


class TestClassifyDownloadedFile(_TempDir):
    def _write(self, data):
        p = os.path.join(self._tmp.name, "f.bin")
        with open(p, "wb") as f:
            f.write(data)
        return p

    def _mp4(self):
        p = os.path.join(self._tmp.name, "f.mp4")
        with open(p, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42")
        return p

    def test_ok(self):
        self.assertEqual(acq.classify_downloaded_file(self._mp4(), http_status=200,
                                                       content_type="video/mp4", size_bytes=8), "ok")

    def test_http_status(self):
        with self.assertRaises(acq.DownloadError) as cm:
            acq.classify_downloaded_file(self._mp4(), http_status=403,
                                         content_type="video/mp4", size_bytes=8)
        self.assertEqual(cm.exception.code, "HTTP_STATUS")

    def test_html_content_type(self):
        with self.assertRaises(acq.DownloadError) as cm:
            acq.classify_downloaded_file(self._mp4(), http_status=200,
                                         content_type="text/html", size_bytes=8)
        self.assertEqual(cm.exception.code, "HTML_RESPONSE")

    def test_fragmented_mp4(self):
        p = self._write(b"\x00\x00\x02\xbcmoof\x00\x00\x00\x10mfhd")
        with self.assertRaises(acq.DownloadError) as cm:
            acq.classify_downloaded_file(p, http_status=200,
                                         content_type="video/mp4", size_bytes=12)
        self.assertEqual(cm.exception.code, "FRAGMENTED_MP4")

    def test_zero_byte(self):
        p = self._write(b"")
        with self.assertRaises(acq.DownloadError) as cm:
            acq.classify_downloaded_file(p, http_status=200,
                                         content_type="video/mp4", size_bytes=0)
        self.assertEqual(cm.exception.code, "ZERO_BYTE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
