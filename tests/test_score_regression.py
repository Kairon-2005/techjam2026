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

# Frozen at commit 2f85538 with the default config.
EXPECTED = {"score": 0.928708, "hr10": 0.995, "mrr": 0.839361, "mttc": 2.03}


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


if __name__ == "__main__":
    unittest.main()
