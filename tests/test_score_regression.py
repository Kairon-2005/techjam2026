"""Deterministic end-to-end score lock.

The agent is fully deterministic, so the public-set score is an exact constant.
Any change that moves it will fail here and has to be justified rather than
discovered later. Skipped automatically when the catalog is absent -- it is
gitignored organizer data, so a fresh clone will not have it.

Takes ~11s (most of it the one-off index build).
"""
from __future__ import annotations

import unittest
from pathlib import Path

CATALOG = Path("data/catalog.jsonl")
DATASET = Path("data/public_set.jsonl")

# The shipped default: Phase 2 arm C retrieval plus the Phase 3
# candidate-aware question utility. Chosen as a near-score-neutral
# product/robustness trade-off -- ask_policy="other" still scores higher at
# 0.932167 -- so this constant is NOT the project's best public score and
# should not be quoted as one.
EXPECTED = {"score": 0.932067, "hr10": 0.995, "mrr": 0.852556, "mttc": 2.06}
# The highest public score measured, kept as an anchor precisely because the
# shipped default is deliberately not it.
EXPECTED_BEST_KNOWN = {"score": 0.932167, "config": {"ask_policy": "other"}}
# The pre-Phase-2 retrieval path. Kept as a lock, not as history: every
# robustness comparison in notes/ is paired against it, and the R3 arm A rows
# in lab/results.jsonl reproduce exactly this.
LEGACY = {"deep_funnel": False, "starvation_bypass": False, "question_utility": False}
EXPECTED_LEGACY = {"score": 0.928508, "hr10": 0.995, "mrr": 0.839361, "mttc": 2.04}
# Pure-"other" asking on the legacy path, the config frozen at 2f85538. It
# locks a RELATIONSHIP as much as a number: it must differ from EXPECTED_LEGACY
# only in MTTC, because pool-aware questioning changes which questions are
# asked, never the ranking.
EXPECTED_ASK_OTHER = {"score": 0.928708, "hr10": 0.995, "mrr": 0.839361, "mttc": 2.03}


@unittest.skipUnless(CATALOG.exists() and DATASET.exists(),
                     "needs data/catalog.jsonl (gitignored organizer data)")
class ScoreRegressionTest(unittest.TestCase):
    def test_default_config_reproduces_the_frozen_score(self) -> None:
        from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
        import starter.agent as A

        samples = load_jsonl(DATASET)
        ids, cats, prods = catalog_index(CATALOG)
        result = evaluate(A.Agent(str(CATALOG)), samples, ids, cats, prods)
        self.assertEqual(result["recommended_technical_score"], EXPECTED["score"])
        self.assertEqual(result["hit_rate_at_10"], EXPECTED["hr10"])
        self.assertEqual(result["mrr"], EXPECTED["mrr"])
        self.assertEqual(result["mttc"], EXPECTED["mttc"])

    def test_the_legacy_retrieval_path_still_reproduces_its_score(self) -> None:
        from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
        import starter.agent as A

        samples = load_jsonl(DATASET)
        ids, cats, prods = catalog_index(CATALOG)
        result = evaluate(A.Agent(str(CATALOG), config=dict(LEGACY)),
                          samples, ids, cats, prods)
        self.assertEqual(result["recommended_technical_score"], EXPECTED_LEGACY["score"])
        self.assertEqual(result["mrr"], EXPECTED_LEGACY["mrr"])

    def test_pure_other_asking_still_reproduces_the_frozen_score(self) -> None:
        from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
        import starter.agent as A

        samples = load_jsonl(DATASET)
        ids, cats, prods = catalog_index(CATALOG)
        result = evaluate(A.Agent(str(CATALOG), config={**LEGACY, "ask_policy": "other"}),
                          samples, ids, cats, prods)
        self.assertEqual(result["recommended_technical_score"], EXPECTED_ASK_OTHER["score"])
        self.assertEqual(result["mttc"], EXPECTED_ASK_OTHER["mttc"])

    def test_pool_asking_costs_turns_but_never_ranking(self) -> None:
        # Compared within one retrieval path -- across paths the ranking moves
        # for reasons that have nothing to do with the ask policy.
        self.assertEqual(EXPECTED_LEGACY["hr10"], EXPECTED_ASK_OTHER["hr10"])
        self.assertEqual(EXPECTED_LEGACY["mrr"], EXPECTED_ASK_OTHER["mrr"])
        self.assertGreater(EXPECTED_LEGACY["mttc"], EXPECTED_ASK_OTHER["mttc"])

    def test_the_default_is_phase_2_arm_c_plus_phase_3_utility(self) -> None:
        import starter.agent as A
        self.assertTrue(A.DEFAULTS["deep_funnel"])
        self.assertFalse(A.DEFAULTS["category_plane"])
        self.assertTrue(A.DEFAULTS["starvation_bypass"])
        self.assertTrue(A.DEFAULTS["question_utility"])

    def test_the_shipped_default_is_not_the_best_public_score(self) -> None:
        # A lock on the honesty of the choice, not on a number: if these ever
        # become equal the trade-off has stopped being a trade-off and the
        # decision note needs rewriting.
        self.assertLess(EXPECTED["score"], EXPECTED_BEST_KNOWN["score"])

    def test_agent_spends_no_tokens(self) -> None:
        from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
        import starter.agent as A

        samples = load_jsonl(DATASET)[:20]
        ids, cats, prods = catalog_index(CATALOG)
        result = evaluate(A.Agent(str(CATALOG)), samples, ids, cats, prods)
        usage = result["reported_token_usage"]
        self.assertEqual(usage["prompt_tokens"], 0)
        self.assertEqual(usage["completion_tokens"], 0)
        self.assertEqual(usage["total_tokens"], 0)


def tearDownModule() -> None:
    """Close the shared SQLite handles so the run ends without ResourceWarnings."""
    try:
        import starter.agent as A
        A.clear_catalog_cache()
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
