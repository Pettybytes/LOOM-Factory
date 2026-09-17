"""Deterministic tests for the card-based Manual Source Intake UI.

Run:  python3 -m unittest discover -s infra/discord-review/tests -v
"""

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "discord-review"))

import intake_card  # noqa: E402
from manual_intake import SubmissionError  # noqa: E402

ACTIVE = {"dreemzy", "client-a", "proof-tenant"}


class TestCustomId(unittest.TestCase):
    def test_roundtrip(self):
        c = intake_card.cid("dest", "abc123")
        self.assertEqual(intake_card.parse_cid(c), ("dest", "abc123"))

    def test_rejects_foreign_ids(self):
        self.assertIsNone(intake_card.parse_cid("sr_ap_abcdef"))  # source-review id
        self.assertIsNone(intake_card.parse_cid("not-mi"))
        self.assertIsNone(intake_card.parse_cid("mi:dest"))  # missing sid
        self.assertIsNone(intake_card.parse_cid("mi:unknown:abc"))

    def test_compact(self):
        c = intake_card.cid("submit", "a" * 16)
        self.assertLess(len(c), 100)


class TestSession(unittest.TestCase):
    def test_new_session_canonicalizes_and_strips_tracking(self):
        s = intake_card.new_session(
            "https://www.instagram.com/reel/DdQE_RUS0GJ/?utm_source=ig_web_copy_link&stkn=MzRlODBiNWFlZA==",
            "bryan#1", ACTIVE)
        self.assertEqual(s["platform"], "instagram")
        self.assertEqual(s["canonical_url"], "https://www.instagram.com/reel/DdQE_RUS0GJ")
        self.assertEqual(s["destination"], "")
        self.assertEqual(s["format"], "")
        self.assertEqual(s["start_s"], None)
        self.assertFalse(intake_card.is_submittable(s))

    def test_new_session_rejects_bad_url(self):
        with self.assertRaises(SubmissionError):
            intake_card.new_session("https://evil.com/x", "bryan#1", ACTIVE)

    def test_consume_session_guards_double_submit(self):
        s = intake_card.new_session("https://www.instagram.com/reel/ABC/", "bryan#1", ACTIVE)
        sid = s["id"]
        self.assertIsNotNone(intake_card.consume_session(sid))
        self.assertIsNone(intake_card.consume_session(sid))  # replay -> None


class TestStateTransitions(unittest.TestCase):
    def _session(self):
        return intake_card.new_session("https://www.instagram.com/reel/ABC/", "bryan#1", ACTIVE)

    def test_apply_select_destination_unassigned(self):
        s = self._session()
        intake_card.apply_select(s, "dest", ["unassigned"])
        self.assertEqual(s["destination"], "")

    def test_apply_select_destination_tenant(self):
        s = self._session()
        intake_card.apply_select(s, "dest", ["dreemzy"])
        self.assertEqual(s["destination"], "dreemzy")

    def test_apply_select_rejects_proof_tenant(self):
        s = self._session()
        with self.assertRaises(ValueError):
            intake_card.apply_select(s, "dest", ["proof-tenant"])

    def test_apply_select_format_then_submittable(self):
        s = self._session()
        self.assertFalse(intake_card.is_submittable(s))
        intake_card.apply_select(s, "fmt", ["reaction_single"])
        self.assertTrue(intake_card.is_submittable(s))

    def test_apply_details_validates_time_order(self):
        s = self._session()
        intake_card.apply_details(s, "12.5", "22.5", "nice moment")
        self.assertEqual(s["start_s"], 12.5)
        self.assertEqual(s["end_s"], 22.5)
        self.assertEqual(s["note"], "nice moment")
        with self.assertRaises(ValueError):
            intake_card.apply_details(s, "50", "10", "")

    def test_apply_details_rejects_non_numeric(self):
        s = self._session()
        with self.assertRaises(ValueError):
            intake_card.apply_details(s, "abc", "", "")


class TestRendering(unittest.TestCase):
    def _ready(self):
        s = intake_card.new_session("https://www.instagram.com/reel/ABC/", "bryan#1", ACTIVE)
        intake_card.apply_select(s, "fmt", ["commentary_story"])
        return s

    def test_card_disables_submit_until_format(self):
        s = intake_card.new_session("https://www.instagram.com/reel/ABC/", "bryan#1", ACTIVE)
        card = intake_card.render_card(s)
        buttons = card["components"][2]["components"]
        submit = [b for b in buttons if b["custom_id"].startswith("mi:submit")][0]
        self.assertTrue(submit["disabled"])

    def test_card_enables_submit_after_format(self):
        card = intake_card.render_card(self._ready())
        buttons = card["components"][2]["components"]
        submit = [b for b in buttons if b["custom_id"].startswith("mi:submit")][0]
        self.assertFalse(submit["disabled"])

    def test_card_has_destination_format_selects_and_three_buttons(self):
        card = intake_card.render_card(self._ready())
        rows = card["components"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["components"][0]["type"], 3)  # destination select
        self.assertEqual(rows[1]["components"][0]["type"], 3)  # format select
        self.assertEqual(len(rows[2]["components"]), 3)  # details / submit / cancel

    def test_destination_select_leads_unassigned_excludes_proof(self):
        s = intake_card.new_session("https://www.instagram.com/reel/ABC/", "bryan#1", ACTIVE)
        opts = intake_card._dest_options(s)
        self.assertEqual(opts[0]["value"], "unassigned")
        values = [o["value"] for o in opts]
        self.assertIn("dreemzy", values)
        self.assertIn("client-a", values)
        self.assertNotIn("proof-tenant", values)

    def test_confirmation_card_shows_routing_and_platform(self):
        s = self._ready()
        payload = {
            "tenant_id": "", "platform": "instagram", "content_format": "commentary_story",
            "source_url": "https://www.instagram.com/reel/ABC/",
        }
        card = intake_card.render_confirmation(payload, {"id": "abc-123"}, True)
        text = str(card["embeds"][0]["fields"])
        self.assertIn("Awaiting tenant assignment", text)
        self.assertIn("Pending Instagram support", text)
        self.assertIn("No media downloaded", card["embeds"][0]["footer"]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
