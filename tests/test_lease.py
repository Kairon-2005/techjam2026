"""The experiment lease: exclusion, verification, and journalling.

These exist because the failure they guard against already happened. A matrix
recorded while a second session was committing produced eight rows carrying
three different agent_commit values, every one of them reporting
code_dirty=false, because provenance was sampled per row instead of being
checked across the run.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from lab import lease as L


class LeaseLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = L.LOCK_PATH
        L.LOCK_PATH = Path(self._tmp.name) / "lock"

    def tearDown(self) -> None:
        L.LOCK_PATH = self._saved
        L._ACTIVE = None
        self._tmp.cleanup()

    def test_the_lock_is_exclusive(self) -> None:
        L._acquire("first")
        with self.assertRaises(L.LeaseBusy):
            L._acquire("second")

    def test_a_stale_lock_is_broken_once(self) -> None:
        # A holder that died without releasing must not block the repository
        # forever; a holder that is alive must.
        L.LOCK_PATH.write_text(json.dumps({"pid": 2 ** 30, "purpose": "dead"}))
        L._acquire("after the corpse")
        self.assertEqual(json.loads(L.LOCK_PATH.read_text())["pid"], os.getpid())

    def test_a_live_holder_is_not_evicted(self) -> None:
        L.LOCK_PATH.write_text(json.dumps({"pid": os.getpid(), "purpose": "live"}))
        with self.assertRaises(L.LeaseBusy):
            L._acquire("intruder")

    def test_the_lease_releases_the_lock(self) -> None:
        with L.lease("smoke", isolate=False, log=Path(self._tmp.name) / "r.jsonl"):
            self.assertTrue(L.LOCK_PATH.exists())
            self.assertIsNotNone(L.current())
        self.assertFalse(L.LOCK_PATH.exists())
        self.assertIsNone(L.current())


class LeaseVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved_lock, self._saved_watched = L.LOCK_PATH, L.WATCHED
        L.LOCK_PATH = self.root / "lock"
        self.watched = self.root / "agent.py"
        self.watched.write_text("original")
        L.WATCHED = ("agent.py",)

    def tearDown(self) -> None:
        L.LOCK_PATH, L.WATCHED = self._saved_lock, self._saved_watched
        L._ACTIVE = None
        self._tmp.cleanup()

    def _lease(self) -> L.Lease:
        return L.Lease(purpose="t", before=L.fingerprint(self.root),
                       origin=self.root, run_dir=self.root)

    def test_an_untouched_run_verifies(self) -> None:
        self.assertEqual(self._lease().verify(), "")

    def test_a_rewritten_input_breaks_the_lease(self) -> None:
        obj = self._lease()
        self.watched.write_text("rewritten mid-run")
        self.assertIn("agent.py", obj.verify())

    def test_broken_lease_stamps_every_row_invalid(self) -> None:
        log = self.root / "results.jsonl"
        obj = self._lease()
        obj.journal = [{"tag": "t", "score": 0.5}, {"tag": "t", "score": 0.6}]
        self.watched.write_text("rewritten mid-run")
        obj.broke = obj.verify()
        obj.verdict = "invalid"
        L._flush(obj, log)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["invalid"]["reason"], "lease_broken")
            self.assertEqual(row["lease"]["verdict"], "invalid")


class LeaseJournalTest(unittest.TestCase):
    """Rows must not reach the ledger before the lease has verified them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved = L.LOCK_PATH
        L.LOCK_PATH = self.root / "lock"

    def tearDown(self) -> None:
        L.LOCK_PATH = self._saved
        L._ACTIVE = None
        self._tmp.cleanup()

    def test_rows_are_held_until_the_block_exits(self) -> None:
        from lab import record as R
        log = self.root / "results.jsonl"
        with L.lease("journal", isolate=False, log=log) as ls:
            ls.journal.append({"tag": "held", "score": 1.0})
            self.assertFalse(log.exists(), "a row reached the ledger unverified")
        self.assertTrue(log.exists())
        self.assertEqual(json.loads(log.read_text().strip())["tag"], "held")
        self.assertIsNone(R.L.current())


if __name__ == "__main__":
    unittest.main()
