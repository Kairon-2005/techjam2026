"""The A1 cache driver: hard split assertions, and one real captured session.

The driver is where "800 sessions" stops being a sentence in a report and
becomes something that either holds or raises. These tests pin both halves:
the guard refuses anything that is not the operative split, and a real run
through the evaluator's own customer loop produces a cache that passes the
default replay gate.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import starter.agent as A
from lab import a1cache as CACHE
from lab import a1driver as D
from lab import split as SPLIT
from tests.test_indexes import PRODUCTS, _catalog_file

DEV = Path("data/supplementary_dev.jsonl")


def _sample(sample_id: str, target: str, scenario: str = "browsing") -> dict:
    """One evaluator-shaped row over the fixture catalog."""
    return {
        "sample_id": sample_id,
        "scenario_type": scenario,
        "user_profile": {"preference_tags": []},
        "ground_truth": {"parent_asin": target},
        "intent_card": {"target_category": "dress",
                        "hard_constraints": ["silk", "color: black"],
                        "soft_preferences": ["for work"]},
        "behavior": {"scenario_type": scenario},
    }


class SplitGuardTest(unittest.TestCase):
    """A driver that can run the wrong split is a driver that eventually will."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._real_dev = SPLIT.DEV
        self.addCleanup(lambda: setattr(SPLIT, "DEV", self._real_dev))

    @unittest.skipUnless(DEV.exists(), "needs data/supplementary_dev.jsonl")
    def test_a_corpus_short_by_one_row_stops_the_driver(self) -> None:
        rows = SPLIT.load_rows(DEV)[:-1]
        doctored = Path(self._tmp.name) / "short.jsonl"
        doctored.write_text("".join(json.dumps(r) + "\n" for r in rows),
                            encoding="utf-8")
        SPLIT.DEV = doctored
        with self.assertRaises(SPLIT.SplitError):
            D.main(["--limit", "1", "--out", self._tmp.name])

    @unittest.skipUnless(DEV.exists(), "needs data/supplementary_dev.jsonl")
    def test_a_corpus_with_a_forbidden_id_stops_the_driver(self) -> None:
        rows = SPLIT.load_rows(DEV)
        rows[0] = {**rows[0], "sample_id": "supplementary_holdout_0001"}
        doctored = Path(self._tmp.name) / "sealed.jsonl"
        doctored.write_text("".join(json.dumps(r) + "\n" for r in rows),
                            encoding="utf-8")
        SPLIT.DEV = doctored
        with self.assertRaises(SPLIT.SplitError) as caught:
            D.main(["--limit", "1", "--out", self._tmp.name])
        self.assertIn("forbidden corpus id", str(caught.exception))

    @unittest.skipUnless(DEV.exists(), "needs data/supplementary_dev.jsonl")
    def test_the_guard_runs_before_any_session(self) -> None:
        # The doctored corpus is rejected without a catalog ever being opened,
        # which is what "at startup" means: no partial build to clean up and no
        # half-written cache to mistake for a real one.
        rows = SPLIT.load_rows(DEV)[:-1]
        doctored = Path(self._tmp.name) / "short.jsonl"
        doctored.write_text("".join(json.dumps(r) + "\n" for r in rows),
                            encoding="utf-8")
        SPLIT.DEV = doctored
        with self.assertRaises(SPLIT.SplitError):
            D.main(["--limit", "1", "--out", self._tmp.name])
        self.assertFalse((Path(self._tmp.name) / "a1cache.jsonl").exists())
        self.assertFalse((Path(self._tmp.name) / "a1cache.meta.json").exists())


class CapturedRunTest(unittest.TestCase):
    """A real run over the fixture catalog, through the evaluator's own loop."""

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))
        targets = [p["parent_asin"] for p in PRODUCTS][:3]
        cls.rows = [_sample(f"supplementary_dev_{i:04d}", t)
                    for i, t in enumerate(targets, 1)]
        cls.built = D.build(cls.rows, catalog=Path(cls.path), progress_every=0)

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def test_every_session_is_cached(self) -> None:
        self.assertEqual(len(self.built["sessions"]), len(self.rows))
        self.assertEqual([s["sample_id"] for s in self.built["sessions"]],
                         [r["sample_id"] for r in self.rows])

    def test_every_cached_turn_has_a_snapshot(self) -> None:
        for session in self.built["sessions"]:
            for turn in session["turns"]:
                self.assertIn((session["sample_id"], turn["turn"]),
                              self.built["capture"].snapshots)

    def test_no_turn_was_captured_twice_and_none_escaped_the_hook(self) -> None:
        self.assertEqual(self.built["capture"].duplicate_keys, [])
        self.assertEqual(self.built["capture"].orphan_calls, 0)

    def test_the_schema_is_valid(self) -> None:
        self.assertEqual(CACHE.validate_schema(self.built["sessions"]), [])

    def test_the_replay_gate_passes_with_zero_mismatches(self) -> None:
        got = CACHE.replay_gate(self.built["sessions"], self.built["agent"],
                                self.built["capture"].snapshots)
        self.assertTrue(got["ok"], got.get("reason"))
        self.assertEqual(got["mismatches"], 0)
        self.assertEqual(got["checked_sessions"], len(self.rows))
        self.assertGreater(got["checked_turns"], 0)

    def test_cached_mrr_equals_a0s_own_mrr_exactly(self) -> None:
        got = CACHE.replay_gate(self.built["sessions"], self.built["agent"],
                                self.built["capture"].snapshots)
        self.assertEqual(got["delta_mrr"], 0.0)

    def test_the_target_is_the_rows_ground_truth(self) -> None:
        for session, row in zip(self.built["sessions"], self.rows):
            for turn in session["turns"]:
                self.assertEqual(turn["target"], row["ground_truth"]["parent_asin"])


@unittest.skipUnless(DEV.exists(), "needs data/supplementary_dev.jsonl")
class ManifestTest(unittest.TestCase):
    """The gate's report must name every input the number depends on."""

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))
        rows = [_sample(f"supplementary_dev_{i:04d}", p["parent_asin"])
                for i, p in enumerate(PRODUCTS[:2], 1)]
        built = D.build(rows, catalog=Path(cls.path), progress_every=0)
        split, _ = SPLIT.operative(DEV)
        cls.manifest = D.gate(built, split,
                              Path(cls._tmp.name) / "a1cache.jsonl")

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def test_the_required_fields_are_all_present(self) -> None:
        for field in ("checked_sessions", "checked_turns",
                      "full_order_mismatches", "cached_default_mrr",
                      "live_a0_mrr", "delta_mrr", "cache_sha256",
                      "split_train_hash", "agent_commit", "agent_sha256",
                      "catalog_sha256"):
            self.assertIn(field, self.manifest)
            self.assertIsNotNone(self.manifest[field], field)

    def test_the_split_hashes_are_the_preregistered_ones(self) -> None:
        self.assertEqual(self.manifest["split_train_hash"], SPLIT.TRAIN_HASH)
        self.assertEqual(self.manifest["split_val_hash"], SPLIT.VAL_HASH)
        self.assertEqual(self.manifest["split_train_n"], 800)
        self.assertEqual(self.manifest["split_val_n"], 200)

    def test_the_gate_verdict_is_a_pass(self) -> None:
        self.assertTrue(self.manifest["ok"], self.manifest.get("first_mismatch"))
        self.assertEqual(self.manifest["full_order_mismatches"], 0)
        self.assertEqual(self.manifest["delta_mrr"], 0.0)


if __name__ == "__main__":
    unittest.main()
