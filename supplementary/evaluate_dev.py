from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluator import local_evaluator as official
from starter.agent import Agent


def ensure_development_rows(rows: list[dict]) -> None:
    if not rows:
        raise ValueError("supplementary development dataset is empty")
    for row in rows:
        if row.get("source") != "supplementary_catalog_synthetic" or row.get("official") is not False:
            raise ValueError("adapter only accepts explicitly non-official supplementary rows")
        if row.get("split") != "supplementary_dev" or row.get("sealed") is not False:
            raise ValueError("sealed holdout evaluation is forbidden; this adapter accepts supplementary_dev only")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the unmodified official protocol on supplementary_dev only"
    )
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/supplementary_dev.jsonl")
    parser.add_argument("--output", default="supplementary_dev_results.json")
    args = parser.parse_args()
    rows = official.load_jsonl(args.dataset)
    ensure_development_rows(rows)
    catalog_ids, categories, products = official.catalog_index(args.catalog)
    result = official.evaluate(Agent(args.catalog), rows, catalog_ids, categories, products)
    result = {
        "source": "supplementary_catalog_synthetic",
        "official": False,
        "split": "supplementary_dev",
        "limitations": "Robustness-veto evidence only; not an official or private-distribution score.",
        **result,
    }
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "sessions"}, indent=2))


if __name__ == "__main__":
    main()

