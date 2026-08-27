"""Citability is enforced on the read path, and invalidation never rewrites.

Both of these guard against defects that were committed, not hypothesised:
the first invalidation pass rewrote lab/results.jsonl in place, unlocked,
while a leased run was appending to it.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lab import provenance as P
from lab import report as R


def _lease(**kw) -> dict:
    base = {"purpose": "t", "isolated": "abc1234", "head": "abc1234",
            "verdict": "valid", "verified_ts": "2026-01-01T00:00:00",
            "expected_cells": 2, "completed_cells": 2, "matrix_complete": True}
    return {**base, **kw}


def _row(**kw) -> dict:
    """A row that passes every citability condition. Tests break exactly one."""
    base = {"schema_version": 2, "tag": "t", "scenario": "clean", "config": {},
            "ts": "2026-01-01T00:00:00", "seeds": [0], "code_dirty": False,
            "score": 0.9, "score_sd": 0.0, "hr10": 0.99, "mrr": 0.8, "mttc": 2.0,
            "agent_commit": "abc1234", "agent_in_worktree": True,
            "agent_sha256": "a" * 16, "scenario_sha256": "b" * 16,
            "dataset_sha256": "c" * 16, "catalog_sha256": "d" * 16,
            "lease": _lease()}
    return {**base, **kw}


# One forgery per rejection condition, each differing from a citable row in
# exactly one field, each carrying a distinctive score so a test can prove
# that score never reaches a rendered table.
FORGERIES = {
    "schema1":        dict(schema_version=1,                       score=0.9101),
    "dirty":          dict(code_dirty=True,                        score=0.9102),
    "inline_invalid": dict(invalid={"reason": "lease_broken"},     score=0.9103),
    "no_lease":       dict(lease=None,                             score=0.9104),
    "empty_lease":    dict(lease={},                               score=0.9105),
    "lease_invalid":  dict(lease=_lease(verdict="invalid"),        score=0.9106),
    "not_complete":   dict(lease=_lease(matrix_complete=False),    score=0.9107),
    "cell_mismatch":  dict(lease=_lease(completed_cells=1),        score=0.9108),
    "cells_missing":  dict(lease=_lease(expected_cells=None,
                                        completed_cells=None),     score=0.9109),
    "not_in_worktree": dict(agent_in_worktree=False,               score=0.9110),
    "worktree_absent": dict(agent_in_worktree=None,                score=0.9111),
    "commit_drift":   dict(agent_commit="deadbee",                 score=0.9112),
    "no_agent_hash":  dict(agent_sha256="",                        score=0.9113),
    "no_scenario_hash": dict(scenario_sha256="",                   score=0.9114),
    "no_dataset_hash": dict(dataset_sha256="",                     score=0.9115),
    "no_catalog_hash": dict(catalog_sha256="",                     score=0.9116),
}


class RowKeyTest(unittest.TestCase):
    def test_identity_is_stable_and_distinguishing(self) -> None:
        a = _row()
        self.assertEqual(P.row_key(a), P.row_key(dict(a)))
        self.assertNotEqual(P.row_key(a), P.row_key(_row(ts="2026-01-02T00:00:00")))
        self.assertNotEqual(P.row_key(a), P.row_key(_row(config={"w_neg": 0})))


class CitabilityTest(unittest.TestCase):
    def test_dirty_schema1_and_invalid_rows_are_all_excluded(self) -> None:
        good = _row(tag="good")
        cases = {
            "dirty": _row(tag="dirty", code_dirty=True),
            "schema1": _row(tag="schema1", schema_version=1),
            "inline": _row(tag="inline", invalid={"reason": "lease_broken"}),
        }
        rows = [good, *cases.values()]
        kept = P.citable(rows, marks={})
        self.assertEqual([r["tag"] for r in kept], ["good"])

    def test_a_ledger_invalidation_excludes_without_touching_the_row(self) -> None:
        good, doomed = _row(tag="good"), _row(tag="doomed")
        marks = {P.row_key(doomed): {"reason": "concurrent_repository_mutation"}}
        self.assertEqual([r["tag"] for r in P.citable([good, doomed], marks)], ["good"])
        self.assertNotIn("invalid", doomed, "the row itself must not be mutated")

    def test_every_exclusion_states_a_reason(self) -> None:
        rows = [_row(tag="dirty", code_dirty=True), _row(tag="ok")]
        why = P.reasons(rows, marks={})
        self.assertIn("code_dirty", why[P.row_key(rows[0])])
        self.assertEqual(why[P.row_key(rows[1])], "")


class InvalidationLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "invalidations.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_marking_appends_and_never_rewrites(self) -> None:
        rows = [_row(tag="a"), _row(tag="b", ts="2026-01-02T00:00:00")]
        P.mark(rows[:1], "first", path=self.path)
        first = self.path.read_text()
        P.mark(rows[1:], "second", path=self.path)
        after = self.path.read_text()
        self.assertTrue(after.startswith(first),
                        "an existing invalidation record was rewritten")
        self.assertEqual(len(after.strip().splitlines()), 2)

    def test_the_results_ledger_is_not_touched(self) -> None:
        results = Path(self._tmp.name) / "results.jsonl"
        rows = [_row(tag="a")]
        results.write_text(json.dumps(rows[0]) + "\n")
        before = results.read_bytes()
        P.mark(rows, "concurrent_repository_mutation", path=self.path)
        self.assertEqual(results.read_bytes(), before)

    def test_an_invalidation_round_trips(self) -> None:
        row = _row(tag="doomed")
        P.mark([row], "run_aborted", note="killed", path=self.path)
        marks = P.invalidations(self.path)
        self.assertEqual(marks[P.row_key(row)]["reason"], "run_aborted")
        self.assertEqual(P.citable([row], marks), [])


class ForgeryTest(unittest.TestCase):
    """Every rejection condition, one forged row each.

    A citability check is only worth having if each clause is load-bearing, so
    each forgery differs from a citable row in exactly ONE field. If any clause
    were dropped, the corresponding case here would start passing through.
    """

    def test_the_baseline_row_is_citable(self) -> None:
        # Without this, every assertion below could pass for the wrong reason.
        self.assertEqual(len(P.citable([_row(tag="ok")], marks={})), 1)

    def test_each_forgery_is_rejected_with_a_reason(self) -> None:
        for name, mutation in FORGERIES.items():
            with self.subTest(forgery=name):
                row = _row(tag=name, **mutation)
                self.assertEqual(P.citable([row], marks={}), [],
                                 f"{name} slipped past citable()")
                why = P.reasons([row], marks={})[P.row_key(row)]
                self.assertTrue(why, f"{name} was rejected without a reason")

    def test_no_forged_score_can_reach_a_table(self) -> None:
        rows = [_row(tag="ok", score=0.9999)]
        rows += [_row(tag=name, **mut) for name, mut in FORGERIES.items()]
        out = R.table(rows)
        self.assertIn("0.999900", out, "the one citable row was dropped too")
        for name, mut in FORGERIES.items():
            self.assertNotIn(f"{mut['score']:.6f}", out,
                             f"{name}'s score reached a rendered table")

    def test_every_forgery_is_named_in_the_exclusion_note(self) -> None:
        rows = [_row(tag=name, **mut) for name, mut in FORGERIES.items()]
        note = R.excluded(rows)
        for name in FORGERIES:
            self.assertIn(name, note, f"{name} was excluded silently")

    def test_isolation_drift_is_caught_per_row_not_just_by_the_lease(self) -> None:
        # The lease says valid and complete; the row says it measured an agent
        # from a different commit. That combination is exactly what the first
        # broken-isolation run produced.
        row = _row(tag="drift", agent_commit="deadbee", lease=_lease(isolated="abc1234"))
        why = P.reasons([row], marks={})[P.row_key(row)]
        self.assertIn("does not match the isolated commit", why)


class ReportPathTest(unittest.TestCase):
    """The table generator must not be able to quote a non-citable row."""

    def test_a_table_quotes_only_citable_rows(self) -> None:
        rows = [_row(tag="good", score=0.91),
                _row(tag="dirty", score=0.99, code_dirty=True),
                _row(tag="old", score=0.98, schema_version=1),
                _row(tag="void", score=0.97, invalid={"reason": "lease_broken"})]
        out = R.table(rows)
        self.assertIn("0.910000", out)
        for forbidden in ("0.990000", "0.980000", "0.970000"):
            self.assertNotIn(forbidden, out, "a non-citable score reached a table")

    def test_a_table_of_nothing_says_so(self) -> None:
        self.assertIn("no citable rows",
                      R.table([_row(tag="dirty", code_dirty=True)]))

    def test_exclusions_are_reported_not_silently_dropped(self) -> None:
        rows = [_row(tag="good"), _row(tag="dirty", code_dirty=True)]
        note = R.excluded(rows)
        self.assertIn("dirty", note)
        self.assertIn("code_dirty", note)


if __name__ == "__main__":
    unittest.main()
