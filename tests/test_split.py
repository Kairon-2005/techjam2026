"""The operative Phase 7A split, and the refusal of every other one.

`notes/40` registered a stratified 800/200 split and SUPERSEDED a global
`sha256 % 5` split that gave 806/194. Both numbers are in the notes; only one is
operative. These tests exist so that a driver cannot silently run the retired
one -- which would train on 806 sessions, validate on 194, and produce numbers
no pre-registration covers.
"""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from lab import split as SPLIT

DEV = Path("data/supplementary_dev.jsonl")


def superseded_split(rows):
    """Revision 2's retired split, rebuilt so the guard has something to reject.

    A GLOBAL hash bucket -- no strata -- which is exactly why it was retired:
    `boundary` is 50 samples in the whole corpus, so a global split makes its
    proportions accidental. This reconstruction is identified by what notes/40
    records about it -- 806/194 and skewed strata -- not by its short hash
    `211be164cec5ff4f`, whose serialization the note does not give.
    """
    train, val = [], []
    for row in rows:
        sid = str(row["sample_id"])
        bucket = int(hashlib.sha256(sid.encode()).hexdigest(), 16) % 5
        (val if bucket == 4 else train).append(sid)
    return SPLIT.Split(tuple(train), tuple(val))


@unittest.skipUnless(DEV.exists(), "needs data/supplementary_dev.jsonl")
class OperativeSplitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = SPLIT.load_rows(DEV)
        cls.split = SPLIT.build(cls.rows)

    def test_the_counts_are_the_preregistered_ones(self) -> None:
        self.assertEqual(len(self.split.train), 800)
        self.assertEqual(len(self.split.val), 200)

    def test_every_scenario_gets_its_registered_count(self) -> None:
        scenario_of = {str(r["sample_id"]): str(r["scenario_type"]) for r in self.rows}
        for ids, expected in ((self.split.train, SPLIT.SCENARIO_TRAIN),
                              (self.split.val, SPLIT.SCENARIO_VAL)):
            counts: dict[str, int] = {}
            for i in ids:
                counts[scenario_of[i]] = counts.get(scenario_of[i], 0) + 1
            self.assertEqual(counts, dict(expected))

    def test_the_val_counts_are_80_80_30_10(self) -> None:
        self.assertEqual(dict(SPLIT.SCENARIO_VAL),
                         {"buying": 80, "browsing": 80,
                          "intent_override": 30, "boundary": 10})

    def test_the_id_hashes_are_the_preregistered_ones(self) -> None:
        self.assertEqual(self.split.train_hash, SPLIT.TRAIN_HASH)
        self.assertEqual(self.split.val_hash, SPLIT.VAL_HASH)

    def test_overlap_is_zero_and_the_union_is_the_whole_corpus(self) -> None:
        self.assertEqual(set(self.split.train) & set(self.split.val), set())
        self.assertEqual(len(set(self.split.train) | set(self.split.val)), 1000)

    def test_no_public_and_no_sealed_holdout_id_is_in_the_split(self) -> None:
        for i in (*self.split.train, *self.split.val):
            self.assertTrue(i.startswith(SPLIT.DEV_PREFIX), i)
            self.assertFalse(i.startswith(SPLIT.FORBIDDEN_PREFIXES), i)

    def test_the_split_is_a_pure_function_of_the_ids(self) -> None:
        # No RNG and no seed: rebuilding from a shuffled corpus is the same
        # split. If it were not, "the split" would be a property of file order.
        shuffled = list(reversed(self.rows))
        again = SPLIT.build(shuffled)
        self.assertEqual(again.train, self.split.train)
        self.assertEqual(again.val, self.split.val)

    def test_assert_operative_accepts_it(self) -> None:
        self.assertEqual(SPLIT.problems(self.split, self.rows), [])
        SPLIT.assert_operative(self.split, self.rows)

    def test_train_rows_and_val_rows_return_the_split_rows(self) -> None:
        train = SPLIT.train_rows(DEV)
        self.assertEqual(len(train), 800)
        self.assertEqual(SPLIT.id_hash(r["sample_id"] for r in train),
                         SPLIT.TRAIN_HASH)
        val = SPLIT.val_rows(DEV)
        self.assertEqual(len(val), 200)
        self.assertEqual(SPLIT.id_hash(r["sample_id"] for r in val), SPLIT.VAL_HASH)


@unittest.skipUnless(DEV.exists(), "needs data/supplementary_dev.jsonl")
class SupersededSplitIsRejectedTest(unittest.TestCase):
    """The negative test. A guard that has never rejected anything is a comment."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = SPLIT.load_rows(DEV)
        cls.old = superseded_split(cls.rows)

    def test_the_retired_split_really_is_806_194(self) -> None:
        # If this ever stops being 806/194 the fixture has drifted and the
        # rejection below would be testing something else.
        self.assertEqual((len(self.old.train), len(self.old.val)), (806, 194))

    def test_the_retired_splits_strata_really_are_accidental(self) -> None:
        # The reason it was retired, made concrete: browsing keeps 337 of 400
        # while buying keeps 318 of 400, and `boundary` -- 50 samples in the
        # whole corpus -- lands 38/12. Nobody chose those numbers.
        scenario_of = {str(r["sample_id"]): str(r["scenario_type"]) for r in self.rows}
        counts: dict[str, int] = {}
        for i in self.old.train:
            counts[scenario_of[i]] = counts.get(scenario_of[i], 0) + 1
        self.assertNotEqual(counts, dict(SPLIT.SCENARIO_TRAIN))
        self.assertEqual(counts["boundary"], 38)

    def test_it_is_named_as_superseded_not_merely_miscounted(self) -> None:
        bad = SPLIT.problems(self.old, self.rows)
        self.assertTrue(any("SUPERSEDED SPLIT" in p for p in bad), bad)
        self.assertTrue(any("806" in p and "194" in p for p in bad), bad)

    def test_assert_operative_raises_on_it(self) -> None:
        with self.assertRaises(SPLIT.SplitError) as caught:
            SPLIT.assert_operative(self.old, self.rows)
        self.assertIn("SUPERSEDED", str(caught.exception))

    def test_the_count_and_the_hashes_are_both_reported(self) -> None:
        bad = "\n".join(SPLIT.problems(self.old, self.rows))
        self.assertIn("train row count 806", bad)
        self.assertIn("val row count 194", bad)
        self.assertIn(SPLIT.TRAIN_HASH, bad)
        self.assertIn(SPLIT.VAL_HASH, bad)

    def test_806_is_refused_even_when_the_hashes_are_not_checked(self) -> None:
        # The tripwire fires on the COUNT alone, so a future variant that
        # happens to hash differently is still caught by the number that
        # identifies it.
        fake = SPLIT.Split(tuple(f"supplementary_dev_{i:04d}" for i in range(1, 807)),
                           tuple(f"supplementary_dev_{i:04d}" for i in range(807, 1001)))
        bad = SPLIT.problems(fake, self.rows)
        self.assertTrue(any("SUPERSEDED SPLIT" in p for p in bad), bad)


@unittest.skipUnless(DEV.exists(), "needs data/supplementary_dev.jsonl")
class OtherRejectionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = SPLIT.load_rows(DEV)
        cls.split = SPLIT.build(cls.rows)

    def test_an_overlapping_split_is_rejected(self) -> None:
        leaked = SPLIT.Split(self.split.train, (self.split.train[0],) + self.split.val[1:])
        bad = SPLIT.problems(leaked, self.rows)
        self.assertTrue(any("overlap" in p for p in bad), bad)

    def test_a_public_sample_id_is_rejected(self) -> None:
        poisoned = SPLIT.Split(("public_0001",) + self.split.train[1:], self.split.val)
        bad = SPLIT.problems(poisoned, self.rows)
        self.assertTrue(any("forbidden corpus id" in p for p in bad), bad)

    def test_a_sealed_holdout_sample_id_is_rejected(self) -> None:
        poisoned = SPLIT.Split(("supplementary_holdout_0001",) + self.split.train[1:],
                               self.split.val)
        bad = SPLIT.problems(poisoned, self.rows)
        self.assertTrue(any("forbidden corpus id" in p for p in bad), bad)

    def test_the_guard_never_opens_the_sealed_corpus(self) -> None:
        # Membership is a NAMESPACE test. A guard that had to read the sealed
        # file in order to prove it was untouched would be touching it.
        source = Path("lab/split.py").read_text(encoding="utf-8")
        self.assertNotIn("supplementary_holdout.jsonl", source)
        self.assertNotIn("public_set.jsonl", source)

    def test_a_scenario_short_by_one_is_rejected(self) -> None:
        short = SPLIT.Split(self.split.train[:-1], self.split.val)
        bad = SPLIT.problems(short, self.rows)
        self.assertTrue(any("train row count 799" in p for p in bad), bad)
        self.assertTrue(any("union is 999" in p for p in bad), bad)


if __name__ == "__main__":
    unittest.main()
