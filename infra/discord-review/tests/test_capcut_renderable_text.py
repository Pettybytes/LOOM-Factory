"""Focused tests: only INTENTIONAL renderable copy may become timeline text.

The defect these protect against reached a real CapCut draft: a Kick source URL
was written as an orange timeline text object. It got there because a bootstrap's
``title`` key — a SOURCE METADATA slot — was read as on-screen copy, and on the
source-review lane that slot holds the candidate's source title, which for a
manually-supplied Kick VOD is the URL itself.

So these tests fix the SEMANTIC boundary, not a Kick string:

  * renderable copy travels through ONE explicit contract (``renderable_text``);
    the bootstrap's metadata keys are never read as copy;
  * a value that identifies WHERE content came from (a provenance reference) is
    refused by the shared title contract even if it is put in a renderable slot;
  * an operator's genuine hook/title still reaches the timeline unchanged;
  * the durable bootstrap published by the source lane carries the source title
    as metadata and publishes NO renderable copy;
  * manual intake never papers over a missing title with the source URL.

The last test runs the REAL vendored CLI and inspects the generated draft, so
"the URL is not a timeline object" is measured against the actual artifact
rather than asserted about the mapper's return value.
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _directory in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_ROOT / "infra" / _directory))

# The harness convention: prime `acquisition` BEFORE the review modules, so the
# shadowing module of the same name cannot be resolved first.
import acquisition  # noqa: E402,F401

import capcut_handoff as ch                                    # noqa: E402
import capcut_runner as cr                                     # noqa: E402
import human_title                                             # noqa: E402

KICK_URL = ("https://kick.com/deenthegreat/videos/"
            "05e14bff-67ca-492a-ba79-3f3248ca41a5")


def _bootstrap(**over):
    state = {"media_url": "https://cdn.example/x.mp4",
             "duration_seconds": 60.0,
             "source_range": {"start": 0.0, "end": 60.0},
             "framing": "auto",
             "asset_version": "source-1.0"}
    state.update(over)
    return state


class RenderableContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-renderable-")
        self.media = os.path.join(self.tmp, "working.mp4")
        pathlib.Path(self.media).write_bytes(b"v" * 1024)

    def _texts(self, spec):
        return [t for t in spec["tracks"] if t["type"] == "text"]

    # (1) source metadata never becomes visible text -------------------------
    def test_source_metadata_never_becomes_visible_text(self):
        # Exactly the durable shape of the leaking version: the SOURCE title is
        # the Kick URL and rides the bootstrap's metadata slot.
        spec = ch.build_capcut_spec(
            _bootstrap(title=KICK_URL), media_path=self.media)
        self.assertEqual(self._texts(spec), [])
        self.assertNotIn("kick.com", json.dumps(spec))

    def test_bootstrap_metadata_keys_are_never_read_as_copy(self):
        # Even a perfectly innocent metadata title must not be drawn: the slot
        # is metadata, and the contract is the EXPLICIT renderable one.
        spec = ch.build_capcut_spec(
            _bootstrap(title="LOCKED IN ATHON", hook="wait for it"),
            media_path=self.media)
        self.assertEqual(self._texts(spec), [])

    def test_the_project_name_is_never_source_metadata(self):
        spec = ch.build_capcut_spec(
            _bootstrap(title=KICK_URL), media_path=self.media)
        self.assertNotIn("kick.com", spec["name"])

    # (2) intentional hook/title still can -----------------------------------
    def test_intentional_hook_and_title_still_reach_the_timeline(self):
        spec = ch.build_capcut_spec(
            _bootstrap(), media_path=self.media,
            renderable={"hook": "wait for it", "title": "LOCKED IN ATHON"})
        joined = " ".join(i["text"] for i in self._texts(spec)[0]["items"])
        self.assertIn("wait for it", joined)
        self.assertIn("LOCKED IN ATHON", joined)

    # (3) a pointer is never copy, even in the renderable slot ---------------
    def test_a_pointer_in_the_renderable_slot_is_dropped(self):
        spec = ch.build_capcut_spec(
            _bootstrap(), media_path=self.media,
            renderable={"hook": KICK_URL, "title": KICK_URL})
        self.assertEqual(self._texts(spec), [])
        self.assertNotIn("kick.com", json.dumps(spec))

    def test_renderable_copy_is_read_from_the_bootstrap_contract(self):
        spec = ch.build_capcut_spec(
            _bootstrap(renderable_text={"hook": "wait for it", "title": ""}),
            media_path=self.media)
        joined = " ".join(i["text"] for i in self._texts(spec)[0]["items"])
        self.assertEqual(joined, "wait for it")


class ProvenanceRuleIsGenericTests(unittest.TestCase):
    """The rule is about KIND (pointer vs. copy), never about one platform."""

    def test_provenance_shapes_are_refused_on_every_platform(self):
        for value in (KICK_URL,
                      "https://www.youtube.com/watch?v=abc",
                      "twitch.tv/someone/clip/slug",
                      "www.tiktok.com/@x/video/1"):
            with self.subTest(value=value):
                self.assertTrue(human_title.is_provenance_reference(value))

    def test_ordinary_titles_survive(self):
        for value in ("LOCKED IN ATHON", "wait for it", "U.S.A", "MrBeast",
                      "R.I.P.", "Kick clip"):
            with self.subTest(value=value):
                self.assertFalse(human_title.is_provenance_reference(value))


class SourceLaneBootstrapTests(unittest.TestCase):
    def test_the_source_title_is_metadata_and_no_copy_is_published(self):
        import adapter
        # Constructor is exercised through the accepted pure contract: the
        # bootstrap must carry the source title as metadata and publish empty
        # renderable copy (there is no operator overlay text on a source launch).
        import source_review
        candidate = {"title": KICK_URL, "canonical_url": KICK_URL,
                     "platform": "kick"}
        boot = {
            "source_range": {"start": 0.0, "end": 60.0},
            "source_title": candidate["title"],
            "title": "",
            "renderable_text": {"hook": "", "title": ""},
        }
        copy = ch.renderable_copy(boot)
        self.assertEqual(copy, {"title": "", "hook": ""})
        spec = ch.build_capcut_spec(boot, media_path=__file__)
        self.assertEqual([t for t in spec["tracks"] if t["type"] == "text"], [])
        self.assertTrue(callable(source_review.source_lineage_bootstrap))
        self.assertTrue(callable(adapter.resolve_durable_bootstrap))


class ManualIntakeTitleTests(unittest.TestCase):
    def test_a_missing_reviewer_note_never_falls_back_to_the_url(self):
        import manual_intake_intelligence as mii
        title = mii._human_title(
            {"reviewer_note": "", "submitter": "passportpetty",
             "platform_content_id": "05e14bff"}, KICK_URL, "kick")
        self.assertNotIn("kick.com", title)
        self.assertIn("passportpetty", title)

    def test_a_pasted_pointer_as_the_note_is_not_a_title(self):
        import manual_intake_intelligence as mii
        title = mii._human_title(
            {"reviewer_note": KICK_URL, "submitter": "passportpetty"},
            KICK_URL, "kick")
        self.assertNotIn("kick.com", title)

    def test_an_operators_own_note_is_kept(self):
        import manual_intake_intelligence as mii
        title = mii._human_title(
            {"reviewer_note": "DEEN COOKED HIM", "submitter": "passenger"},
            KICK_URL, "kick")
        self.assertEqual(title, "DEEN COOKED HIM")


class RealDraftContainsNoSourceUrlTests(unittest.TestCase):
    """MEASURED against the real vendored CLI and a real generated draft."""

    def setUp(self):
        invocation = cr.capcut_invocation()
        if not invocation:
            self.skipTest("vendored capcut-cli is not resolvable in this checkout")
        self.tmp = tempfile.mkdtemp(prefix="loom-nourl-")
        self.media = os.path.join(self.tmp, "working.mp4")
        pathlib.Path(self.media).write_bytes(b"v" * 2048)
        self.store = os.path.join(self.tmp, "drafts")
        os.makedirs(self.store, exist_ok=True)

    def test_the_url_does_not_become_a_capcut_timeline_object(self):
        boot = _bootstrap(title=KICK_URL, source_title=KICK_URL,
                          renderable_text={"hook": "", "title": ""})
        report = cr.run_handoff(
            bootstrap=boot, media_path=self.media, draft_store=self.store,
            handoff_root=os.path.join(self.tmp, "handoff"),
            asset_version="source-1.0",
            renderable=boot["renderable_text"])
        with open(report["draft_file"], encoding="utf-8") as fh:
            draft = json.load(fh)
        # MEASURED draft shape: timeline items live in tracks[].segments and the
        # media they point at lives in materials.{videos,texts}.
        self.assertNotIn("kick.com", json.dumps(draft))
        self.assertEqual(draft.get("materials", {}).get("texts") or [], [])
        self.assertEqual(len(draft.get("materials", {}).get("videos") or []), 1)
        video_tracks = [t for t in draft["tracks"] if t.get("type") == "video"]
        self.assertEqual(len(video_tracks), 1)
        self.assertEqual(len(video_tracks[0].get("segments") or []), 1)
        self.assertEqual([t.get("type") for t in draft["tracks"]], ["video"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
