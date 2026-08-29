"""The sup-val gates and the one-public-run guard.

These decide whether an arm reaches the public 200 and whether `score_default`
moves, so they are the last place in the project where a plausible-looking
implementation would be expensive. Every branch is exercised on fixtures: the
real runs happen once and cannot be used to test the code that judges them.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lab import public as PB
from lab import supval as SV


def row(arm: str, score: float, mrr: float, hr10: float = 0.5,
        mttc: float = 6.0) -> dict:
    return {"arm": arm, "score": score, "mrr": mrr, "hr10": hr10, "mttc": mttc}


A0 = row("a0", 0.4374, 0.2102)


class FloorTest(unittest.TestCase):
    """Step 1. "Did not regress badly" -- and nothing more than that."""

    def test_a_clean_improvement_passes_the_floors(self) -> None:
        got = SV.judge("a1", row("a1", 0.7177, 0.4388), A0)
        self.assertTrue(got["passed_floors"])

    def test_a_composite_collapse_fails(self) -> None:
        got = SV.judge("a1", row("a1", 0.4374 - 0.0051, 0.2102), A0)
        self.assertFalse(got["passed_floors"])
        self.assertIn("FAILS THE FLOORS", got["note"])

    def test_an_mrr_collapse_fails(self) -> None:
        got = SV.judge("a1", row("a1", 0.4374, 0.2102 - 0.0101), A0)
        self.assertFalse(got["passed_floors"])

    def test_the_floors_are_the_preregistered_numbers(self) -> None:
        self.assertEqual((SV.FLOOR_COMPOSITE, SV.FLOOR_MRR), (-0.005, -0.010))

    def test_a2_must_hold_hr10_and_mttc_exactly(self) -> None:
        # notes/44 section 9: a permutation cannot move either, so movement is
        # an implementation defect and is investigated as a bug.
        moved_hr = SV.judge("a2", row("a2", 0.44, 0.22, hr10=0.5 + 1e-9), A0)
        self.assertFalse(moved_hr["passed_floors"])
        moved_turn = SV.judge("a2", row("a2", 0.44, 0.22, mttc=6.0 + 1e-9), A0)
        self.assertFalse(moved_turn["passed_floors"])
        held = SV.judge("a2", row("a2", 0.44, 0.22), A0)
        self.assertTrue(held["passed_floors"])

    def test_a1_is_not_held_to_the_invariance_floors(self) -> None:
        # A1 reweights the whole pool; moving HR@10 is what it is FOR.
        got = SV.judge("a1", row("a1", 0.7177, 0.4388, hr10=0.86, mttc=3.2), A0)
        self.assertTrue(got["passed_floors"])
        self.assertNotIn("hr10 exactly invariant", got["floors"])


class QualificationTest(unittest.TestCase):
    """Step 1b. A floor is not a signal."""

    def test_clearing_the_floors_is_not_qualifying(self) -> None:
        # The exact case revision 2 would have let through: harmless, and
        # therefore not worth the single public confirmation.
        got = SV.judge("a1", row("a1", 0.4374, 0.2102), A0)
        self.assertTrue(got["passed_floors"])
        self.assertFalse(got["qualified"])
        self.assertIn("no demonstrated positive signal", got["note"])

    def test_mrr_must_be_strictly_positive(self) -> None:
        self.assertFalse(SV.judge("a1", row("a1", 0.44, 0.2102), A0)["qualified"])
        self.assertTrue(SV.judge("a1", row("a1", 0.44, 0.2102 + 1e-9),
                                 A0)["qualified"])

    def test_composite_may_tie_but_not_fall(self) -> None:
        self.assertTrue(SV.judge("a1", row("a1", 0.4374, 0.22), A0)["qualified"])
        self.assertFalse(SV.judge("a1", row("a1", 0.4374 - 1e-9, 0.22),
                                  A0)["qualified"])


class FinalistTest(unittest.TestCase):
    """Step 2. One finalist, by a fixed order, decided before any of them ran."""

    def choose(self, a1: dict, a2: dict) -> dict:
        rows = {"a0": A0, "a1": a1, "a2": a2}
        judgements = {arm: SV.judge(arm, rows[arm], A0) for arm in ("a1", "a2")}
        return SV.finalist(judgements, rows)

    def test_neither_qualifying_is_a_negative_result_not_a_near_miss(self) -> None:
        got = self.choose(row("a1", 0.4374, 0.2102), row("a2", 0.4374, 0.2102))
        self.assertIsNone(got["finalist"])
        self.assertIn("negative result", got["reason"])
        self.assertIn("A0 remains the default", got["reason"])

    def test_one_qualifier_is_the_finalist(self) -> None:
        got = self.choose(row("a1", 0.72, 0.44), row("a2", 0.4374, 0.2102))
        self.assertEqual(got["finalist"], "a1")

    def test_higher_sup_val_mrr_wins(self) -> None:
        got = self.choose(row("a1", 0.50, 0.30), row("a2", 0.60, 0.25))
        self.assertEqual(got["finalist"], "a1")
        self.assertIn("higher sup-val MRR", got["reason"])

    def test_a_tie_on_mrr_falls_to_composite(self) -> None:
        got = self.choose(row("a1", 0.50, 0.30), row("a2", 0.60, 0.30))
        self.assertEqual(got["finalist"], "a2")
        self.assertIn("composite", got["reason"])

    def test_a_dead_heat_goes_to_fewer_moving_parts(self) -> None:
        # A1 needs no dependency, no artifact and no runtime. A dead heat on
        # quality should not buy a dependency.
        got = self.choose(row("a1", 0.60, 0.30), row("a2", 0.60, 0.30))
        self.assertEqual(got["finalist"], "a1")
        self.assertIn("fewer moving parts", got["reason"])


class FrozenArmTest(unittest.TestCase):
    """sup-val refuses to validate an arm that is not frozen."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self._a1, self._a2 = SV.A1_WEIGHTS, SV.A2_LAMBDA
        self.addCleanup(lambda: (setattr(SV, "A1_WEIGHTS", self._a1),
                                 setattr(SV, "A2_LAMBDA", self._a2)))

    def write(self, name: str, payload: dict) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_a_no_op_a1_is_refused(self) -> None:
        SV.A1_WEIGHTS = self.write("a1.json", {"no_op": True, "trials": 189,
                                               "weights": {}})
        with self.assertRaises(SV.FrozenArmError) as caught:
            SV.a1_config()
        self.assertIn("not finalist-eligible", str(caught.exception))

    def test_a_short_a1_search_is_refused(self) -> None:
        SV.A1_WEIGHTS = self.write("a1.json", {"no_op": False, "trials": 63,
                                               "weights": {}})
        with self.assertRaises(SV.FrozenArmError):
            SV.a1_config()

    def test_a_lambda_zero_a2_is_refused(self) -> None:
        SV.A2_LAMBDA = self.write("a2.json", {"no_op": True, "best_lambda": 0.0,
                                              "ok": True})
        with self.assertRaises(SV.FrozenArmError) as caught:
            SV.a2_config()
        self.assertIn("semantic signal failed", str(caught.exception))

    def test_a_smoke_a2_is_refused(self) -> None:
        SV.A2_LAMBDA = self.write("a2.json", {"no_op": False, "best_lambda": 1.0,
                                              "ok": True, "smoke": True})
        with self.assertRaises(SV.FrozenArmError):
            SV.a2_config()

    def test_an_absent_freeze_is_refused(self) -> None:
        SV.A1_WEIGHTS = self.dir / "absent.json"
        with self.assertRaises(SV.FrozenArmError):
            SV.a1_config()


class PublicGuardTest(unittest.TestCase):
    """Exactly once, for exactly one arm. Enforced, not remembered."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ledger = Path(self._tmp.name) / "public.jsonl"
        self._verdict = SV.verdict
        self.addCleanup(lambda: setattr(SV, "verdict", self._verdict))

    def stub(self, **kw):
        SV.verdict = lambda *a, **k: {"ok": True, "finalist": "a1", **kw}

    def test_an_existing_confirmation_row_stops_a_second_run(self) -> None:
        self.stub()
        self.ledger.write_text(
            json.dumps({"tag": PB.TAG_CLEAN, "score": 0.9}) + "\n",
            encoding="utf-8")
        with self.assertRaises(PB.PublicRunError) as caught:
            PB.guard(self.ledger)
        self.assertIn("already exist", str(caught.exception))
        self.assertIn("never re-run to retune", str(caught.exception))

    def test_an_empty_ledger_admits_the_finalist(self) -> None:
        self.stub()
        self.assertEqual(PB.guard(self.ledger)["finalist"], "a1")

    def test_no_finalist_means_no_public_run(self) -> None:
        SV.verdict = lambda *a, **k: {"ok": True, "finalist": None,
                                      "reason": "neither qualified"}
        with self.assertRaises(PB.PublicRunError) as caught:
            PB.guard(self.ledger)
        self.assertIn("negative result", str(caught.exception))

    def test_an_incomplete_sup_val_stops_the_run(self) -> None:
        SV.verdict = lambda *a, **k: {"ok": False, "reason": "no citable row"}
        with self.assertRaises(PB.PublicRunError):
            PB.guard(self.ledger)


class PublicGateTest(unittest.TestCase):
    """Section 9, on fixtures. The real run happens once."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ledger = Path(self._tmp.name) / "public.jsonl"

    def write(self, clean_arm: dict, robust_delta: float = 0.0,
              hr10_delta: float = 0.0, slice_delta: float = 0.0) -> None:
        lease = {"purpose": "p", "isolated": "abc1234", "verdict": "valid",
                 "matrix_complete": True, "expected_cells": 1,
                 "completed_cells": 1}
        stamp = {"schema_version": 2, "code_dirty": False, "lease": lease,
                 "agent_commit": "abc1234", "agent_in_worktree": True,
                 "agent_sha256": "a" * 16, "scenario_sha256": "b" * 16,
                 "dataset_sha256": "c" * 16, "catalog_sha256": "d" * 16}
        a0_slices = {"clean": {"mrr": 0.85}}
        rows = [
            {**stamp, "tag": PB.TAG_CLEAN, "scenario": "clean",
             "config_label": "a0", "score": 0.932067, "hr10": 0.995,
             "mrr": 0.852556, "mttc": 2.06, "ts": "1", "slices": a0_slices},
            {**stamp, "tag": PB.TAG_CLEAN, "scenario": "clean",
             "config_label": "a1_finalist", "ts": "2",
             "slices": {"clean": {"mrr": 0.85 + slice_delta}}, **clean_arm},
        ]
        for i, scenario in enumerate(PB.SH.ROBUSTNESS_SCENARIOS):
            rows.append({**stamp, "tag": PB.TAG_ROBUST, "scenario": scenario,
                         "config_label": "a0", "score": 0.9, "hr10": 0.99,
                         "mrr": 0.8, "mttc": 2.1, "ts": f"a{i}"})
            rows.append({**stamp, "tag": PB.TAG_ROBUST, "scenario": scenario,
                         "config_label": "a1_finalist",
                         "score": 0.9 + robust_delta, "hr10": 0.99 + hr10_delta,
                         "mrr": 0.8, "mttc": 2.1, "ts": f"b{i}"})
        self.ledger.write_text("".join(json.dumps(r) + "\n" for r in rows),
                               encoding="utf-8")

    def test_a_passing_finalist_moves_score_default(self) -> None:
        self.write({"score": 0.945, "hr10": 0.995, "mrr": 0.87, "mttc": 2.05},
                   slice_delta=0.02)
        got = PB.gates("a1", self.ledger)
        self.assertTrue(got["all_gates_pass"], got["checks"])
        self.assertIn("MOVES", got["score_default"])

    def test_a_clean_mrr_short_of_the_bar_fails(self) -> None:
        # +0.009 is an improvement and is still a failure: the bar is +0.010
        # and it was set before the arm ran.
        self.write({"score": 0.94, "hr10": 0.995, "mrr": 0.852556 + 0.009,
                    "mttc": 2.05}, slice_delta=0.009)
        got = PB.gates("a1", self.ledger)
        self.assertFalse(got["all_gates_pass"])
        self.assertFalse(got["checks"]["clean MRR delta >= +0.010"]["pass"])
        self.assertIn("STAYS A0", got["score_default"])
        self.assertIn("NOT adjusted and re-run", got["score_default"])

    def test_a_composite_below_the_shipped_default_fails(self) -> None:
        self.write({"score": 0.93, "hr10": 0.995, "mrr": 0.87, "mttc": 2.05},
                   slice_delta=0.02)
        got = PB.gates("a1", self.ledger)
        self.assertFalse(got["checks"][
            f"composite not below {PB.GATE_COMPOSITE_FLOOR}"]["pass"])

    def test_a_single_slice_regression_fails(self) -> None:
        self.write({"score": 0.945, "hr10": 0.995, "mrr": 0.87, "mttc": 2.05},
                   slice_delta=-1e-9)
        got = PB.gates("a1", self.ledger)
        self.assertFalse(got["checks"]["no official slice MRR regression"]["pass"])

    def test_a_robustness_score_drop_fails(self) -> None:
        self.write({"score": 0.945, "hr10": 0.995, "mrr": 0.87, "mttc": 2.05},
                   slice_delta=0.02, robust_delta=-0.0051)
        got = PB.gates("a1", self.ledger)
        self.assertFalse(got["checks"][
            "robustness paired score delta >= -0.005"]["pass"])

    def test_a_robustness_hr10_drop_fails(self) -> None:
        self.write({"score": 0.945, "hr10": 0.995, "mrr": 0.87, "mttc": 2.05},
                   slice_delta=0.02, hr10_delta=-0.011)
        got = PB.gates("a1", self.ledger)
        self.assertFalse(got["checks"][
            "robustness paired HR@10 drop <= 0.01"]["pass"])

    def test_a_non_citable_row_is_not_quoted(self) -> None:
        self.write({"score": 0.945, "hr10": 0.995, "mrr": 0.87, "mttc": 2.05},
                   slice_delta=0.02)
        rows = [json.loads(l) for l in
                self.ledger.read_text(encoding="utf-8").splitlines()]
        rows[1]["code_dirty"] = True
        self.ledger.write_text("".join(json.dumps(r) + "\n" for r in rows),
                               encoding="utf-8")
        got = PB.gates("a1", self.ledger)
        self.assertFalse(got["ok"])
        self.assertIn("no citable clean rows", got["reason"])

    def test_the_gate_constants_are_the_preregistered_ones(self) -> None:
        self.assertEqual(PB.GATE_CLEAN_MRR, 0.010)
        self.assertEqual(PB.GATE_COMPOSITE_FLOOR, 0.932067)
        self.assertEqual(PB.GATE_ROBUST_SCORE, -0.005)
        self.assertEqual(PB.GATE_ROBUST_HR10, -0.01)


if __name__ == "__main__":
    unittest.main()
