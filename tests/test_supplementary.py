from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from supplementary.evaluate_dev import ensure_development_rows
from supplementary.generate import generate, load_jsonl, sha256_bytes
from supplementary.validate import validate_all


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/catalog.jsonl"
PUBLIC = ROOT / "data/public_set.jsonl"
MANIFEST = ROOT / "data/supplementary_manifest.json"
DEV = ROOT / "data/supplementary_dev.jsonl"
HOLDOUT = ROOT / "data/supplementary_holdout.jsonl"


class TestGeneratorDeterminism(unittest.TestCase):
    def _fixtures(self, root: Path) -> tuple[Path, Path, Path]:
        catalog = root / "catalog.jsonl"
        public = root / "public.jsonl"
        config = root / "config.json"
        products = []
        for index in range(240):
            products.append({
                "parent_asin": f"T{index:09d}",
                "title": f"Synthetic fixture garment number {index}",
                "features": ["Cotton", "Black", "Machine washable", "Lightweight"],
                "description": [],
                "price": 19.99 + index % 30,
                "categories": ["Clothing, Shoes & Jewelry", "Test", f"Group {index % 12}"],
                "details": {"Department": "unisex"},
                "average_rating": 4.0,
                "rating_number": 10,
                "store": "fixture",
            })
        catalog.write_text("".join(json.dumps(row) + "\n" for row in products), encoding="utf-8")
        public_rows = [{
            "sample_id": f"public_{index}",
            "scenario_type": "buying",
            "user_profile": {},
            "ground_truth": {"parent_asin": f"T{index:09d}"},
        } for index in range(20)]
        public.write_text("".join(json.dumps(row) + "\n" for row in public_rows), encoding="utf-8")
        config.write_text(json.dumps({
            "schema_version": "supplementary-catalog-v1",
            "generator_version": "1.0.0",
            "generator_seed": 20260827,
            "selection_strategy": "category_stratified_hash_round_robin",
            "splits": {
                "supplementary_dev": {"size": 100, "seed_offset": 0, "sealed": False},
                "supplementary_holdout": {"size": 100, "seed_offset": 1000003, "sealed": True},
            },
            "scenario_mix": {"buying": 0.4, "browsing": 0.4, "intent_override": 0.15, "boundary": 0.05},
            "override_turns": [3, 4],
            "max_turns": 10,
            "top_k": 10,
            "minimum_safe_facts": 2,
            "exclude_public_targets": True,
            "allow_upstream_purchase_or_review_data": False,
        }), encoding="utf-8")
        return catalog, public, config

    def test_regeneration_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog, public, config = self._fixtures(Path(directory))
            first, first_manifest = generate(catalog, public, config, "fixture-commit")
            second, second_manifest = generate(catalog, public, config, "fixture-commit")
        self.assertEqual(first, second)
        self.assertEqual(first_manifest, second_manifest)
        self.assertEqual(
            {name: sha256_bytes(data) for name, data in first.items()},
            {name: sha256_bytes(data) for name, data in second.items()},
        )

    def test_exact_mix_and_split_disjointness_in_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog, public, config = self._fixtures(Path(directory))
            payloads, _ = generate(catalog, public, config, "fixture-commit")
        rows_by_split = {
            name: [json.loads(line) for line in data.decode().splitlines()]
            for name, data in payloads.items()
        }
        expected = {"buying": 40, "browsing": 40, "intent_override": 15, "boundary": 5}
        for rows in rows_by_split.values():
            self.assertEqual(Counter(row["scenario_type"] for row in rows), expected)
        dev_targets = {row["ground_truth"]["parent_asin"] for row in rows_by_split["supplementary_dev.jsonl"]}
        holdout_targets = {row["ground_truth"]["parent_asin"] for row in rows_by_split["supplementary_holdout.jsonl"]}
        self.assertFalse(dev_targets & holdout_targets)
        self.assertFalse({f"T{index:09d}" for index in range(20)} & (dev_targets | holdout_targets))


@unittest.skipUnless(CATALOG.exists() and MANIFEST.exists(), "generated catalog artifacts are not installed")
class TestFrozenSupplementaryArtifacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dev = load_jsonl(DEV)
        cls.holdout = load_jsonl(HOLDOUT)
        cls.public = load_jsonl(PUBLIC)
        with MANIFEST.open(encoding="utf-8") as handle:
            cls.manifest = json.load(handle)

    def test_structural_provenance_and_leakage_validation(self) -> None:
        summary = validate_all(CATALOG, PUBLIC, MANIFEST)
        self.assertFalse(summary["official"])
        self.assertEqual(summary["splits"]["supplementary_dev"]["row_count"], 1000)
        self.assertEqual(summary["splits"]["supplementary_holdout"]["row_count"], 1000)

    def test_exact_official_scenario_ratio(self) -> None:
        expected = {"buying": 400, "browsing": 400, "intent_override": 150, "boundary": 50}
        self.assertEqual(Counter(row["scenario_type"] for row in self.dev), expected)
        self.assertEqual(Counter(row["scenario_type"] for row in self.holdout), expected)

    def test_targets_are_unique_catalog_members_and_pairwise_disjoint(self) -> None:
        catalog_ids = {row["parent_asin"] for row in load_jsonl(CATALOG)}
        public_targets = {row["ground_truth"]["parent_asin"] for row in self.public}
        dev_targets = {row["ground_truth"]["parent_asin"] for row in self.dev}
        holdout_targets = {row["ground_truth"]["parent_asin"] for row in self.holdout}
        self.assertEqual(len(dev_targets), 1000)
        self.assertEqual(len(holdout_targets), 1000)
        self.assertLessEqual(dev_targets | holdout_targets, catalog_ids)
        self.assertFalse(public_targets & dev_targets)
        self.assertFalse(public_targets & holdout_targets)
        self.assertFalse(dev_targets & holdout_targets)

    def test_no_parent_asin_leaks_beyond_ground_truth(self) -> None:
        for row in self.dev + self.holdout:
            target = row["ground_truth"]["parent_asin"]
            exposed = json.dumps({
                "user_profile": row["user_profile"],
                "intent_card": row["intent_card"],
                "behavior": row["behavior"],
                "supplementary_metadata": row["supplementary_metadata"],
            }).casefold()
            self.assertNotIn(target.casefold(), exposed)

    def test_override_turns_and_max_turn_contract(self) -> None:
        for rows in (self.dev, self.holdout):
            override_rows = [row for row in rows if row["scenario_type"] == "intent_override"]
            self.assertEqual(Counter(row["behavior"]["override"]["turn"] for row in override_rows), {3: 75, 4: 75})
            self.assertTrue(all(row["behavior"]["max_turns"] <= 10 for row in rows))

    def test_boundary_semantics(self) -> None:
        for rows in (self.dev, self.holdout):
            for row in rows:
                expected = row["scenario_type"] == "boundary"
                self.assertEqual(row["behavior"]["boundary_no_preference_once"], expected)

    def test_holdout_is_sealed_and_dev_adapter_refuses_it(self) -> None:
        self.assertTrue(all(row["sealed"] for row in self.holdout))
        self.assertTrue(all(not row["sealed"] for row in self.dev))
        self.assertEqual(self.manifest["splits"]["supplementary_holdout"]["status"], "sealed_unrun")
        ensure_development_rows(self.dev)
        with self.assertRaisesRegex(ValueError, "sealed holdout evaluation is forbidden"):
            ensure_development_rows(self.holdout)


if __name__ == "__main__":
    unittest.main()



class SupplementaryLedgerWiringTest(unittest.TestCase):
    """The ledger must be able to tell a veto signal from a score."""

    def test_supplementary_scenarios_are_marked_non_official(self) -> None:
        from lab import scenarios as S
        names = [n for n in S.BY_NAME if n.startswith("supplementary_")]
        self.assertIn("supplementary_dev", names)
        for name in names:
            sc = S.BY_NAME[name]
            self.assertFalse(sc.official, name)
            self.assertEqual(sc.source, "supplementary_catalog_synthetic", name)
            self.assertEqual(sc.dataset, S.SUPPLEMENTARY_DEV, name)

    def test_official_scenarios_stay_official(self) -> None:
        from lab import scenarios as S
        for name in ("clean", "clean_buying", "uncooperative", "override_category"):
            self.assertTrue(S.BY_NAME[name].official, name)
            self.assertIsNone(S.BY_NAME[name].dataset, name)

    def test_the_sealed_holdout_has_no_scenario_to_run_it_through(self) -> None:
        # The surest way not to run a sealed split is to give it no way in.
        from lab import scenarios as S
        for name, sc in S.BY_NAME.items():
            self.assertNotIn("holdout", str(sc.dataset or ""), name)

    def test_the_four_supplementary_slices_partition_the_split(self) -> None:
        from lab import scenarios as S
        rows = S.load_dataset(S.SUPPLEMENTARY_DEV)
        total = 0
        for name in ("buying", "browsing", "intent_override", "boundary"):
            keep = S.BY_NAME[f"supplementary_{name}"].keep
            total += sum(1 for r in rows if keep(r))
        self.assertEqual(total, len(rows))
