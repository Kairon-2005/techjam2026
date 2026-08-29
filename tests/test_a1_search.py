"""Phase 7A-R1: the A1 feature cache and its deterministic coordinate search.

Written before the implementation. The rules come from
notes/44-phase7a-r1-prereg.md revision 3, and three of them are the ones a
plausible-looking implementation gets wrong:

  * the cached objective must use the EVALUATOR's Top-10 semantics -- a target
    at rank 40 scores zero, not 1/40;
  * the grid multiplies the ORIGINAL DEFAULT, not the current value, or a
    weight zeroed in sweep 1 can never come back;
  * the cache stores FEATURES, never final scores, or the weights it was built
    to search cannot be recomputed from it.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import starter.agent as A
from lab import a1search as S


def turn(target_rank: int | None, n: int = 30, target: str = "T"):
    """One cached turn where the target sits at `target_rank` (1-based).

    Features are rigged so that w_bm25 alone orders candidates by position:
    the item at index i gets f_bm25 = 1 - i/n and zero elsewhere.
    """
    cands, feats = [], []
    for i in range(n):
        asin = target if (target_rank and i == target_rank - 1) else f"c{i}"
        cands.append(asin)
        feats.append({"f_bm25": 1.0 - i / n})
    return {"candidates": cands, "features": feats, "target": target}


def session(*turns_):
    return {"sample_id": "s", "turns": list(turns_)}


class ObjectiveTest(unittest.TestCase):
    """Evaluator semantics: Top-10, stop on conversion, continue on miss."""

    def mrr(self, *sessions, weights=None):
        # Weight NAMES, not feature keys: cached_mrr maps w_bm25 -> f_bm25.
        return S.cached_mrr(list(sessions), weights or {"w_bm25": 1.0})

    def test_rank_one_scores_one(self) -> None:
        self.assertEqual(self.mrr(session(turn(1))), 1.0)

    def test_rank_ten_scores_one_tenth(self) -> None:
        self.assertAlmostEqual(self.mrr(session(turn(10))), 0.1)

    def test_rank_eleven_scores_zero_not_one_eleventh(self) -> None:
        # The evaluator only scores Top-10 (local_evaluator.py TOP_K = 10).
        # Crediting 1/11 here would award a conversion it never awards.
        self.assertEqual(self.mrr(session(turn(11))), 0.0)

    def test_rank_forty_scores_zero(self) -> None:
        self.assertEqual(self.mrr(session(turn(40, n=50))), 0.0)

    def test_a_missing_target_scores_zero(self) -> None:
        self.assertEqual(self.mrr(session(turn(None))), 0.0)

    def test_the_session_stops_at_the_first_top_ten_hit(self) -> None:
        # Turn 1 converts at rank 2; turn 2 would convert at rank 1 and must
        # NOT be reached, or a later better turn would overwrite an earlier
        # conversion the evaluator already took.
        got = self.mrr(session(turn(2), turn(1)))
        self.assertAlmostEqual(got, 0.5)

    def test_a_miss_continues_to_the_next_turn(self) -> None:
        # Turn 1 is out of the Top-10, so the session continues and converts
        # on turn 2 -- which is what makes MTTC-shaped behaviour visible.
        got = self.mrr(session(turn(20), turn(4)))
        self.assertAlmostEqual(got, 0.25)

    def test_the_mean_is_over_sessions(self) -> None:
        got = self.mrr(session(turn(1)), session(turn(None)))
        self.assertAlmostEqual(got, 0.5)

    def test_no_sessions_is_zero_not_an_error(self) -> None:
        self.assertEqual(S.cached_mrr([], {"f_bm25": 1.0}), 0.0)


class GridTest(unittest.TestCase):
    """Candidates multiply the ORIGINAL DEFAULT, never the current value."""

    def test_the_grid_is_relative_to_the_original_default(self) -> None:
        got = S.candidate_values("w_bm25", original=0.3)
        self.assertEqual(got, [0.0, 0.075, 0.15, 0.3, 0.6, 1.2, 2.4])

    def test_a_weight_zeroed_in_sweep_one_can_be_restored(self) -> None:
        # The trap: with a grid over the CURRENT value, every multiplier of 0
        # is 0, so sweep 1 could permanently delete a feature.
        got = S.candidate_values("w_bm25", original=0.3)
        self.assertIn(0.3, got)
        self.assertGreater(max(got), 0.0)

    def test_the_searched_set_is_the_nine_non_zero_weights(self) -> None:
        self.assertEqual(list(S.SEARCH_WEIGHTS),
                         ["w_bm25", "w_cat", "w_exact", "w_field", "w_idf",
                          "w_neg", "w_phrase", "w_pop", "slot_soft"])
        for name in S.SEARCH_WEIGHTS:
            self.assertNotEqual(A.DEFAULTS[name], 0.0, name)

    def test_zero_and_structural_weights_are_excluded(self) -> None:
        for name in ("w_pos", "w_card", "w_soft", "w_profile",
                     "w_profile_adaptive", "w_soft_lo", "w_soft_hi",
                     "soft_adaptive", "phrase_idf"):
            self.assertNotIn(name, S.SEARCH_WEIGHTS, name)

    def test_the_trial_count_is_one_hundred_and_eighty_nine(self) -> None:
        self.assertEqual(S.SWEEPS * len(S.SEARCH_WEIGHTS) * len(S.MULTIPLIERS), 189)


class SearchTest(unittest.TestCase):
    def test_the_search_is_deterministic(self) -> None:
        sessions = [session(turn(3)), session(turn(7))]
        a = S.coordinate_search(sessions, sweeps=1)
        b = S.coordinate_search(sessions, sweeps=1)
        self.assertEqual(a["weights"], b["weights"])
        self.assertEqual(a["trials"], b["trials"])

    def test_the_search_never_worsens_the_objective(self) -> None:
        sessions = [session(turn(5)), session(turn(2))]
        out = S.coordinate_search(sessions, sweeps=1)
        base = S.cached_mrr(sessions, S.default_weights())
        self.assertGreaterEqual(out["mrr"], base)

    def test_the_tie_break_prefers_the_lower_l1_norm(self) -> None:
        # Every candidate ties on a corpus the weights cannot separate, so the
        # smallest weight vector must win rather than whichever came first.
        sessions = [session(turn(None))]
        out = S.coordinate_search(sessions, sweeps=1)
        base = S.default_weights()
        self.assertLessEqual(sum(abs(v) for v in out["weights"].values()),
                             sum(abs(v) for v in base.values()))

    def test_a_no_op_result_is_detected(self) -> None:
        # notes/44 revision 3, Step 0: weights identical to the defaults are a
        # no-op, and a no-op must not reach sup-val or the public run.
        self.assertTrue(S.is_no_op(S.default_weights()))
        changed = dict(S.default_weights())
        changed["w_bm25"] = changed["w_bm25"] * 2
        self.assertFalse(S.is_no_op(changed))


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "cache.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, sessions) -> str:
        return S.write_cache(sessions, self.path)

    def test_the_cache_round_trips(self) -> None:
        sessions = [session(turn(3)), session(turn(None))]
        self.write(sessions)
        self.assertEqual(S.read_cache(self.path), sessions)

    def test_the_hash_is_stable_and_content_addressed(self) -> None:
        sessions = [session(turn(3))]
        first = self.write(sessions)
        second = self.write(sessions)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_the_hash_moves_when_the_cache_moves(self) -> None:
        first = self.write([session(turn(3))])
        second = self.write([session(turn(4))])
        self.assertNotEqual(first, second)

    def test_the_cache_stores_features_not_scores(self) -> None:
        # A cached SCORE cannot be re-weighted, which is the one thing the
        # search must do. Every row carries the feature vector.
        self.write([session(turn(3))])
        row = json.loads(self.path.read_text().splitlines()[0])
        for t in row["turns"]:
            self.assertIn("features", t)
            self.assertTrue(all(isinstance(f, dict) for f in t["features"]))
        blob = self.path.read_text()
        for banned in ('"score"', '"total"', '"a0_score"'):
            self.assertNotIn(banned, blob)


if __name__ == "__main__":
    unittest.main()
