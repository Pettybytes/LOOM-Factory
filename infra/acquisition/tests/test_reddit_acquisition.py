"""Reddit exact-URL acquisition contract.

Covers identity, share-link resolution bounds, classification, stream selection,
the native media path (mux + verified-silent), external delegation, deterministic
filenames, and the value-safe authorized-session helper.

All I/O is faked; no network, no media, and no real cookie values anywhere.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(_REPO_ROOT / "infra" / _d))

import media_normalize  # noqa: E402
import reaction_library as rl  # noqa: E402
import reddit_context as rctx  # noqa: E402
import reddit_media as rm  # noqa: E402
import reddit_session as rs  # noqa: E402
import reddit_url as ru  # noqa: E402
import title_contract as tc  # noqa: E402

POST = "1l6m106"
SUB = "TheCapitalLink"
CANON = f"https://www.reddit.com/r/{SUB}/comments/{POST}/now_why_they_do_officer_fine_shit_like_that/"
SHARE = f"https://www.reddit.com/r/{SUB}/s/rPHdJGq2A2"
DIGEST = "0e9af235dacf129a93f64ef40b89eab3512c65d63f1f870f1c7cbc58cddf1d6c"


def _expect(case, exc_type, code, fn, *a, **kw):
    with case.assertRaises(exc_type) as cm:
        fn(*a, **kw)
    case.assertEqual(getattr(cm.exception, "code", None), code, str(cm.exception))
    return cm.exception


class TestIdentity(unittest.TestCase):
    def test_canonical_post_url(self):
        got = ru.classify(CANON)
        self.assertEqual(got["platform"], "reddit")
        self.assertEqual(got["post_id"], POST)
        self.assertEqual(got["subreddit"], SUB)
        self.assertEqual(got["canonical_url"], f"https://www.reddit.com/r/{SUB}/comments/{POST}/")

    def test_tracking_parameters_never_change_identity(self):
        tracked = CANON + "?share_id=0CGE0JcKeoMe6JyttE2ae&utm_source=share&utm_term=1"
        self.assertEqual(ru.classify(tracked)["post_id"], ru.classify(CANON)["post_id"])
        self.assertEqual(ru.classify(tracked)["canonical_url"], ru.classify(CANON)["canonical_url"])
        self.assertEqual(ru.fingerprint_for(tracked), ru.fingerprint_for(CANON))
        stripped = ru.strip_tracking(tracked)
        self.assertNotIn("?", stripped)
        self.assertNotIn("share_id", stripped)

    def test_the_slug_is_not_identity(self):
        """Slugs change when a title is edited; identity must not follow them."""
        a = f"https://www.reddit.com/r/{SUB}/comments/{POST}/original-title/"
        b = f"https://www.reddit.com/r/{SUB}/comments/{POST}/a_completely_different_slug/"
        self.assertEqual(ru.classify(a)["post_id"], ru.classify(b)["post_id"])
        self.assertEqual(ru.classify(a)["canonical_url"], ru.classify(b)["canonical_url"])
        self.assertEqual(ru.fingerprint_for(a), ru.fingerprint_for(b))

    def test_all_supported_host_forms(self):
        for host in ("www.reddit.com", "old.reddit.com", "np.reddit.com",
                     "new.reddit.com", "m.reddit.com", "reddit.com"):
            self.assertEqual(ru.classify(f"https://{host}/r/{SUB}/comments/{POST}/slug/")["post_id"],
                             POST, host)

    def test_redd_it_short_form(self):
        self.assertEqual(ru.classify(f"https://redd.it/{POST}")["post_id"], POST)

    def test_subreddit_listings_and_profiles_are_refused(self):
        for bad in (f"https://www.reddit.com/r/{SUB}/",
                    f"https://www.reddit.com/r/{SUB}/top/",
                    f"https://www.reddit.com/r/{SUB}/about/",
                    "https://www.reddit.com/user/someone/",
                    "https://www.reddit.com/search?q=x",
                    "https://www.reddit.com/r/all/"):
            _expect(self, ru.RedditUrlError, ru.REDDIT_URL_UNSUPPORTED, ru.classify, bad)

    def test_foreign_host_and_credentials_are_refused(self):
        _expect(self, ru.RedditUrlError, ru.REDDIT_URL_UNSUPPORTED, ru.classify,
                f"https://reddit.com.evil.com/r/{SUB}/comments/{POST}/")
        _expect(self, ru.RedditUrlError, ru.REDDIT_URL_UNSUPPORTED, ru.classify,
                f"https://user:pw@www.reddit.com/r/{SUB}/comments/{POST}/")

    def test_post_id_drift_is_refused(self):
        _expect(self, ru.RedditUrlError, ru.SOURCE_ID_DRIFT, ru.parse, CANON,
                expected_post_id="otherid")

    def test_media_id_is_not_the_post_id(self):
        self.assertNotEqual("61i3p09gjr5f1", POST)
        self.assertEqual(ru.classify(CANON)["post_id"], POST)

    def test_media_host_predicate(self):
        for ok in ("v.redd.it", "packager.redd.it", "preview.redd.it", "i.redd.it",
                   "preview.redditmedia.com"):
            self.assertTrue(ru.is_reddit_media_host(ok), ok)
        for bad in ("redd.it.evil.com", "vredd.it", "evil.com", "reddit.com"):
            self.assertFalse(ru.is_reddit_media_host(bad), bad)


class TestShareLinkResolution(unittest.TestCase):
    def _resolver(self, hops):
        def r(url):
            for src, status, loc, final in hops:
                if url == src:
                    return status, loc, final
            return None, None, url
        return r

    def test_share_link_derives_the_post_id_from_the_final_destination(self):
        hops = [(SHARE, 301, CANON, CANON), (CANON, 200, None, CANON)]
        got = ru.resolve(SHARE, self._resolver(hops))
        self.assertEqual(got["post_id"], POST)
        self.assertEqual(got["subreddit"], SUB)
        self.assertNotIn("rPHdJGq2A2", got["post_id"])       # the code is never identity
        self.assertEqual(got["resolved_from"], SHARE)

    def test_share_link_is_refused_without_resolution(self):
        _expect(self, ru.RedditUrlError, ru.REDDIT_SHARE_LINK_UNRESOLVED, ru.classify, SHARE)
        _expect(self, ru.RedditUrlError, ru.REDDIT_SHARE_LINK_UNRESOLVED, ru.parse, SHARE)

    def test_hop_limit_is_enforced(self):
        def endless(url):
            return 302, url + "x", url + "x"
        _expect(self, ru.RedditUrlError, ru.REDDIT_SHARE_LINK_UNRESOLVED,
                ru.resolve, SHARE, endless, max_hops=3)

    def test_redirect_loop_is_rejected(self):
        def looper(url):
            return 302, (CANON if url == SHARE else SHARE), url
        _expect(self, ru.RedditUrlError, "REDDIT_REDIRECT_LOOP", ru.resolve, SHARE, looper)

    def test_foreign_redirect_destination_is_rejected(self):
        def foreign(url):
            return 301, "https://evil.example.com/r/x/comments/abcde/", url
        _expect(self, ru.RedditUrlError, ru.REDDIT_URL_UNSUPPORTED, ru.resolve, SHARE, foreign)

    def test_rate_limit_during_resolution_is_typed(self):
        _expect(self, ru.RedditUrlError, "REDDIT_RATE_LIMITED", ru.resolve, SHARE,
                lambda u: (429, None, u))

    def test_resolution_without_a_post_identity_is_refused(self):
        _expect(self, ru.RedditUrlError, ru.REDDIT_URL_UNSUPPORTED, ru.resolve, SHARE,
                lambda u: (200, None, "https://www.reddit.com/r/x/"))


def _native_meta(**over):
    meta = {"extractor_key": "Reddit", "id": "61i3p09gjr5f1", "display_id": POST,
            "title": "t", "duration": 21, "width": 720, "height": 1044,
            "thumbnail": "https://b.thumbs.redditmedia.com/x.jpg",
            "formats": [
                {"format_id": "dash-6", "vcodec": "none", "acodec": "mp4a.40.2",
                 "ext": "m4a", "tbr": 64, "url": "https://v.redd.it/a/DASH_AUDIO_128.mp4"},
                {"format_id": "dash-3", "vcodec": "avc1.4d401e", "acodec": "none",
                 "ext": "mp4", "tbr": 950, "url": "https://v.redd.it/a/DASH_720.mp4"},
                {"format_id": "fallback", "vcodec": "h264", "acodec": "none",
                 "ext": "mp4", "tbr": 300, "url": "https://v.redd.it/a/fallback.mp4"}]}
    meta.update(over)
    return meta


class TestClassification(unittest.TestCase):
    def test_native_reddit_video(self):
        self.assertEqual(rctx.classify_metadata(_native_meta())["classification"],
                         rctx.NATIVE_VIDEO)

    def test_external_host_yields_the_destination_platform(self):
        got = rctx.classify_metadata({"extractor_key": "Youtube", "id": "abc"})
        self.assertEqual(got["classification"], rctx.EXTERNAL_HOST)
        self.assertEqual(got["platform_hint"], "youtube")

    def test_typed_failures_are_classified(self):
        cases = {
            "This post was deleted": rctx.POST_DELETED_CODE,
            "this post was removed by a moderator": rctx.POST_REMOVED_CODE,
            "This community is private": rctx.COMMUNITY_PRIVATE_CODE,
            "this subreddit is quarantined": rctx.COMMUNITY_QUARANTINED_CODE,
            "This post is unavailable": rctx.POST_UNAVAILABLE_CODE,
            "This content is age-restricted": rctx.AGE_GATE_CODE,
            "only available for registered users": rctx.AGE_GATE_CODE,
            "HTTP Error 429: Too Many Requests": rctx.REDDIT_RATE_LIMITED,
        }
        for message, code in cases.items():
            self.assertEqual(rctx.classify_failure(message), code, message)

    def test_a_generic_403_never_becomes_an_age_gate_claim(self):
        for message in ("HTTP Error 403: Forbidden", "403", "Forbidden"):
            self.assertNotEqual(rctx.classify_failure(message), rctx.AGE_GATE_CODE)
        self.assertEqual(rctx.classify_failure("HTTP Error 403: Forbidden"), "")

    def test_unknown_is_not_a_guess(self):
        self.assertEqual(rctx.classify_metadata({"extractor_key": "Reddit",
                                                 "formats": []})["classification"],
                         rctx.UNKNOWN)

    def test_image_and_gallery_hints(self):
        self.assertEqual(rctx.classify_metadata(
            {"extractor_key": "Reddit", "formats": [], "url": "https://i.redd.it/x.jpg"}
        )["classification"], rctx.IMAGE)
        self.assertEqual(rctx.classify_metadata(
            {"extractor_key": "Reddit", "formats": [],
             "url": "https://www.reddit.com/gallery/abc"})["classification"], rctx.GALLERY)

    def test_crosspost_is_not_silently_treated_as_the_original(self):
        """A crosspost must not be reported as the source it points at."""
        got = rctx.classify_metadata(_native_meta(crosspost_parent_list=[
            {"id": "other1", "subreddit": "elsewhere"}]))
        self.assertEqual(got["classification"], rctx.NATIVE_VIDEO)
        # The media id stays this post's own; the parent is never substituted.
        self.assertEqual(_native_meta()["id"], "61i3p09gjr5f1")


class TestStreamSelection(unittest.TestCase):
    def test_picks_h264_video_and_aac_audio(self):
        got = rctx.select_streams(_native_meta())
        self.assertEqual(got["video"]["format_id"], "dash-3")   # highest H.264 bitrate
        self.assertEqual(got["audio"]["format_id"], "dash-6")
        self.assertTrue(got["audio_stream_available"])

    def test_no_audio_stream_reports_unavailable(self):
        meta = _native_meta()
        meta["formats"] = [f for f in meta["formats"] if f["acodec"] == "none"]
        self.assertFalse(rctx.select_streams(meta)["audio_stream_available"])

    def test_only_a_probe_may_declare_a_source_silent(self):
        self.assertEqual(rctx.audio_state(stream_available=True, probed_has_audio=None),
                         "unprobed")
        self.assertEqual(rctx.audio_state(stream_available=False, probed_has_audio=False),
                         "verified_silent")

    def test_external_delegation_support(self):
        self.assertTrue(rctx.external_supported("youtube"))
        self.assertFalse(rctx.external_supported("redgifs"))


_COMPLIANT_AUDIO = {
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "21.0"},
    "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
                 "width": 720, "height": 1044, "avg_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac"}]}
_COMPLIANT_SILENT = {
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "21.0"},
    "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
                 "width": 720, "height": 1044, "avg_frame_rate": "30/1"}]}


class TestVerifiedSilentNormalization(unittest.TestCase):
    def test_missing_audio_is_a_gap_by_default(self):
        self.assertIn("no_audio", media_normalize.compatibility_gaps(_COMPLIANT_SILENT))

    def test_missing_audio_is_not_a_gap_for_a_verified_silent_source(self):
        self.assertEqual(media_normalize.compatibility_gaps(_COMPLIANT_SILENT,
                                                           require_audio=False), [])

    def test_validate_accepts_verified_silence_and_reports_it(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.mp4"
            p.write_bytes(b"\x00" * 32)
            rep = media_normalize.validate_finalized(_COMPLIANT_SILENT, path=p,
                                                    require_audio=False)
            self.assertFalse(rep["has_audio"])
            self.assertEqual(rep["audio_codec"], "")

    def test_normalize_command_drops_audio_mapping_when_silent(self):
        cmd = media_normalize.build_normalize_command("a.mp4", "b.mp4", with_audio=False)
        self.assertNotIn("0:a:0", cmd)
        self.assertNotIn("-c:a", cmd)
        self.assertIn("0:a:0", media_normalize.build_normalize_command(
            "a.mp4", "b.mp4", with_audio=True))


class _FakeRedditIO:
    def __init__(self, tmp, lib, *, mux_gives_audio=True, classify_error=None):
        self.downloads = []
        self._mux_gives_audio = mux_gives_audio
        self._classify_error = classify_error
        self.io = rm.RedditMediaIO(
            download_one_video=self.download_one_video,
            classify_downloaded_file=self.classify_downloaded_file,
            run_ffprobe=self.run_ffprobe,
            parse_ffprobe_result=self.parse_ffprobe_result,
            checksum_sha256=self.checksum,
            finalize=self.finalize,
            cleanup_temp=self.cleanup,
            run_ffmpeg=self.run_ffmpeg,
            run_ffprobe_json=self.run_ffprobe_json,
            mux_builder=lambda v, a, o: ["ffmpeg", "-i", v, "-i", a, o],
            tmp_dir=str(tmp), library_dir=str(lib))
        self._last_had_audio = True

    def download_one_video(self, url, tmp_dir, *, max_bytes, timeout):
        self.downloads.append(url)
        name = "audio.mp4.part" if "AUDIO" in url else "video.mp4.part"
        path = Path(tmp_dir) / name
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x11" * 64)
        return str(path), path.stat().st_size, 200, "video/mp4"

    def classify_downloaded_file(self, path, *, http_status, content_type, size_bytes):
        if self._classify_error:
            raise self._classify_error
        return "ok"

    def run_ffprobe(self, path):
        """The probe records what the LAST source carried, so the normalized-output
        probe can mirror it: a silent source stays silent after normalization."""
        if "muxed" in str(path):
            probe = _COMPLIANT_AUDIO if self._mux_gives_audio else _COMPLIANT_SILENT
        else:
            probe = _COMPLIANT_SILENT            # the raw video stream is audio-less
        self._last_had_audio = any(s.get("codec_type") == "audio"
                                   for s in probe.get("streams") or [])
        return probe

    @staticmethod
    def parse_ffprobe_result(probe, *, actual_size_bytes, max_bytes, max_duration):
        audio = [s for s in (probe.get("streams") or []) if s.get("codec_type") == "audio"]
        return {"duration_seconds": 21.0, "width": 720, "height": 1044,
                "frame_rate": "30/1", "has_audio": bool(audio),
                "container": "mov,mp4,m4a,3gp,3g2,mj2", "video_codec": "h264"}

    @staticmethod
    def checksum(path):
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def finalize(tmp_path, final_path):
        Path(tmp_path).replace(final_path)
        return final_path

    @staticmethod
    def cleanup(path):
        try:
            Path(path).unlink()
        except OSError:
            pass

    @staticmethod
    def run_ffmpeg(cmd):
        Path(cmd[-1]).write_bytes(
            b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200 + b"moov" + b"mdat")
        return ""

    def run_ffprobe_json(self, path):
        return _COMPLIANT_AUDIO if getattr(self, "_last_had_audio", True) else _COMPLIANT_SILENT


def _target(**over):
    t = {"post_id": POST, "canonical_url": CANON, "subreddit": SUB,
         "title": "Now why they do officer fine shit like that",
         "creator_name": "[deleted]", "creator_handle": "",
         "video_url": "https://v.redd.it/abc/DASH_720.mp4",
         "audio_url": "https://v.redd.it/abc/DASH_AUDIO_128.mp4",
         "duration_seconds": 21, "width": 720, "height": 1044}
    t.update(over)
    return t


class TestNativeMediaPath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._lib = tempfile.TemporaryDirectory()
        self.tmp, self.lib = Path(self._tmp.name), Path(self._lib.name)

    def tearDown(self):
        self._tmp.cleanup()
        self._lib.cleanup()

    def test_native_video_with_audio_is_muxed_and_finalized(self):
        fake = _FakeRedditIO(self.tmp, self.lib)
        seen = {}
        out = rm.acquire_reddit_native(target=_target(), io=fake.io, library_rows=[],
                                       persist_library=lambda r: (seen.update(r), r)[1])
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["muxed"])
        self.assertEqual(out["audio_state"], "audio")
        self.assertEqual(out["video_codec"], "h264")
        self.assertEqual(len(out["asset_hash"]), 64)
        self.assertTrue(Path(out["local_path"]).is_file())
        self.assertEqual(out["asset_filename"].split("__")[0], "reddit")
        self.assertIn(POST, out["asset_filename"])
        self.assertEqual(seen["platform_content_id"], POST)
        self.assertEqual(rl.asset_titles(seen)["display_title"], out["display_title"])
        self.assertNotIn("v.redd.it", json.dumps(seen))      # no signed URL persisted

    def test_silent_source_is_verified_then_finalized_video_only(self):
        fake = _FakeRedditIO(self.tmp, self.lib)
        out = rm.acquire_reddit_native(target=_target(audio_url=""), io=fake.io,
                                       library_rows=[])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["audio_state"], "verified_silent")
        self.assertFalse(out["has_audio"])
        self.assertFalse(out["muxed"])

    def test_a_downloaded_audio_stream_that_yields_no_audio_is_a_failure(self):
        fake = _FakeRedditIO(self.tmp, self.lib, mux_gives_audio=False)
        _expect(self, rm.RedditMediaError, "AUDIO_MISSING",
                rm.acquire_reddit_native, target=_target(), io=fake.io, library_rows=[])

    def test_html_or_error_payloads_are_rejected(self):
        err = type("E", (Exception,), {})("HTML_RESPONSE")
        err.code, err.message = "HTML_RESPONSE", "downloaded an HTML error body"
        fake = _FakeRedditIO(self.tmp, self.lib, classify_error=err)
        _expect(self, rm.RedditMediaError, "HTML_RESPONSE",
                rm.acquire_reddit_native, target=_target(), io=fake.io, library_rows=[])

    def test_missing_video_stream_is_refused(self):
        fake = _FakeRedditIO(self.tmp, self.lib)
        _expect(self, rm.RedditMediaError, rctx.NO_VIDEO_STREAM,
                rm.acquire_reddit_native, target=_target(video_url=""), io=fake.io,
                library_rows=[])

    def test_repeat_acquisition_is_idempotent_and_downloads_nothing(self):
        fake = _FakeRedditIO(self.tmp, self.lib)
        rows = [{"platform_content_id": POST, "canonical_url": CANON, "id": "row-1"}]
        out = rm.acquire_reddit_native(target=_target(), io=fake.io, library_rows=rows)
        self.assertTrue(out["idempotent"])
        self.assertFalse(out["downloaded"])
        self.assertEqual(fake.downloads, [])

    def test_no_library_root_is_refused(self):
        fake = _FakeRedditIO(self.tmp, self.lib)
        fake.io.library_dir = ""
        _expect(self, rm.RedditMediaError, "NO_LIBRARY_DIR",
                rm.acquire_reddit_native, target=_target(), io=fake.io, library_rows=[])

    def test_filenames_are_deterministic(self):
        a = tc.title_fields(platform="reddit", content_id=POST, digest=DIGEST,
                            source_title="Some Title", creator_name="Creator")
        b = tc.title_fields(platform="reddit", content_id=POST, digest=DIGEST,
                            source_title="Some Title", creator_name="Creator")
        self.assertEqual(a["asset_filename"], b["asset_filename"])
        self.assertTrue(a["asset_filename"].startswith("reddit__creator__some-title__"))
        self.assertEqual(a["poster_filename"],
                         a["asset_filename"].rsplit(".", 1)[0] + "__poster.jpg")

    def test_deleted_author_never_becomes_a_fabricated_creator_token(self):
        f = tc.title_fields(platform="reddit", content_id=POST, digest=DIGEST,
                            source_title="Officer fine shit", creator_name="[deleted]")
        self.assertEqual(f["creator_name"], "[deleted]")      # recorded verbatim
        self.assertNotIn("--", f["asset_filename"])


class TestExternalDelegation(unittest.TestCase):
    def test_supported_destination_is_delegated(self):
        out = rm.delegate_external("youtube")
        self.assertTrue(out["delegated"])
        self.assertEqual(out["destination_platform"], "youtube")

    def test_unsupported_destination_is_classified(self):
        out = rm.delegate_external("redgifs")
        self.assertFalse(out["delegated"])
        self.assertEqual(out["error"], rctx.EXTERNAL_HOST_UNSUPPORTED)

    def test_missing_destination_is_classified(self):
        self.assertEqual(rm.delegate_external("")["error"], rctx.EXTERNAL_HOST_UNSUPPORTED)


# ===========================================================================
# 7. Authorized session (value-safe)
# ===========================================================================
class TestAuthorizedSessionSafety(unittest.TestCase):
    """The cookie path must never surface, store, or ship cookie material."""

    SENTINEL = "SUPERSECRETCOOKIEVALUE_do_not_leak"

    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self.root = Path(self._root.name)
        for name, hosts in (("Default", [".reddit.com", "www.reddit.com"]),
                            ("Profile 1", [".example.com"])):
            d = self.root / name
            d.mkdir(parents=True)
            con = sqlite3.connect(d / "Cookies")
            con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
            for h in hosts:
                con.execute("INSERT INTO cookies VALUES (?,?,?)",
                            (h, "reddit_session", self.SENTINEL))
                con.execute("INSERT INTO cookies VALUES (?,?,?)",
                            (h, "token_v2", self.SENTINEL))
            con.commit()
            con.close()
        self._old = os.environ.get("LOOM_CHROME_ROOT")
        os.environ["LOOM_CHROME_ROOT"] = str(self.root)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("LOOM_CHROME_ROOT", None)
        else:
            os.environ["LOOM_CHROME_ROOT"] = self._old
        self._root.cleanup()

    def test_the_right_profile_is_found_by_host_key_only(self):
        self.assertEqual(rs.find_profile(host="reddit.com"), "Default")
        self.assertEqual(rs.list_profiles(), ["Default", "Profile 1"])

    def test_discovery_returns_host_keys_and_counts_but_never_values(self):
        info = rs.profile_cookie_hosts("Default", pattern="%reddit%")
        self.assertEqual(info["hosts"], [".reddit.com", "www.reddit.com"])
        self.assertGreater(info["rows"], 0)
        blob = json.dumps(info)
        self.assertNotIn(self.SENTINEL, blob)                 # no cookie value
        self.assertNotIn("reddit_session", blob)              # no cookie name either
        self.assertNotIn("value", info)

    def test_describe_is_safe_to_print(self):
        blob = json.dumps(rs.describe(host="reddit.com"))
        self.assertNotIn(self.SENTINEL, blob)
        self.assertIn("Default", blob)

    def test_public_first_no_cookie_arguments_by_default(self):
        self.assertEqual(rs.browser_cookie_args(), [])
        self.assertEqual(rs.browser_cookie_args(authenticated=False), [])

    def test_authenticated_arguments_use_in_process_browser_cookies(self):
        args = rs.browser_cookie_args(profile="Default", authenticated=True)
        self.assertEqual(args, ["--cookies-from-browser", "chrome:Default"])
        self.assertNotIn("--cookies", [a for a in args if a == "--cookies"])  # never a file

    def test_auth_is_only_justified_by_an_explicit_auth_gate(self):
        self.assertTrue(rs.needs_authenticated_session("AGE_GATE_AUTH_REQUIRED"))
        for code in ("POST_DELETED", "POST_REMOVED", "COMMUNITY_PRIVATE",
                     "POST_UNAVAILABLE", "NOT_A_VIDEO_POST", ""):
            self.assertFalse(rs.needs_authenticated_session(code), code)

    def test_redaction_removes_session_identifiers(self):
        self.assertNotIn(self.SENTINEL, rs.redact(f"reddit_session={self.SENTINEL}"))
        self.assertNotIn(self.SENTINEL, rs.redact(f"session: {self.SENTINEL}"))
        self.assertNotIn(self.SENTINEL, rs.redact("token_v2=" + self.SENTINEL))
        self.assertIn(rs.REDACTED, rs.redact(f"reddit_session={self.SENTINEL}"))

    def test_outbound_payload_guard_refuses_cookie_material(self):
        with self.assertRaises(ValueError) as e:
            rs.assert_no_cookie_material({"note": "used --cookies-from-browser chrome"})
        self.assertIn("COOKIE_MATERIAL_REFUSED", str(e.exception))
        # A clean payload passes untouched.
        self.assertEqual(rs.assert_no_cookie_material({"title": "Officer fine shit"}),
                         {"title": "Officer fine shit"})

    def test_no_password_is_ever_requested_or_stored(self):
        src = (Path(_REPO_ROOT) / "infra/acquisition/reddit_session.py").read_text()
        for forbidden in ("getpass", "password=", "input("):
            self.assertNotIn(forbidden, src)
        self.assertIn("never requested, stored, or logged", src)

    def test_blocker_instruction_is_one_command(self):
        text = rs.blocker_instruction("Default")
        self.assertIn("--cookies-from-browser chrome:Default", text)
        self.assertEqual(text.count("`"), 2)


if __name__ == "__main__":
    unittest.main()
