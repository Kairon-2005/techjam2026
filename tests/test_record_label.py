"""The recorded row must carry its own config label.

`matrix()` set row["config_label"] on the dict `cell()` returned, but `cell()`
had already serialised and written that row -- to the lease journal, or
straight to lab/results.jsonl -- before returning. The label therefore reached
the caller and never the ledger: every row written before this fix is missing
it, and a reader had to reverse-map a row back to its label by comparing
row["config"] against the shard's config dict. Nothing failed loudly, because
lab/report.py:_label() falls back to a config-derived string -- which is
exactly why it survived this long.

Anything that must end up in the ledger has to be in the row BEFORE cell()
writes it. That is the invariant these cases pin.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from lab import lease as L
from lab import record as R
from lab import scenarios as S

_RESULT = {"recommended_technical_score": 0.5, "hit_rate_at_10": 0.5,
           "mrr": 0.5, "mttc": 3.0, "telemetry": {}, "scenario_metrics": {}}


class CellLabelTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self._tmp.name) / "journal.jsonl"
        # Journalling to a temp file exercises the real write path inside
        # cell() without appending to lab/results.jsonl, which is append-only
        # and must never gain a row that no run produced.
        self._saved_env = os.environ.get(L.JOURNAL_ENV)
        os.environ[L.JOURNAL_ENV] = str(self.journal)
        # A stand-in scenario: cell() reads .source/.official/.dataset off it
        # and hands it to S.run, which is stubbed here. Running a real scenario
        # needs the 58 MB catalog and minutes of wall clock, and neither is
        # what this test is about.
        self._saved_run = S.run
        self._name = "labeltest"
        S.BY_NAME[self._name] = S.Scenario(name=self._name, probes="label plumbing")
        S.run = lambda *a, **k: dict(_RESULT)

    def tearDown(self) -> None:
        S.run = self._saved_run
        S.BY_NAME.pop(self._name, None)
        if self._saved_env is None:
            os.environ.pop(L.JOURNAL_ENV, None)
        else:
            os.environ[L.JOURNAL_ENV] = self._saved_env
        self._tmp.cleanup()

    def _written(self) -> list[dict]:
        return [json.loads(line) for line
                in self.journal.read_text(encoding="utf-8").splitlines() if line]

    def _cell(self, **kw) -> dict:
        return R.cell(self._name, {}, (0,), (None, None, None, None),
                      tag="labeltest", **kw)

    def test_a_label_passed_to_cell_reaches_the_written_row(self) -> None:
        self._cell(label="compat_anchor")
        written, = self._written()
        self.assertEqual(written["config_label"], "compat_anchor")

    def test_the_label_is_written_and_not_merely_returned(self) -> None:
        # The defect precisely: the returned dict carried the label and the
        # serialised row did not.
        row = self._cell(label="shadow")
        written, = self._written()
        self.assertEqual(row["config_label"], written["config_label"])

    def test_an_unlabelled_cell_writes_no_label_key(self) -> None:
        # Rows written before this fix have no config_label at all. Stamping
        # "" onto every unlabelled row would change the ledger's shape for no
        # reader's benefit: report.py:_label() already falls back to a
        # config-derived string when the key is absent.
        self._cell()
        written, = self._written()
        self.assertNotIn("config_label", written)


if __name__ == "__main__":
    unittest.main()
