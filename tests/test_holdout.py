"""The synthetic holdout is one-shot, score_default-only, and not the private set.

A sealed corpus is only sealed while something refuses to open it twice. These
tests exercise that refusal, and the two claims the document must never make.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lab import holdout as H


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ledger = Path(self._tmp.name) / "holdout.jsonl"

    def test_an_empty_ledger_permits_the_one_run(self) -> None:
        H.guard(self.ledger)                     # must not raise

    def test_an_existing_row_refuses_a_second_run(self) -> None:
        self.ledger.write_text(
            json.dumps({"tag": H.TAG, "ts": "2026-08-30T03:00:00"}) + "\n",
            encoding="utf-8")
        with self.assertRaises(H.HoldoutConsumedError) as caught:
            H.guard(self.ledger)
        self.assertIn("already consumed", str(caught.exception))
        self.assertIn("ONE-SHOT", str(caught.exception))

    def test_an_unrelated_row_does_not_block_it(self) -> None:
        self.ledger.write_text(
            json.dumps({"tag": "something-else"}) + "\n", encoding="utf-8")
        H.guard(self.ledger)


class ScoreDefaultOnlyTest(unittest.TestCase):
    """No configuration argument exists, so none can be passed."""

    def test_run_takes_no_config(self) -> None:
        import inspect
        self.assertEqual(list(inspect.signature(H.run).parameters), [])

    def test_the_agent_is_constructed_bare(self) -> None:
        source = Path("lab/holdout.py").read_text(encoding="utf-8")
        line = next(l for l in source.splitlines() if "A.Agent(" in l)
        self.assertNotIn("config", line,
                         "a config reaches the holdout agent; it must be "
                         "score_default and nothing else")

    def test_the_module_names_no_showcase_profile(self) -> None:
        source = Path("lab/holdout.py").read_text(encoding="utf-8")
        for banned in ("showcase_semantic", "showcase_dense", "semantic_lambda",
                       "dense_browsing"):
            self.assertNotIn(banned, source, banned)


class FramingTest(unittest.TestCase):
    """What the write-up must and must not say about this corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        source = Path("lab/holdout.py").read_text(encoding="utf-8")
        # Emphasis markers are stripped: a phrase must be present as PROSE, and
        # whether a word is bolded is not what these tests are about.
        cls.flat = " ".join(source.replace("**", "").split())

    def requires(self, phrase: str) -> None:
        self.assertTrue(phrase in self.flat,
                        f"lab/holdout.py does not say: {phrase!r}")

    def test_it_is_never_called_the_private_set(self) -> None:
        self.requires("not the organizer's private 800")
        self.requires("not presented")

    def test_it_is_labelled_synthetic_and_ours(self) -> None:
        self.requires("a corpus we wrote")

    def test_the_document_says_nothing_changed(self) -> None:
        self.requires("did not change anything")
        self.requires("cannot be re-run")

    @unittest.skipUnless(H.LEDGER.exists(), "the holdout has not been consumed")
    def test_the_consumed_document_matches_the_ledger(self) -> None:
        rows = [json.loads(l) for l in
                H.LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows = [r for r in rows if r.get("tag") == H.TAG]
        self.assertEqual(len(rows), 1, "the holdout has more than one row")
        doc = Path("docs/HOLDOUT_CONSUMED.md").read_text(encoding="utf-8")
        self.assertIn(str(rows[0]["score"]), doc)
        self.assertEqual(rows[0]["sample_count"], H.EXPECTED_ROWS)
        self.assertTrue(rows[0].get("not_the_private_set"))


if __name__ == "__main__":
    unittest.main()
