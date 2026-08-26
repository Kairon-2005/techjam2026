"""Run agent configurations against the public set and log every result.

Usage:  python3 -m lab.sweep                  # default ablation set
        python3 -m lab.sweep name='{"k":v}'   # ad-hoc configs
The catalog index and metadata are built once and shared across configs.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
import starter.agent as agent_module

CATALOG = "data/catalog.jsonl"
DATASET = "data/public_set.jsonl"
LOG = Path("lab/experiments.jsonl")


def git_hash() -> str:
    """Commit of the code that ran, with a +dirty marker when it is unstaged.

    Without the marker every row claims a commit that may not contain the code
    that produced it -- which is what happened to every row logged before
    2f85538, all of which point at the untouched starter commit.
    """
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        if not head:
            return "nogit"
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "starter", "lab"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{head}+dirty" if dirty else head
    except Exception:
        return "nogit"


def run(configs: dict[str, dict]) -> list[dict]:
    samples = load_jsonl(DATASET)
    catalog_ids, categories, products = catalog_index(CATALOG)
    commit = git_hash()
    rows = []
    for name, cfg in configs.items():
        started = time.time()
        agent = agent_module.Agent(CATALOG, config=cfg)
        result = evaluate(agent, samples, catalog_ids, categories, products)
        row = {
            "name": name, "config": cfg,
            "score": result["recommended_technical_score"],
            "hr10": result["hit_rate_at_10"], "mrr": result["mrr"], "mttc": result["mttc"],
            "by_scenario": {k: v["hit_rate_at_10"] for k, v in result["scenario_metrics"].items()},
            "mrr_by_scenario": {k: v["mrr"] for k, v in result["scenario_metrics"].items()},
            "seconds": round(time.time() - started, 1), "git": commit,
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
        }
        rows.append(row)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        print(f"{name:<24} score={row['score']:.4f}  hr10={row['hr10']:.3f}  "
              f"mrr={row['mrr']:.3f}  mttc={row['mttc']:.2f}  ({row['seconds']}s)", flush=True)
    return rows


DEFAULT_SWEEP = {
    "control_probe":     {"ask_policy": "probe_cycle", "on_override": "erase",
                          "chrome_stop": False},
    "H1_ask_other":      {"ask_policy": "other", "on_override": "erase",
                          "chrome_stop": False},
    "H2_keep_override":  {"ask_policy": "probe_cycle", "on_override": "keep",
                          "chrome_stop": False},
    "H1+H2":             {"ask_policy": "other", "on_override": "keep",
                          "chrome_stop": False},
    "H1+H2+chrome":      {"ask_policy": "other", "on_override": "keep",
                          "chrome_stop": True},
    "other_then_cycle":  {"ask_policy": "other_then_cycle", "on_override": "keep",
                          "chrome_stop": True},
    "H1+decay":          {"ask_policy": "other", "on_override": "decay",
                          "chrome_stop": True},
}

if __name__ == "__main__":
    if len(sys.argv) > 1:
        configs = {}
        for arg in sys.argv[1:]:
            name, _, blob = arg.partition("=")
            configs[name] = json.loads(blob)
    else:
        configs = DEFAULT_SWEEP
    results = run(configs)
    best = max(results, key=lambda r: r["score"])
    print(f"\nbest: {best['name']}  score={best['score']:.4f}")
