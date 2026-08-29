"""The A1 search checks its own inputs before it searches them.

A search run against a cache that has moved since the replay gate passed is a
search whose objective nobody verified, and the failure is silent: it produces a
weight vector, an MRR and a delta that all look exactly like a result.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lab import a1search as S
from lab import a1trials as T


def _manifest(**kw) -> dict:
    base = {"ok": True, "smoke": False, "checked_sessions": 800,
            "checked_turns": 4402, "delta_mrr": 0.0, "evaluator_delta": 0.0,
            "cache_sha256": "", "split_train_hash": "48d1", "ts": "2026-08-30T00:00:00"}
    return {**base, **kw}


class FrozenInputTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cache = self.dir / "a1cache.jsonl"
        self.cache.write_text('{"sample_id":"s","scenario":"x",'
                              '"scoring_from_turn":1,"turns":[]}\n', encoding="utf-8")
        self.manifest = self.dir / "a1cache.meta.json"
        self.digest = T.sha256_of(self.cache)

    def write(self, **kw) -> None:
        meta = _manifest(cache_sha256=self.digest, **kw)
        self.manifest.write_text(json.dumps(meta), encoding="utf-8")

    def test_a_matching_cache_is_accepted(self) -> None:
        self.write()
        got = T.frozen_inputs(self.cache, self.manifest)
        self.assertEqual(got["cache_sha256"], self.digest)

    def test_a_cache_that_moved_is_refused(self) -> None:
        self.write()
        self.cache.write_text('{"sample_id":"t","scenario":"x",'
                              '"scoring_from_turn":1,"turns":[]}\n', encoding="utf-8")
        with self.assertRaises(T.FrozenInputError) as caught:
            T.frozen_inputs(self.cache, self.manifest)
        self.assertIn("has moved since it was gated", str(caught.exception))

    def test_a_failed_gate_is_refused(self) -> None:
        self.write(ok=False, first_mismatch={"key": ["s", 1]})
        with self.assertRaises(T.FrozenInputError):
            T.frozen_inputs(self.cache, self.manifest)

    def test_a_smoke_manifest_is_refused(self) -> None:
        self.write(smoke=True)
        with self.assertRaises(T.FrozenInputError) as caught:
            T.frozen_inputs(self.cache, self.manifest)
        self.assertIn("SMOKE", str(caught.exception))

    def test_a_short_split_is_refused(self) -> None:
        self.write(checked_sessions=806)
        with self.assertRaises(T.FrozenInputError) as caught:
            T.frozen_inputs(self.cache, self.manifest)
        self.assertIn("806", str(caught.exception))

    def test_a_non_zero_delta_is_refused(self) -> None:
        for field in ("delta_mrr", "evaluator_delta"):
            self.write(**{field: 1e-9})
            with self.assertRaises(T.FrozenInputError) as caught:
                T.frozen_inputs(self.cache, self.manifest)
            self.assertIn("exactly zero", str(caught.exception))

    def test_a_missing_manifest_is_refused(self) -> None:
        with self.assertRaises(T.FrozenInputError):
            T.frozen_inputs(self.cache, self.dir / "absent.json")


class TrialCountTest(unittest.TestCase):
    """189 is asserted, not counted afterwards."""

    def test_the_grid_really_is_189_trials(self) -> None:
        self.assertEqual(T.EXPECTED_TRIALS,
                         S.SWEEPS * len(S.SEARCH_WEIGHTS) * len(S.MULTIPLIERS))

    def test_a_short_search_is_refused(self) -> None:
        sessions = [{"sample_id": "s", "scoring_from_turn": 1, "turns": []}]
        with self.assertRaises(T.FrozenInputError) as caught:
            T.run(sessions, sweeps=1)
        self.assertIn("not 189", str(caught.exception))


class VerdictTest(unittest.TestCase):
    """Step 0 is applied on delta_mrr, and says which way it went."""

    def row(self, delta: float, unchanged: bool = True) -> dict:
        weights = S.default_weights()
        result = {"trials": 189, "sweeps": 3, "searched": list(S.SEARCH_WEIGHTS),
                  "multipliers": list(S.MULTIPLIERS), "baseline_mrr": 0.2,
                  "best_mrr": 0.2 + delta, "delta_mrr": delta,
                  "weights": weights, "weights_unchanged": unchanged,
                  "accepted": [], "seconds": 1.0}
        return T.verdict(result, _manifest(cache_sha256="abc"))

    def test_a_zero_delta_is_a_no_op(self) -> None:
        row = self.row(0.0)
        self.assertTrue(row["no_op"])
        self.assertIn("NO-OP", row["verdict"])
        self.assertIn("not finalist-eligible", row["verdict"])

    def test_a_negative_delta_is_a_no_op(self) -> None:
        self.assertTrue(self.row(-1e-9)["no_op"])

    def test_a_positive_delta_is_a_challenger(self) -> None:
        row = self.row(1e-9)
        self.assertFalse(row["no_op"])
        self.assertIn("CHALLENGER", row["verdict"])

    def test_moved_weights_with_no_gain_are_still_a_no_op(self) -> None:
        # The defect revision 4 corrected: the tie-break can move weights
        # without improving the objective, and the verdict is delta_mrr.
        row = self.row(0.0, unchanged=False)
        self.assertTrue(row["no_op"])
        self.assertFalse(row["weights_unchanged_diagnostic"])

    def test_the_row_carries_the_frozen_input_hashes(self) -> None:
        row = self.row(0.01)
        self.assertEqual(row["cache_sha256"], "abc")
        self.assertEqual(row["split_train_hash"], "48d1")


if __name__ == "__main__":
    unittest.main()
