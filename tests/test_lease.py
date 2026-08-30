"""The experiment lease: exclusion, isolation, verification, completion.

Every case here corresponds to a defect that actually occurred. A matrix
recorded while a second session was committing produced eight rows carrying
three different agent_commit values, all reporting code_dirty=false, because
provenance was sampled per row instead of checked across the run. A later
version isolated only the working directory, so `import starter.agent` still
resolved against the origin tree and rows claimed an isolation they did not
have.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lab import lease as L


def _tree_is_committed() -> bool:
    return not L._dirty()


class LeaseLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = L.LOCK_PATH
        L.LOCK_PATH = Path(self._tmp.name) / "lock"

    def tearDown(self) -> None:
        L.LOCK_PATH = self._saved
        self._tmp.cleanup()

    def test_the_lock_is_exclusive(self) -> None:
        L._acquire("first")
        with self.assertRaises(L.LeaseBusy):
            L._acquire("second")

    def test_a_stale_lock_is_broken_once(self) -> None:
        L.LOCK_PATH.write_text(json.dumps({"pid": 2 ** 30, "purpose": "dead"}))
        L._acquire("after the corpse")
        self.assertEqual(json.loads(L.LOCK_PATH.read_text())["pid"], os.getpid())

    def test_a_live_holder_is_not_evicted(self) -> None:
        L.LOCK_PATH.write_text(json.dumps({"pid": os.getpid(), "purpose": "live"}))
        with self.assertRaises(L.LeaseBusy):
            L._acquire("intruder")


class FingerprintTest(unittest.TestCase):
    def test_the_catalog_is_watched(self) -> None:
        # 58 MB, gitignored, and reached through a symlink inside an isolated
        # run -- the single input most able to change without leaving a trace.
        self.assertIn("data/catalog.jsonl", L.WATCHED)
        self.assertIn("data/public_set.jsonl", L.WATCHED)
        self.assertIn("starter/agent.py", L.WATCHED)

    def test_the_a1_cache_is_watched(self) -> None:
        # 420 MB, gitignored, and the INPUT to the coordinate search. A cache
        # swapped between the gate that passed it and the search that consumes
        # it would produce a weight vector, an MRR and a delta that all look
        # exactly like a result.
        self.assertIn("lab/a1cache.jsonl", L.WATCHED)
        for module in ("lab/split.py", "lab/a1search.py", "lab/a1cache.py"):
            self.assertIn(module, L.WATCHED)

    def test_a_tracked_input_is_never_replaced_by_a_link(self) -> None:
        # Linking over a TRACKED file makes git report a typechange, stamps
        # every row code_dirty, and gets the run refused by provenance -- a
        # measurement invalidated by its own isolation. The loop must skip
        # anything the checkout already provides.
        source = L._sh("sed", "-n", "/for relative in LINKED_INPUTS:/,/symlink_to/p",
                       "lab/lease.py")
        self.assertIn("if target.exists() or target.is_symlink():", source)
        self.assertIn("continue", source.split("target.exists()", 1)[1])

    def test_every_linked_input_is_also_watched(self) -> None:
        # A linked input that is not fingerprinted is the worst of both: it
        # reaches the run through a path the run cannot check, and a repoint
        # leaves no trace at all.
        for relative in L.LINKED_INPUTS:
            self.assertIn(relative, L.WATCHED, relative)


class LeaseVerdictTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved_watched = L.WATCHED
        self.real = self.root / "real.bin"
        self.real.write_text("original")
        self.link = self.root / "linked.bin"
        self.link.symlink_to(self.real)
        L.WATCHED = ("real.bin", "linked.bin")

    def tearDown(self) -> None:
        L.WATCHED = self._saved_watched
        self._tmp.cleanup()

    def _lease(self, **kw) -> L.Lease:
        obj = L.Lease(purpose="t", before=L.fingerprint(self.root), origin=self.root,
                      run_dir=self.root, journal_path=self.root / "j.jsonl", **kw)
        obj.expected_cells = obj.expected_cells or 2
        obj.completed_cells = 2
        return obj

    def test_an_untouched_complete_run_verifies(self) -> None:
        self.assertEqual(self._lease().verify(), "")

    def test_a_rewritten_input_breaks_the_lease(self) -> None:
        obj = self._lease()
        self.real.write_text("rewritten mid-run")
        self.assertIn("real.bin", obj.verify())

    def test_a_repointed_symlink_breaks_the_lease(self) -> None:
        # Same path, same bytes reachable, different file. A hash of the link
        # target alone would not say which file moved.
        obj = self._lease()
        other = self.root / "other.bin"
        other.write_text("original")             # identical CONTENT
        self.link.unlink()
        self.link.symlink_to(other)
        self.assertIn("repointed", obj.verify())

    def test_an_aborted_run_is_invalid_even_though_nothing_moved(self) -> None:
        obj = self._lease()
        obj.aborted = "RuntimeError: boom"
        self.assertIn("boom", obj.verify())
        self.assertFalse(obj.matrix_complete)

    def test_a_short_matrix_is_invalid(self) -> None:
        obj = self._lease()
        obj.expected_cells, obj.completed_cells = 25, 11
        self.assertIn("matrix incomplete", obj.verify())

    def test_settle_stamps_partial_rows_and_keeps_them(self) -> None:
        obj = self._lease()
        obj.expected_cells, obj.completed_cells = 4, 2
        obj.journal_path.write_text(
            json.dumps({"tag": "t", "score": 0.5}) + "\n"
            + json.dumps({"tag": "t", "score": 0.6}) + "\n")
        log = self.root / "results.jsonl"
        obj.aborted = "KeyboardInterrupt: "
        L._settle(obj, log)
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertEqual(len(rows), 2, "partial rows are evidence and are kept")
        for row in rows:
            self.assertEqual(row["invalid"]["reason"], "run_aborted")
            self.assertEqual(row["lease"]["expected_cells"], 4)
            self.assertEqual(row["lease"]["completed_cells"], 2)
            self.assertFalse(row["lease"]["matrix_complete"])


class LeaseIsolationTest(unittest.TestCase):
    """The run must import from the worktree, not from the working copy."""

    @unittest.skipUnless(_tree_is_committed(),
                         "needs a committed tree to isolate")
    def test_the_child_imports_from_the_isolated_worktree(self) -> None:
        script = ("import starter.agent as A, os, json;"
                  "open(os.environ['LAB_JOURNAL'],'a').write("
                  "json.dumps({'tag':'iso','agent':A.__file__})+chr(10))")
        # A test must never append to the real ledger: `lease` flushes to
        # `log` on exit, and the default is lab/results.jsonl.
        scratch = Path(tempfile.mkdtemp(prefix="lease-test-")) / "results.jsonl"
        self.addCleanup(shutil.rmtree, scratch.parent, ignore_errors=True)
        with L.lease("isolation-test", log=scratch) as ls:
            rc = ls.run(script, expected_cells=1)
            self.assertEqual(rc, 0)
            rows = ls.rows()
        self.assertEqual(len(rows), 1)
        loaded = os.path.realpath(rows[0]["agent"])
        worktree = os.path.realpath(ls.run_dir)
        self.assertTrue(loaded.startswith(worktree),
                        f"child imported {loaded}, not the isolated worktree")
        self.assertNotIn(os.path.realpath(ls.origin / "starter"), loaded)


if __name__ == "__main__":
    unittest.main()
