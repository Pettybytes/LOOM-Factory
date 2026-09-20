"""Focused tests for relative reranking (bounded A/B support).

All offline: transports are injected, so no network and no credential is needed.
Covers the output contract, strict schema validation, the complete fallback
matrix, determinism, bounded prompt construction and provenance preservation.

Run from inside this directory:  python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

_SUITE_DIR = Path(__file__).resolve().parents[1]
if str(_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUITE_DIR))

from relative_rerank import (  # noqa: E402
    Finalist,
    RerankConfig,
    RerankValidationError,
    build_rerank_prompt,
    relative_rerank,
    validate_rerank_payload,
)


def finalists(n=3):
    return [
        Finalist(candidate_id=f"c{i}", independent_score=90 - i * 10, independent_rank=i,
                 transcript_excerpt=f"excerpt {i}", duration_s=20.0 + i,
                 dimensions={"hook": 80 - i, "payoff": 70 - i},
                 hook=f"hook {i}", signal_bases=["audio_spike"] if i % 2 else [],
                 source_id="src-1", provenance={"clip": f"loom_{i}"})
        for i in range(1, n + 1)
    ]


def payload_for(fs, order=None, **overrides):
    order = order or [f.candidate_id for f in fs]
    rows = []
    for rank, cid in enumerate(order, start=1):
        row = {"candidate_id": cid, "relative_rank": rank, "relative_score": 100 - rank,
               "rationale": f"because {cid}", "strongest_evidence": "hook",
               "redundant_with": None, "confidence": 0.7}
        row.update(overrides.pop(cid, {}))
        rows.append(row)
    return {"ranking": rows}


def transport(payload_text, usage=None, model="qwen/qwen3.5-9b"):
    def _t(_prompt):
        return payload_text, usage or {"prompt_tokens": 120, "completion_tokens": 60}, model
    return _t


class SuccessPathTests(unittest.TestCase):
    def test_valid_payload_produces_reranked_order(self):
        fs = finalists(3)
        import json
        payload = payload_for(fs, order=["c3", "c1", "c2"])
        r = relative_rerank(fs, transport=transport(json.dumps(payload)))
        self.assertTrue(r.used)
        self.assertEqual(r.fallback, "")
        self.assertEqual([f.candidate_id for f in r.finalists], ["c3", "c1", "c2"])
        self.assertEqual([f.relative_rank for f in r.finalists], [1, 2, 3])
        self.assertTrue(all(f.reranked for f in r.finalists))

    def test_original_independent_score_is_never_overwritten(self):
        fs = finalists(3)
        import json
        r = relative_rerank(fs, transport=transport(json.dumps(payload_for(fs))))
        by_id = {f.candidate_id: f for f in r.finalists}
        for f in fs:
            self.assertEqual(by_id[f.candidate_id].independent_score, f.independent_score)
            self.assertEqual(by_id[f.candidate_id].independent_rank, f.independent_rank)

    def test_relative_fields_are_captured(self):
        fs = finalists(2)
        import json
        payload = payload_for(fs, c2={"redundant_with": "c1"})
        r = relative_rerank(fs, transport=transport(json.dumps(payload)))
        by_id = {f.candidate_id: f for f in r.finalists}
        self.assertEqual(by_id["c2"].redundant_with, "c1")
        self.assertIsNotNone(by_id["c1"].rationale)
        self.assertIsNotNone(by_id["c1"].confidence)

    def test_provenance_is_preserved(self):
        fs = finalists(2)
        import json
        r = relative_rerank(fs, transport=transport(json.dumps(payload_for(fs))))
        by_id = {f.candidate_id: f for f in r.finalists}
        self.assertEqual(by_id["c1"].provenance, {"clip": "loom_1"})

    def test_usage_and_cost_are_recorded(self):
        fs = finalists(2)
        import json
        r = relative_rerank(fs, transport=transport(json.dumps(payload_for(fs)),
                                                    usage={"prompt_tokens": 500, "completion_tokens": 200}))
        self.assertEqual(r.usage.input_tokens, 500)
        self.assertEqual(r.usage.output_tokens, 200)
        self.assertIsNotNone(r.usage.cost_usd)
        self.assertIsNotNone(r.usage.latency_s)

    def test_ordering_changed_flag(self):
        fs = finalists(3)
        import json
        same = relative_rerank(fs, transport=transport(json.dumps(payload_for(fs))))
        self.assertFalse(same.ordering_changed)
        flipped = relative_rerank(fs, transport=transport(
            json.dumps(payload_for(fs, order=["c2", "c1", "c3"]))))
        self.assertTrue(flipped.ordering_changed)


class DeterminismTests(unittest.TestCase):
    def test_rerank_is_deterministic_for_identical_input(self):
        fs = finalists(3)
        import json
        t = transport(json.dumps(payload_for(fs, order=["c2", "c3", "c1"])))
        a = relative_rerank(fs, transport=t).to_dict()
        b = relative_rerank(fs, transport=t).to_dict()
        self.assertEqual(a, b)

    def test_equal_scores_tie_break_is_deterministic(self):
        fs = finalists(3)
        import json
        payload = payload_for(fs)
        for row in payload["ranking"]:
            row["relative_score"] = 50          # all tied
        r = relative_rerank(fs, transport=transport(json.dumps(payload)))
        # independent_score desc, then id asc
        self.assertEqual([f.candidate_id for f in r.finalists], ["c1", "c2", "c3"])

    def test_max_finalists_carries_the_rest_through(self):
        fs = finalists(5)
        import json
        cfg = RerankConfig(max_finalists=3)
        payload = payload_for(fs[:3], order=["c3", "c1", "c2"])
        r = relative_rerank(fs, transport=transport(json.dumps(payload)), config=cfg)
        self.assertEqual(r.reranked_count, 3)
        self.assertEqual(r.carried_count, 2)
        carried = [f for f in r.finalists if not f.reranked]
        self.assertEqual([f.candidate_id for f in carried], ["c4", "c5"])
        self.assertEqual([f.relative_rank for f in carried], [4, 5])


class FallbackMatrixTests(unittest.TestCase):
    def test_set_too_small_is_skipped(self):
        r = relative_rerank(finalists(1), transport=transport("{}"))
        self.assertFalse(r.used)
        self.assertEqual(r.fallback, "finalist_set_too_small")
        self.assertEqual(r.finalists[0].relative_rank, r.finalists[0].independent_rank)

    def test_missing_credential_falls_back(self):
        from providers import ProviderError

        def no_key(_p):
            raise ProviderError("AUTH", "LOOM_OPENROUTER_API_KEY absent", retryable=False)
        r = relative_rerank(finalists(3), transport=no_key)
        self.assertFalse(r.used)
        self.assertEqual(r.fallback, "provider_error:AUTH")
        self.assertFalse(r.ordering_changed)

    def test_http_error_falls_back(self):
        from providers import ProviderError

        def http(_p):
            raise ProviderError("HTTP", "Qwen HTTP 429", retryable=True)
        r = relative_rerank(finalists(3), transport=http)
        self.assertEqual(r.fallback, "provider_error:HTTP")

    def test_unexpected_transport_error_falls_back(self):
        def boom(_p):
            raise RuntimeError("socket exploded")
        r = relative_rerank(finalists(3), transport=boom)
        self.assertEqual(r.fallback, "transport_error:RuntimeError")
        self.assertFalse(r.used)

    def test_malformed_json_falls_back(self):
        r = relative_rerank(finalists(3), transport=transport("not json at all"))
        self.assertEqual(r.fallback, "invalid_json")
        self.assertFalse(r.used)

    def test_missing_candidate_falls_back(self):
        fs = finalists(3)
        import json
        payload = payload_for(fs)
        payload["ranking"].pop()
        r = relative_rerank(fs, transport=transport(json.dumps(payload)))
        self.assertEqual(r.fallback, "schema_invalid")

    def test_duplicate_candidate_falls_back(self):
        fs = finalists(3)
        import json
        payload = payload_for(fs)
        payload["ranking"][1]["candidate_id"] = payload["ranking"][0]["candidate_id"]
        r = relative_rerank(fs, transport=transport(json.dumps(payload)))
        self.assertEqual(r.fallback, "schema_invalid")

    def test_unknown_candidate_falls_back(self):
        fs = finalists(2)
        import json
        payload = payload_for(fs)
        payload["ranking"][0]["candidate_id"] = "ghost"
        r = relative_rerank(fs, transport=transport(json.dumps(payload)))
        self.assertEqual(r.fallback, "schema_invalid")

    def test_non_unique_ranks_fall_back(self):
        fs = finalists(3)
        import json
        payload = payload_for(fs)
        payload["ranking"][1]["relative_rank"] = 1
        r = relative_rerank(fs, transport=transport(json.dumps(payload)))
        self.assertEqual(r.fallback, "schema_invalid")

    def test_every_fallback_preserves_the_independent_order(self):
        fs = finalists(3)
        cases = [
            transport("garbage"),
            transport('{"ranking": []}'),
            lambda _p: (_ for _ in ()).throw(RuntimeError("x")),
        ]
        for t in cases:
            r = relative_rerank(fs, transport=t)
            self.assertFalse(r.used)
            self.assertEqual([f.candidate_id for f in r.finalists], ["c1", "c2", "c3"])
            self.assertEqual([f.relative_rank for f in r.finalists], [1, 2, 3])
            self.assertTrue(all(not f.reranked for f in r.finalists))


class ValidationUnitTests(unittest.TestCase):
    def test_validator_rejects_non_object(self):
        with self.assertRaises(RerankValidationError):
            validate_rerank_payload([], finalists(2))

    def test_validator_rejects_bad_confidence(self):
        fs = finalists(2)
        p = payload_for(fs)
        p["ranking"][0]["confidence"] = 1.5
        with self.assertRaises(RerankValidationError):
            validate_rerank_payload(p, fs)

    def test_validator_rejects_self_referential_redundancy(self):
        fs = finalists(2)
        p = payload_for(fs)
        p["ranking"][0]["redundant_with"] = p["ranking"][0]["candidate_id"]
        with self.assertRaises(RerankValidationError):
            validate_rerank_payload(p, fs)

    def test_validator_accepts_a_clean_payload(self):
        fs = finalists(3)
        rows = validate_rerank_payload(payload_for(fs), fs)
        self.assertEqual(len(rows), 3)


class PromptBoundingTests(unittest.TestCase):
    def test_long_excerpts_are_truncated(self):
        cfg = RerankConfig(max_excerpt_chars=50)
        f = Finalist(candidate_id="c1", independent_score=90, independent_rank=1,
                     transcript_excerpt="x" * 5000)
        prompt = build_rerank_prompt([f], config=cfg)
        self.assertNotIn("x" * 51, prompt)
        self.assertIn("x" * 50, prompt)

    def test_total_evidence_budget_is_enforced(self):
        cfg = RerankConfig(max_excerpt_chars=800, max_evidence_chars=1200)
        fs = [Finalist(candidate_id=f"c{i}", independent_score=90, independent_rank=i,
                       transcript_excerpt="word " * 200) for i in range(1, 7)]
        prompt = build_rerank_prompt(fs, config=cfg)
        self.assertLess(len(prompt), 1200 + 2500)   # budget + fixed instructions
        self.assertLess(prompt.count("candidate_id:"), len(fs))

    def test_prompt_forbids_virality_claims(self):
        prompt = build_rerank_prompt(finalists(2))
        self.assertIn("NOT predicting views", prompt)
        self.assertIn("redundant_with", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
