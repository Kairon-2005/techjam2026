"""The benchmark harness, against the failures that made it necessary.

Phase 6B2's performance evidence was an uncommitted script, four of seven
repetitions, no ledger row and no diagnostic when it stalled. Each of those is
a test here, phrased as the thing that must NOT be possible any more.
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
import unittest
from pathlib import Path

import starter.agent as A
from lab import benchfixtures as F
from lab import benchmark as B
from lab import benchreport as BR
from lab import benchweights as W
from lab import lease as L
from lab import provenance as P
from lab import record as R
from tests.test_indexes import _catalog_file


def _row(rep: int, *, status="completed", fixtures=None, tag="t", **kw) -> dict:
    """A benchmark row that is citable unless a test makes it otherwise."""
    row = {
        "schema_version": 2, "benchmark_schema_version": 1, "kind": "benchmark",
        "tag": tag, "scenario": f"bench-rep-{rep}", "config": {}, "ts": f"ts{rep}",
        "rep": rep, "status": status, "code_dirty": False,
        "agent_in_worktree": True, "agent_commit": "abc1234",
        "agent_sha256": "a", "scenario_sha256": "b",
        "dataset_sha256": "c", "catalog_sha256": "d",
        "lease": {"verdict": "valid", "matrix_complete": True, "isolated": "abc1234",
                  "expected_cells": 7, "completed_cells": 7},
        "fixtures": fixtures if fixtures is not None else {},
    }
    row.update(kw)
    return row


def _fixtures(legacy: float, pure: float) -> dict:
    return {f.name: {"branch_class": f.branch_class,
                     "expects_selection": f.expects_selection,
                     "selection_mode": f.expects_selection,
                     "branch_as_registered": True,
                     "attribute_agrees": True, "state_agrees": True,
                     "legacy_ms": legacy, "pure_ms": pure,
                     "overhead_ms": pure - legacy,
                     "ratio": pure / legacy if legacy else None}
            for f in F.FIXTURES}


class LedgerWiringTest(unittest.TestCase):
    """The benchmark ledger must not lock the lease out of its own re-runs."""

    def test_the_benchmark_ledger_is_excluded_from_the_lease_dirty_set(self) -> None:
        # invalidations.jsonl was missing from this tuple once, and recording an
        # aborted run then made the lease refuse to start the re-run of that
        # same experiment. A second ledger must not repeat it.
        # This is the exact predicate _dirty() applies to each porcelain path.
        self.assertTrue("lab/benchmarks.jsonl".startswith(L.LEDGER_PREFIXES))
        self.assertFalse("lab/benchmark.py".startswith(L.LEDGER_PREFIXES),
                         "the RUNNER is code and must still dirty the tree")

    def test_appending_to_the_benchmark_ledger_is_not_code_dirt(self) -> None:
        self.assertIn("lab/benchmarks.jsonl", R.RESULT_LOGS)

    def test_the_runner_and_its_fixtures_are_watched_inputs(self) -> None:
        for path in ("lab/benchmark.py", "lab/benchfixtures.py", "lab/benchweights.py"):
            self.assertIn(path, L.WATCHED, path)


class FixtureTest(unittest.TestCase):
    """A fixture that has drifted off its branch is a mislabelled gate."""

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))
        cls.agent = A.Agent(cls.path)
        cls.pool = list(cls.agent.cat.cats)

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def test_every_fixture_takes_the_branch_it_registers(self) -> None:
        saved = self.agent.cfg
        try:
            for fixture in F.FIXTURES:
                cfg = {**saved, **fixture.cfg}
                self.agent.cfg = cfg
                got = B._agree(self.agent, fixture, cfg, self.pool[:fixture.pool_size])
                with self.subTest(fixture=fixture.name):
                    self.assertTrue(got["branch_as_registered"],
                                    f"{fixture.name} took {got['selection_mode']!r}, "
                                    f"registered {fixture.expects_selection!r}")
                    self.assertTrue(got["attribute_agrees"])
                    self.assertTrue(got["state_agrees"])
        finally:
            self.agent.cfg = saved

    def test_all_three_branch_classes_are_covered(self) -> None:
        classes = {f.branch_class for f in F.FIXTURES}
        for required in F.REQUIRED_CLASSES:
            self.assertIn(required, classes)

    def test_every_branch_class_has_a_gate(self) -> None:
        for fixture in F.FIXTURES:
            self.assertIn(fixture.branch_class, BR.GATES, fixture.name)

    def test_the_fixture_hash_moves_when_a_fixture_moves(self) -> None:
        before = F.fixture_sha256()
        saved = F.FIXTURES
        try:
            F.FIXTURES = saved[:-1]
            self.assertNotEqual(before, F.fixture_sha256())
        finally:
            F.FIXTURES = saved
        self.assertEqual(before, F.fixture_sha256())

    def test_prebuilt_states_do_not_share_a_mutated_list(self) -> None:
        # _states uses a shallow copy; that is only safe while neither arm
        # mutates a contained object in place. If one ever does, every state
        # after the first is contaminated and the measurement is of something
        # else entirely.
        fixture = F.BY_NAME["pool_utility_on_none_asked"]
        saved, cfg = self.agent.cfg, {**self.agent.cfg, **fixture.cfg}
        try:
            self.agent.cfg = cfg
            states = B._states(fixture, 3)
            arms = B._arms(self.agent, cfg, self.pool)
            arms["legacy"](states[0])
            arms["pure"](states[1])
            self.assertEqual(states[2]["broad_options"], [])
            self.assertEqual(states[2]["last_bits"], 0.0)
            self.assertIsNot(states[0]["broad_options"], states[2]["broad_options"])
        finally:
            self.agent.cfg = saved


class WeightsTest(unittest.TestCase):
    def test_the_frozen_weights_match_the_shadow_matrix_they_claim(self) -> None:
        derived = W.derive(P.load())
        if not derived:
            self.skipTest("lab/results.jsonl has no p6b2b-shadow rows here")
        self.assertEqual(sorted(derived), sorted(W.WEIGHTS))
        for branch, share in W.WEIGHTS.items():
            self.assertAlmostEqual(share, derived[branch], places=5, msg=branch)

    def test_the_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(W.WEIGHTS.values()), 1.0, places=5)

    def test_a_weighted_branch_has_a_representative_fixture(self) -> None:
        for branch in W.WEIGHTS:
            self.assertIn(W.REPRESENTATIVE[branch], F.BY_NAME, branch)

    def test_a_missing_branch_raises_instead_of_being_skipped(self) -> None:
        # Silently dropping the branch that failed to measure is the shortest
        # path from "one fixture aborted" to "the aggregate passed".
        with self.assertRaises(ValueError):
            W.weighted({"pool_selection": 0.1})


class SufficiencyTest(unittest.TestCase):
    """The four-of-seven guard: the reason this module exists."""

    def test_the_registered_count_comes_from_the_rows_not_the_caller(self) -> None:
        # A caller that could choose this number could choose the one its
        # evidence happens to satisfy.
        rows = [_row(i, fixtures=_fixtures(1.0, 1.05), config={"reps": 7})
                for i in range(4)]
        agg = BR.aggregate(rows)
        self.assertEqual(agg["required_reps"], 7)
        self.assertFalse(agg["sufficient"])

    def test_four_of_seven_is_reported_as_not_sufficient(self) -> None:
        rows = [_row(i, fixtures=_fixtures(1.0, 1.05)) for i in range(4)]
        agg = BR.aggregate(rows, required_reps=7)
        self.assertFalse(agg["sufficient"])
        self.assertIn("4 citable completed repetitions", agg["reason"])

    def test_seven_of_seven_is_sufficient(self) -> None:
        rows = [_row(i, fixtures=_fixtures(1.0, 1.05)) for i in range(7)]
        self.assertTrue(BR.aggregate(rows, required_reps=7)["sufficient"])

    def test_the_report_marks_an_insufficient_table_diagnostic(self) -> None:
        rows = [_row(i, fixtures=_fixtures(1.0, 1.05)) for i in range(4)]
        text = BR.report(tag="t", required_reps=7, rows=rows)
        self.assertIn("NOT SUFFICIENT", text)
        self.assertIn("DIAGNOSTIC", text)

    def test_an_aborted_repetition_does_not_count_towards_sufficiency(self) -> None:
        rows = [_row(i, fixtures=_fixtures(1.0, 1.05)) for i in range(6)]
        rows.append(_row(6, status="aborted", fixtures={},
                         invalid={"reason": "repetition_aborted"}))
        agg = BR.aggregate(rows, required_reps=7)
        self.assertEqual(agg["n_citable_completed"], 6)
        self.assertFalse(agg["sufficient"])

    def test_an_aborted_repetition_is_retained_but_not_citable(self) -> None:
        row = _row(0, status="aborted", invalid={"reason": "repetition_timeout"})
        self.assertEqual(P.citable([row]), [])
        self.assertIn("repetition_timeout", P.reasons([row])[P.row_key(row)])


class GateTest(unittest.TestCase):
    def test_a_pool_fixture_over_its_absolute_gate_fails(self) -> None:
        fx = _fixtures(1.0, 1.05)
        fx["pool_utility_on_none_asked"].update(
            {"legacy_ms": 10.0, "pure_ms": 10.6, "overhead_ms": 0.6, "ratio": 1.06})
        verdicts = BR.evaluate(BR.per_branch([_row(0, fixtures=fx)]))
        bad = [v for v in verdicts
               if v["fixture"] == "pool_utility_on_none_asked" and v["metric"] == "overhead_ms"]
        self.assertEqual(len(bad), 1)
        self.assertFalse(bad[0]["passes"])

    def test_a_pool_fixture_over_its_ratio_gate_fails(self) -> None:
        fx = _fixtures(1.0, 1.05)
        fx["pool_utility_on_none_asked"].update(
            {"legacy_ms": 1.0, "pure_ms": 1.3, "overhead_ms": 0.3, "ratio": 1.3})
        verdicts = BR.evaluate(BR.per_branch([_row(0, fixtures=fx)]))
        bad = [v for v in verdicts
               if v["fixture"] == "pool_utility_on_none_asked" and v["metric"] == "ratio"]
        self.assertEqual(len(bad), 1)
        self.assertFalse(bad[0]["passes"])

    def test_a_sub_floor_ratio_is_diagnostic_and_still_printed(self) -> None:
        # R2.1: below a 0.10ms legacy median the ratio is arithmetic on noise
        # -- 0.0007ms -> 0.0009ms is a "ratio of 1.29" and means nothing -- but
        # a check that did not run must be VISIBLE as withheld, not absent.
        fx = _fixtures(1.0, 1.05)
        fx["first_two_other"].update({"legacy_ms": 0.0007, "pure_ms": 0.0009,
                                      "overhead_ms": 0.0002, "ratio": 1.2857})
        rows = [_row(0, fixtures=fx)]
        verdicts = {v["metric"]: v for v in BR.evaluate(BR.per_branch(rows))
                    if v["fixture"] == "first_two_other"}
        self.assertEqual(set(verdicts), {"overhead_ms", "ratio"})
        self.assertTrue(verdicts["ratio"]["diagnostic"])
        self.assertTrue(verdicts["ratio"]["passes"], "a diagnostic cannot fail")
        self.assertFalse(verdicts["overhead_ms"]["diagnostic"])
        self.assertIn("DIAGNOSTIC ONLY", BR.report(tag="t", rows=rows))

    def test_the_floor_is_not_an_exemption_the_micro_budget_still_binds(self) -> None:
        # The failure mode a floor invites: "small baseline" becoming "no gate".
        # Below the floor the requirement is the TIGHTEST budget in the table.
        fx = _fixtures(1.0, 1.05)
        fx["pool_empty"].update({"legacy_ms": 0.05, "pure_ms": 0.55,
                                 "overhead_ms": 0.50, "ratio": 11.0})
        verdicts = {v["metric"]: v for v in
                    BR.evaluate(BR.per_branch([_row(0, fixtures=fx)]))
                    if v["fixture"] == "pool_empty"}
        self.assertEqual(verdicts["overhead_ms"]["limit"],
                         BR.MICRO_PATH_MAX_OVERHEAD_MS)
        self.assertFalse(verdicts["overhead_ms"]["passes"],
                         "+0.50ms under the floor must fail the 0.10ms budget")

    def test_below_the_floor_the_category_budget_tightens(self) -> None:
        # R2.1 is not uniformly permissive: category-only goes from 0.25ms to
        # 0.10ms below the floor. An overhead of 0.20ms passes above it and
        # fails below it.
        fx = _fixtures(1.0, 1.05)
        fx["easier"].update({"legacy_ms": 0.02, "pure_ms": 0.22,
                             "overhead_ms": 0.20, "ratio": 11.0})
        under = {v["metric"]: v for v in BR.evaluate(BR.per_branch([_row(0, fixtures=fx)]))
                 if v["fixture"] == "easier"}
        self.assertFalse(under["overhead_ms"]["passes"])
        fx["easier"].update({"legacy_ms": 1.0, "pure_ms": 1.20, "ratio": 1.2})
        over = {v["metric"]: v for v in BR.evaluate(BR.per_branch([_row(0, fixtures=fx)]))
                if v["fixture"] == "easier"}
        self.assertTrue(over["overhead_ms"]["passes"])
        self.assertEqual(over["overhead_ms"]["limit"], 0.25)


class GateRevisionFalsificationTest(unittest.TestCase):
    """R2.1 was written after seeing the result it changes the verdict on.

    A correction like that is worth nothing unless it still REJECTS the
    implementation the gate existed to reject. If an edit to the floor ever
    starts admitting the eager 6B2 arm, this breaks the build -- which is the
    only reason to believe the floor is a specification fix rather than a knob.
    """

    def rows(self) -> list[dict]:
        rows = [r for r in P.load(B.LOG)
                if r.get("tag") == "p6b2-eager-control" and r.get("kind") == "benchmark"]
        if not rows:
            self.skipTest("no p6b2-eager-control rows in this checkout")
        return rows

    def test_the_eager_implementation_still_fails_under_r2_1(self) -> None:
        agg = BR.aggregate(self.rows())
        failed = {v["fixture"] for v in agg["verdicts"]
                  if not v["passes"] and not v["diagnostic"]}
        # The four micro-paths: eager scans every facet to decide branches that
        # read none of them, at +16.7ms against a 0.10ms budget.
        for name in ("first_two_other", "probe_cycle", "easier", "give_up"):
            self.assertIn(name, failed, name)
        self.assertIn("pool_utility_on_none_asked", failed)
        self.assertGreaterEqual(len(failed), 5)
        self.assertFalse(agg["weighted_passes"],
                         "the live-weighted aggregate must still reject eager")
        self.assertGreater(agg["weighted_overhead_ms"], BR.AGGREGATE_GATE_MS * 10)

    def test_the_floor_changes_exactly_one_eager_verdict(self) -> None:
        # The claim in notes/35, checked rather than asserted in prose.
        verdicts = BR.aggregate(self.rows())["verdicts"]
        withheld = [v for v in verdicts if v["diagnostic"] and v["actual"] > v["limit"]]
        self.assertEqual([v["fixture"] for v in withheld if v["branch_class"] == F.POOL],
                         ["pool_empty"])

    def test_the_overhead_median_is_paired_not_a_difference_of_medians(self) -> None:
        rows = []
        for i, (legacy, pure) in enumerate([(1.0, 1.2), (3.0, 3.1), (2.0, 2.5)]):
            rows.append(_row(i, fixtures=_fixtures(legacy, pure)))
        got = BR.per_branch(rows)["easier"]
        self.assertAlmostEqual(got["overhead_ms"], statistics.median([0.2, 0.1, 0.5]))
        self.assertNotAlmostEqual(got["overhead_ms"],
                                  statistics.median([1.2, 3.1, 2.5])
                                  - statistics.median([1.0, 3.0, 2.0]))

    def test_the_screen_stops_only_past_twenty_percent_over_a_gate(self) -> None:
        fx = _fixtures(1.0, 1.05)
        fx["pool_utility_on_none_asked"].update(
            {"legacy_ms": 10.0, "pure_ms": 10.55, "overhead_ms": 0.55, "ratio": 1.055})
        self.assertFalse(BR.screen([_row(0, fixtures=fx)])["stop"])  # 0.55 <= 0.50*1.2
        fx["pool_utility_on_none_asked"].update(
            {"legacy_ms": 10.0, "pure_ms": 10.7, "overhead_ms": 0.7, "ratio": 1.07})
        verdict = BR.screen([_row(0, fixtures=fx)])
        self.assertTrue(verdict["stop"])
        self.assertIn("pool_utility_on_none_asked", verdict["reason"])

    def test_the_screen_stops_when_there_is_nothing_citable(self) -> None:
        self.assertTrue(BR.screen([])["stop"])


class CoverageTest(unittest.TestCase):
    def test_a_repetition_missing_a_fixture_is_rejected(self) -> None:
        fx = _fixtures(1.0, 1.05)
        fx.pop("give_up")
        self.assertIn("give_up", B._fixture_coverage({"fixtures": fx}))

    def test_disagreeing_arms_are_rejected(self) -> None:
        fx = _fixtures(1.0, 1.05)
        fx["easier"]["attribute_agrees"] = False
        self.assertIn("meaningless", B._fixture_coverage({"fixtures": fx}))

    def test_a_fixture_off_its_registered_branch_is_rejected(self) -> None:
        fx = _fixtures(1.0, 1.05)
        fx["easier"].update({"branch_as_registered": False,
                             "selection_mode": "pool_selection"})
        note = B._fixture_coverage({"fixtures": fx})
        self.assertIn("easier", note)
        self.assertIn("pool_selection", note)

    def test_a_complete_repetition_is_accepted(self) -> None:
        self.assertEqual(B._fixture_coverage({"fixtures": _fixtures(1.0, 1.05)}), "")


class ProcessFactsTest(unittest.TestCase):
    """Four hours with no output produced no diagnostic. That is now impossible."""

    def test_a_live_pid_yields_state_cpu_and_rss(self) -> None:
        got = B.ps_snapshot(os.getpid())
        self.assertNotIn("error", got)
        for key in ("stat", "cpu_pct", "cpu_time", "rss_kb"):
            self.assertIn(key, got)
        self.assertGreater(got["rss_kb"], 0)

    def test_a_dead_pid_says_so_instead_of_raising(self) -> None:
        self.assertIn("error", B.ps_snapshot(999_999_999))

    def test_memory_is_recorded_with_its_units(self) -> None:
        got = B.rss_bytes()
        self.assertGreater(got["peak_rss_bytes"], 0)
        self.assertIn(got["ru_maxrss_units"], ("bytes", "kilobytes"))


class RepetitionTest(unittest.TestCase):
    """A repetition really does run in its own process, and really is bounded."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.catalog = _catalog_file(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_one_repetition_measures_every_fixture_in_a_child_process(self) -> None:
        record = B.run_repetition(0, warmup=2, measured=5, catalog=self.catalog)
        self.assertEqual(record["status"], "completed", record.get("stderr_tail"))
        self.assertEqual(set(record["fixtures"]), set(F.BY_NAME))
        self.assertEqual(B._fixture_coverage(record), "")
        self.assertNotEqual(record.get("child_cpu_seconds"), None)
        self.assertIn("peak_rss_bytes", record["memory"])
        for got in record["fixtures"].values():
            self.assertEqual(got["legacy"]["dispatches"], 5)
            self.assertIn("cpu_ms", got["legacy"])

    def test_arm_order_alternates_with_the_repetition_index(self) -> None:
        even = B.run_repetition(0, warmup=1, measured=2, catalog=self.catalog)
        odd = B.run_repetition(1, warmup=1, measured=2, catalog=self.catalog)
        self.assertEqual(even["arm_order"], ["legacy", "pure"])
        self.assertEqual(odd["arm_order"], ["pure", "legacy"])

    def test_a_repetition_that_overruns_is_killed_and_retained(self) -> None:
        # The four-hour stall produced no record at all. A timeout must produce
        # one, marked non-citable, rather than nothing.
        record = B.run_repetition(0, warmup=1000, measured=100000,
                                  catalog=self.catalog, timeout=3)
        self.assertEqual(record["status"], "aborted")
        self.assertIn("timeout", record["note"])
        self.assertTrue(record["probes"], "no process facts captured before the kill")
        self.assertEqual(record["probes"][-1]["why"], "timeout_kill")
        self.assertIn("invalid", record)
        self.assertEqual(P.citable([_row(0, **{k: record[k] for k in ("status", "invalid")})]), [])

    def test_the_child_publishes_a_projection_before_doing_the_work(self) -> None:
        record = B.run_repetition(0, warmup=1, measured=2, catalog=self.catalog)
        self.assertIsNotNone(record["plan"], "no projection: the watchdog is unarmed")
        self.assertEqual(record["plan"]["fixtures"], len(F.FIXTURES))
        self.assertGreaterEqual(record["plan"]["projected_seconds"], 0)
        self.assertEqual(record["plan"]["arm_order"], record["arm_order"])


class StreamingTest(unittest.TestCase):
    def test_each_repetition_is_flushed_before_the_next_one_starts(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        catalog = _catalog_file(Path(tmp.name))
        log = Path(tmp.name) / "benchmarks.jsonl"
        saved = B.LOG
        try:
            B.LOG = log
            rows = B.repetitions("unit-test", 2, 1, 2, catalog, timeout=120)
        finally:
            B.LOG = saved
        written = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        self.assertEqual(len(written), 2)
        self.assertEqual(len(rows), 2)
        for row in written:
            self.assertEqual(row["kind"], "benchmark")
            self.assertEqual(row["fixture_sha256"], F.fixture_sha256())
            self.assertEqual(row["weights_frozen_ts"], W.FROZEN_TS)
            for field in ("agent_sha256", "catalog_sha256", "python", "elapsed_seconds"):
                self.assertIn(field, row)

    def test_an_unleased_row_is_recorded_and_is_not_citable(self) -> None:
        # Refusing to record without a lease would lose the measurement; the
        # rule is that it is kept and never quotable, exactly as in lab/record.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        catalog = _catalog_file(Path(tmp.name))
        log = Path(tmp.name) / "benchmarks.jsonl"
        saved = B.LOG
        try:
            B.LOG = log
            B.repetitions("unit-test", 1, 1, 2, catalog, timeout=120)
        finally:
            B.LOG = saved
        rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        self.assertIsNone(rows[0]["lease"])
        self.assertEqual(P.citable(rows), [])


if __name__ == "__main__":
    unittest.main()
