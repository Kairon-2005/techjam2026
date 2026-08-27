from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from supplementary import GENERATOR_VERSION, SCHEMA_VERSION


MATERIALS = (
    "cotton", "polyester", "nylon", "leather", "wool", "spandex",
    "silk", "rayon", "linen", "cashmere", "suede", "fleece",
)
COLORS = (
    "black", "white", "blue", "red", "pink", "green", "brown",
    "gray", "grey", "purple", "yellow", "orange", "beige", "navy",
)
USE_CASES = (
    "hiking", "running", "gym", "winter", "outdoor", "work", "swim",
    "travel", "yoga", "cycling", "ski", "snow", "rain",
)
FITS = ("regular fit", "slim fit", "relaxed fit", "loose fit", "compression", "oversized")
FEATURE_PATTERNS = (
    (r"\bwaterproof\b", "feature: waterproof"),
    (r"\bwater[- ]resistant\b", "feature: water resistant"),
    (r"\bmoisture[- ]wick(?:ing)?\b|\bwicks? moisture\b", "feature: moisture wicking"),
    (r"\bmachine wash(?:able)?\b", "feature: machine washable"),
    (r"\bhypoallergenic\b", "feature: hypoallergenic"),
    (r"\buv protection\b|\bupf\s*\d+\b", "feature: UV protection"),
    (r"\bzipper(?:ed)?\b|\bzip closure\b", "feature: zipper closure"),
    (r"\bpockets?\b", "feature: pockets"),
    (r"\bbreathable\b", "feature: breathable"),
    (r"\blightweight\b", "feature: lightweight"),
)
GENERIC_CATEGORY_ROOTS = {
    "clothing", "clothing shoes & jewelry", "clothing, shoes & jewelry",
    "women", "men", "girls", "boys", "baby", "unisex",
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_rank(seed: int, namespace: str, value: str) -> str:
    return sha256_bytes(f"{seed}\0{namespace}\0{value}".encode())


def load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_config(path: str | Path) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {config.get('schema_version')!r}")
    if config.get("generator_version") != GENERATOR_VERSION:
        raise ValueError(f"unsupported generator_version: {config.get('generator_version')!r}")
    mix = config.get("scenario_mix") or {}
    if set(mix) != {"buying", "browsing", "intent_override", "boundary"}:
        raise ValueError("scenario_mix must contain the four official scenarios")
    if not math.isclose(sum(float(v) for v in mix.values()), 1.0):
        raise ValueError("scenario_mix must sum to 1")
    return config


def searchable_text(product: dict) -> str:
    parts: list[str] = []
    for field in ("title", "features", "description", "details", "categories"):
        value = product.get(field)
        if isinstance(value, dict):
            parts.extend(f"{key} {item}" for key, item in value.items())
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value not in (None, ""):
            parts.append(str(value))
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def category_label(product: dict) -> str:
    values: list[str] = []
    for raw in product.get("categories") or []:
        values.extend(part.strip() for part in str(raw).split(",") if part.strip())
    cleaned = [value for value in values if value.lower() not in GENERIC_CATEGORY_ROOTS]
    return " ".join(cleaned[-2:]) if cleaned else "clothing item"


def _token_present(text: str, token: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", text) is not None


def safe_facts(product: dict) -> list[tuple[str, str]]:
    """Extract only generic, verifiable facts from participant-visible metadata.

    No free-form title, store, feature sentence, description sentence, or ASIN
    is copied into a fact. This reduces direct target leakage while retaining
    metadata-grounded material/color/use-case/feature constraints.
    """
    text = searchable_text(product)
    facts: list[tuple[str, str]] = []
    facts.extend(("material", material) for material in MATERIALS if _token_present(text, material))
    facts.extend(("color", f"color: {color}") for color in COLORS if _token_present(text, color))
    facts.extend(("use_case", f"for {use_case}") for use_case in USE_CASES if _token_present(text, use_case))
    facts.extend(("style", f"style: {fit}") for fit in FITS if _token_present(text, fit))
    for pattern, phrase in FEATURE_PATTERNS:
        if re.search(pattern, text):
            facts.append(("feature", phrase))
    price = product.get("price")
    if isinstance(price, (int, float)) and 0 < float(price) <= 1000:
        ceiling = max(10, int(math.ceil(float(price) / 10.0) * 10))
        facts.append(("budget", f"budget under ${ceiling + 1}"))
    label = category_label(product).lower()
    if label != "clothing item" and len(label) <= 64:
        facts.append(("style", f"style: {label}"))
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for kind, phrase in facts:
        key = phrase.casefold()
        if key not in seen:
            seen.add(key)
            unique.append((kind, phrase))
    return unique


def intent_card(product: dict, seed: int, parent_asin: str) -> tuple[dict, list[tuple[str, str]]]:
    facts = safe_facts(product)
    facts.sort(key=lambda item: stable_rank(seed, "fact", f"{parent_asin}\0{item[0]}\0{item[1]}"))
    hard = [phrase for _, phrase in facts[:2]]
    soft = [phrase for _, phrase in facts[2:4]]
    if not soft and hard:
        soft = hard[-1:]
    return {
        "target_category": category_label(product),
        "hard_constraints": hard,
        "soft_preferences": soft,
    }, facts


def _alternate_value(product: dict, facts: list[tuple[str, str]], seed: int, parent_asin: str) -> str:
    text = searchable_text(product)
    options = [f"color: {value}" for value in COLORS if not _token_present(text, value)]
    options.extend(value for value in MATERIALS if not _token_present(text, value))
    if not options:
        return "a different style"
    options.sort(key=lambda value: stable_rank(seed, "override-old", f"{parent_asin}\0{value}"))
    return options[0]


def behavior_for(
    scenario: str,
    card: dict,
    product: dict,
    facts: list[tuple[str, str]],
    seed: int,
    parent_asin: str,
    override_turn: int | None,
) -> dict:
    behavior: dict = {
        "scenario_type": scenario,
        "max_turns": 10,
        "boundary_no_preference_once": scenario == "boundary",
    }
    if scenario == "intent_override":
        new_value = str(card["hard_constraints"][0])
        old_value = _alternate_value(product, facts, seed, parent_asin)
        behavior["override"] = {
            "turn": int(override_turn or 3),
            "old_value": f"I initially preferred {old_value}.",
            "new_value": new_value,
            "message": f"Actually, ignore my earlier preference. What I need is: {new_value}.",
        }
    return behavior


def neutral_profile() -> dict:
    return {
        "purchase_frequency": "unknown (synthetic challenge set)",
        "average_prior_rating": None,
        "rating_style": "unknown",
        "preference_tags": [],
        "summary": (
            "No purchase-history profile is available for this synthetic "
            "catalog-grounded challenge session."
        ),
    }


def scenario_sequence(size: int, mix: dict[str, float], seed: int, split: str) -> list[str]:
    counts = {name: int(round(size * float(ratio))) for name, ratio in mix.items()}
    if sum(counts.values()) != size:
        raise ValueError(f"scenario counts do not sum to split size: {counts}")
    values = [name for name in ("buying", "browsing", "intent_override", "boundary") for _ in range(counts[name])]
    random.Random(f"{seed}\0{split}\0scenario-order").shuffle(values)
    return values


def select_targets(
    products: list[dict],
    excluded: set[str],
    count: int,
    seed: int,
    minimum_safe_facts: int,
) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for product in products:
        parent_asin = str(product.get("parent_asin") or "")
        if not parent_asin or parent_asin in excluded:
            continue
        if len(safe_facts(product)) < minimum_safe_facts:
            continue
        groups[category_label(product)].append(product)
    for label, rows in groups.items():
        rows.sort(key=lambda row: stable_rank(seed, f"target:{label}", str(row["parent_asin"])))
    labels = sorted(groups, key=lambda value: stable_rank(seed, "category", value))
    selected: list[dict] = []
    level = 0
    while len(selected) < count:
        progressed = False
        for label in labels:
            rows = groups[label]
            if level < len(rows):
                selected.append(rows[level])
                progressed = True
                if len(selected) == count:
                    break
        if not progressed:
            raise ValueError(f"catalog has only {len(selected)} eligible non-excluded targets; need {count}")
        level += 1
    selected.sort(key=lambda row: stable_rank(seed, "selected-order", str(row["parent_asin"])))
    return selected


def build_split(
    split: str,
    products: list[dict],
    scenarios: list[str],
    config: dict,
    seed: int,
    sealed: bool,
) -> list[dict]:
    override_index = 0
    rows: list[dict] = []
    override_turns = [int(value) for value in config["override_turns"]]
    for index, (product, scenario) in enumerate(zip(products, scenarios), start=1):
        parent_asin = str(product["parent_asin"])
        card, facts = intent_card(product, seed, parent_asin)
        override_turn = None
        if scenario == "intent_override":
            override_turn = override_turns[override_index % len(override_turns)]
            override_index += 1
        behavior = behavior_for(scenario, card, product, facts, seed, parent_asin, override_turn)
        rows.append({
            "sample_id": f"{split}_{index:04d}",
            "source": "supplementary_catalog_synthetic",
            "official": False,
            "schema_version": SCHEMA_VERSION,
            "split": split,
            "sealed": sealed,
            "scenario_type": scenario,
            "category_bucket": "catalog_challenge",
            "difficulty_bucket": "hard" if len(facts) <= 3 else "medium",
            "user_profile": neutral_profile(),
            "ground_truth": {"parent_asin": parent_asin},
            "intent_card": card,
            "behavior": behavior,
            "supplementary_metadata": {
                "constraint_source": "participant_visible_catalog_metadata_only",
                "synthetic": True,
                "official_distribution_claim": False,
                "max_turns": int(config["max_turns"]),
            },
        })
    return rows


def jsonl_bytes(rows: Iterable[dict]) -> bytes:
    return ("".join(canonical_json(row) + "\n" for row in rows)).encode("utf-8")


def generate(
    catalog_path: str | Path,
    public_set_path: str | Path,
    config_path: str | Path,
    generator_commit: str,
) -> tuple[dict[str, bytes], dict]:
    config = load_config(config_path)
    products = load_jsonl(catalog_path)
    public_rows = load_jsonl(public_set_path)
    public_targets = {str(row["ground_truth"]["parent_asin"]) for row in public_rows}
    total = sum(int(spec["size"]) for spec in config["splits"].values())
    selected = select_targets(
        products,
        public_targets,
        total,
        int(config["generator_seed"]),
        int(config["minimum_safe_facts"]),
    )
    payloads: dict[str, bytes] = {}
    manifest_splits: dict[str, dict] = {}
    cursor = 0
    for split, spec in config["splits"].items():
        size = int(spec["size"])
        split_seed = int(config["generator_seed"]) + int(spec["seed_offset"])
        split_products = selected[cursor:cursor + size]
        cursor += size
        scenarios = scenario_sequence(size, config["scenario_mix"], split_seed, split)
        rows = build_split(split, split_products, scenarios, config, split_seed, bool(spec["sealed"]))
        data = jsonl_bytes(rows)
        filename = f"{split}.jsonl"
        payloads[filename] = data
        manifest_splits[split] = {
            "path": f"data/{filename}",
            "row_count": len(rows),
            "scenario_counts": dict(sorted(Counter(row["scenario_type"] for row in rows).items())),
            "target_count": len({row["ground_truth"]["parent_asin"] for row in rows}),
            "sha256": sha256_bytes(data),
            "sealed": bool(spec["sealed"]),
            "status": "sealed_unrun" if spec["sealed"] else "development",
        }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generator_commit": generator_commit,
        "generator_config": config,
        "catalog": {
            "path": "data/catalog.jsonl",
            "sha256": sha256_file(catalog_path),
            "row_count": len(products),
        },
        "public_set": {
            "path": "data/public_set.jsonl",
            "sha256": sha256_file(public_set_path),
            "row_count": len(public_rows),
            "excluded_target_count": len(public_targets),
        },
        "splits": manifest_splits,
        "claims": {
            "source": "supplementary_catalog_synthetic",
            "official": False,
            "uses_purchase_history": False,
            "uses_review_text": False,
            "reconstructs_private_labels": False,
            "selection_depends_on_agent_outputs": False,
        },
        "holdout_contract": {
            "split": "supplementary_holdout",
            "sealed": True,
            "permitted_before_one_shot_evaluation": ["schema_validation", "hash_validation", "aggregate_statistics"],
            "forbidden": ["parameter_tuning", "candidate_selection", "repeated_evaluation", "per_row_manual_analysis"],
        },
        "limitations": [
            "Synthetic shopping intents derived from catalog metadata, not real shopping conversations.",
            "Target selection increases category and attribute coverage and is not an estimate of the private distribution.",
            "Neutral synthetic profiles do not evaluate purchase-history personalization.",
            "Metadata assertions may be noisy or incomplete despite deterministic extraction.",
            "Supplementary results can veto a brittle default but cannot override an official-set regression.",
        ],
    }
    return payloads, manifest


def write_outputs(payloads: dict[str, bytes], manifest: dict, output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for filename, data in payloads.items():
        (destination / filename).write_bytes(data)
    (destination / "supplementary_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate catalog-only supplementary challenge datasets")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--public-set", default="data/public_set.jsonl")
    parser.add_argument("--config", default="supplementary/config.json")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--generator-commit", required=True)
    args = parser.parse_args()
    payloads, manifest = generate(args.catalog, args.public_set, args.config, args.generator_commit)
    write_outputs(payloads, manifest, args.output_dir)
    print(json.dumps({"splits": manifest["splits"], "official": False}, indent=2))


if __name__ == "__main__":
    main()

