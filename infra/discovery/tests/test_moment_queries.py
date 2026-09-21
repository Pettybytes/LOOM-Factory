"""Moment query library: categories, creator composition, banned-seed guard."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from discovery.moment_queries import (  # noqa: E402
    ANIMALS, BANNED_GENERIC_SEEDS, CATEGORIES, CREATOR_TARGETS, PUBLIC_HUMAN,
    STREAMER_CREATOR, MomentQuery, assert_no_banned_defaults, compose_creator_moment,
    is_banned_seed, moment_queries,
)


class LibraryTests(unittest.TestCase):
    def test_default_library_covers_three_categories(self):
        qs = moment_queries()
        cats = {q.category for q in qs}
        self.assertEqual(cats, {"streamer_creator", "public_human", "animals"})
        self.assertEqual(len(qs), len(STREAMER_CREATOR) + len(PUBLIC_HUMAN) + len(ANIMALS))

    def test_default_library_has_no_banned_generic_seeds(self):
        assert_no_banned_defaults(moment_queries())          # must not raise
        for banned in BANNED_GENERIC_SEEDS:
            self.assertFalse(any(q.text == banned for q in moment_queries()), banned)

    def test_moment_seeds_are_moment_oriented(self):
        texts = {q.text for q in moment_queries()}
        self.assertIn("wild streamer moment", texts)
        self.assertIn("crazy fall", texts)
        self.assertIn("funny dog moment", texts)
        self.assertNotIn("streamer reaction", texts)

    def test_category_selection_is_configurable(self):
        qs = moment_queries(categories=("animals",))
        self.assertTrue(qs and all(q.category == "animals" for q in qs))

    def test_unknown_category_rejected(self):
        with self.assertRaises(KeyError):
            moment_queries(categories=("does_not_exist",))

    def test_creator_composition(self):
        qs = moment_queries(categories=("streamer_creator",), creators=("IShowSpeed",))
        composed = [q for q in qs if q.creator]
        self.assertEqual(len(composed), len(STREAMER_CREATOR))
        self.assertIn("IShowSpeed + wild fan interaction", {q.text for q in composed})
        for c in CREATOR_TARGETS:
            q = compose_creator_moment(c, "crazy live moment")
            self.assertEqual(q.creator, c)
            self.assertTrue(q.text.startswith(f"{c} + "))

    def test_seed_key_is_stable_and_distinct(self):
        a = MomentQuery(text="crazy fall", category="public_human")
        b = MomentQuery(text="Crazy Fall", category="public_human")
        c = compose_creator_moment("Kai Cenat", "crazy fall")
        self.assertEqual(a.seed_key, b.seed_key)
        self.assertNotEqual(a.seed_key, c.seed_key)

    def test_banned_detection_flags_generic_phrases(self):
        for t in ("streamer reaction", "Streamer Rage", "streamer fails"):
            self.assertTrue(is_banned_seed(t), t)
        self.assertFalse(is_banned_seed("wild streamer moment"))

    def test_banned_guard_rejects_a_contaminated_set(self):
        with self.assertRaises(ValueError):
            assert_no_banned_defaults([MomentQuery(text="streamer rage", category="streamer_creator")])

    def test_categories_are_non_empty_tuples(self):
        for name, seeds in CATEGORIES.items():
            self.assertTrue(seeds, name)
            self.assertTrue(all(isinstance(s, str) and s.strip() for s in seeds), name)


if __name__ == "__main__":
    unittest.main()
