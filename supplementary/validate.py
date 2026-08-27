from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from supplementary import SCHEMA_VERSION
from supplementary.generate import (
    category_label,
    load_jsonl,
    safe_facts,
    sha256_file,
)


SCENARIOS = {"buying", "browsing", "intent_override", "boundary"}
REQUIRED_PROFILE_KEYS = {
    "purchase_frequency", "average_prior_rating", "rating_style",
    "preference_tags", "summary",
}


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_all_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_all_strings(item))
        return strings
    return []


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def validate_row(row: dict, products: dict[str, dict], expected_split: str, expected_sealed: bool) -> None:
    if row.get("schema_version") != SCHEMA_VERSION:
        raise AssertionError("schema version mismatch")
    if row.get("source") != "supplementary_catalog_synthetic" or row.get("official") is not False:
        raise AssertionError("supplementary source/official markers missing")
    if row.get("split") != expected_split or row.get("sealed") is not expected_sealed:
        raise AssertionError("split or sealed marker mismatch")
    if row.get("scenario_type") not in SCENARIOS:
        raise AssertionError("invalid scenario")
    if set(row.get("user_profile") or {}) != REQUIRED_PROFILE_KEYS:
        raise AssertionError("profile does not follow the official reset contract")
    target = str((row.get("ground_truth") or {}).get("parent_asin") or "")
    if target not in products:
        raise AssertionError(f"target {target!r} is not in the frozen catalog")
    product = products[target]
    card = row.get("intent_card") or {}
    if card.get("target_category") != category_label(product):
        raise AssertionError("target category is not deterministically catalog-grounded")
    allowed_facts = {phrase for _, phrase in safe_facts(product)}
    constraints = [
        *[str(value) for value in card.get("hard_constraints") or []],
        *[str(value) for value in card.get("soft_preferences") or []],
    ]
    if not card.get("hard_constraints") or not constraints:
        raise AssertionError("intent card has no usable constraint")
    if any(value not in allowed_facts for value in constraints):
        raise AssertionError("intent constraint is not derived from the target's visible metadata")
    behavior = row.get("behavior") or {}
    if int(behavior.get("max_turns", 0)) > 10:
        raise AssertionError("max turns exceeds the official protocol")
    scenario = row["scenario_type"]
    if scenario == "intent_override":
        override = behavior.get("override") or {}
        if override.get("turn") not in (3, 4):
            raise AssertionError("override must happen on turn 3 or 4")
        if str(override.get("new_value") or "") not in card["hard_constraints"]:
            raise AssertionError("override does not converge to a target-grounded hard constraint")
    elif behavior.get("override"):
        raise AssertionError("non-override scenario carries override behavior")
    if (scenario == "boundary") != bool(behavior.get("boundary_no_preference_once")):
        raise AssertionError("boundary no-preference semantics mismatch")

    # Ground truth must exist in the evaluator artifact, but it must not leak
    # into any user-visible intent, profile, behavior, or metadata string.
    exposed = {
        "user_profile": row.get("user_profile"),
        "intent_card": card,
        "behavior": behavior,
        "supplementary_metadata": row.get("supplementary_metadata"),
    }
    strings = _all_strings(exposed)
    if any(target.casefold() in value.casefold() for value in strings):
        raise AssertionError("parent_asin leaked into simulated user-visible data")
    title = _normalized(str(product.get("title") or ""))
    exposed_blob = _normalized(" ".join(strings))
    if len(title) >= 12 and title in exposed_blob:
        raise AssertionError("full product title leaked into simulated user-visible data")


def validate_all(
    catalog_path: str | Path,
    public_set_path: str | Path,
    manifest_path: str | Path,
) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise AssertionError("manifest schema mismatch")
    if manifest["catalog"]["sha256"] != sha256_file(catalog_path):
        raise AssertionError("catalog hash differs from generation manifest")
    if manifest["public_set"]["sha256"] != sha256_file(public_set_path):
        raise AssertionError("public set hash differs from generation manifest")
    catalog_rows = load_jsonl(catalog_path)
    products = {str(row["parent_asin"]): row for row in catalog_rows}
    public_rows = load_jsonl(public_set_path)
    public_targets = {str(row["ground_truth"]["parent_asin"]) for row in public_rows}
    split_targets: dict[str, set[str]] = {}
    summary: dict[str, dict] = {}
    root = Path(manifest_path).resolve().parent.parent
    for split, spec in manifest["splits"].items():
        path = root / spec["path"]
        if sha256_file(path) != spec["sha256"]:
            raise AssertionError(f"{split} hash mismatch")
        rows = load_jsonl(path)
        if len(rows) != int(spec["row_count"]):
            raise AssertionError(f"{split} row count mismatch")
        for row in rows:
            validate_row(row, products, split, bool(spec["sealed"]))
        targets = {str(row["ground_truth"]["parent_asin"]) for row in rows}
        if len(targets) != len(rows):
            raise AssertionError(f"{split} repeats target products")
        if targets & public_targets:
            raise AssertionError(f"{split} overlaps public targets")
        counts = dict(sorted(Counter(row["scenario_type"] for row in rows).items()))
        if counts != spec["scenario_counts"]:
            raise AssertionError(f"{split} scenario counts mismatch")
        override_turns = Counter(
            row["behavior"]["override"]["turn"]
            for row in rows if row["scenario_type"] == "intent_override"
        )
        split_targets[split] = targets
        summary[split] = {
            "row_count": len(rows),
            "scenario_counts": counts,
            "target_count": len(targets),
            "override_turn_counts": dict(sorted(override_turns.items())),
            "sha256": spec["sha256"],
            "sealed": bool(spec["sealed"]),
        }
    names = list(split_targets)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            if split_targets[left] & split_targets[right]:
                raise AssertionError(f"target overlap between {left} and {right}")
    contract = manifest.get("holdout_contract") or {}
    holdout = manifest["splits"].get(str(contract.get("split"))) or {}
    if contract.get("sealed") is not True or holdout.get("status") != "sealed_unrun":
        raise AssertionError("sealed holdout contract missing")
    return {"official": False, "source": "supplementary_catalog_synthetic", "splits": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate supplementary dataset structure and provenance")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--public-set", default="data/public_set.jsonl")
    parser.add_argument("--manifest", default="data/supplementary_manifest.json")
    args = parser.parse_args()
    print(json.dumps(validate_all(args.catalog, args.public_set, args.manifest), indent=2))


if __name__ == "__main__":
    main()

