"""The one place experiment results are produced and recorded.

Every number that reaches a report must come from here. The rule exists
because it was broken: Phase 1 was reported as uncooperative=0.8372 from an
ad-hoc script using seeds (7,11,23,42,101), while lab/capability.py's
documented default is range(7,12). Re-running the committed harness gives
0.829795. No code differed -- only the seed set, and the ad-hoc runs were
never logged, so the discrepancy was invisible until someone re-ran it.

Each row is one (scenario, config) cell and carries everything needed to
re-run it: commit, dirty state, the exact config, the exact seed list, every
per-seed metric, and mean/sd. Never report an aggregate without its seeds.
"""
from __future__ import annotations

import datetime as dt
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

from lab import scenarios as S

LOG = Path("lab/results.jsonl")
METRICS = ("score", "hr10", "mrr", "mttc")


def git_state() -> dict:
    def sh(*args: str) -> str:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    dirty = sh("git", "status", "--porcelain", "--", "starter", "lab", "tests")
    return {"commit": sh("git", "rev-parse", "--short", "HEAD") or "nogit",
            "dirty": bool(dirty),
            "dirty_files": [l[3:] for l in dirty.splitlines()] if dirty else []}


def _metrics(res: dict) -> dict:
    return {"score": res["recommended_technical_score"], "hr10": res["hit_rate_at_10"],
            "mrr": res["mrr"], "mttc": res["mttc"]}


def cell(scenario_name: str, config: dict, seeds: tuple[int, ...],
         data, tag: str = "") -> dict:
    """Run one (scenario, config) over `seeds` and return a recorded row."""
    samples, ids, cats, prods = data
    scenario = S.BY_NAME[scenario_name]
    started = time.time()
    per_seed = {}
    for sd in seeds:
        per_seed[sd] = _metrics(S.run(scenario, config, samples, ids, cats, prods, seed=sd))
    row = {
        "tag": tag, "scenario": scenario_name, "config": config,
        "seeds": list(seeds),
        "per_seed": {str(k): v for k, v in per_seed.items()},
        "n_seeds": len(seeds),
        "seconds": round(time.time() - started, 1),
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        **git_state(),
    }
    for key in METRICS:
        vals = [m[key] for m in per_seed.values()]
        row[key] = statistics.fmean(vals)
        row[key + "_sd"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def matrix(scenario_names, configs: dict[str, dict], seeds: tuple[int, ...],
           tag: str = "", data=None) -> list[dict]:
    """Run every (scenario, config) pair, printing and logging as it goes."""
    data = data or S.load()
    rows = []
    for name in scenario_names:
        use = (0,) if name == "clean" else seeds     # clean is deterministic
        print(f"\n=== {name}  (seeds={list(use)}) ===")
        print(f"  {'config':<22}{'score':>10}{'sd':>8}{'HR@10':>8}{'MRR':>8}{'MTTC':>7}")
        for label, cfg in configs.items():
            row = cell(name, cfg, use, data, tag=tag or label)
            row["config_label"] = label
            print(f"  {label:<22}{row['score']:>10.6f}{row['score_sd']:>8.4f}"
                  f"{row['hr10']:>8.3f}{row['mrr']:>8.3f}{row['mttc']:>7.2f}", flush=True)
            rows.append(row)
    return rows


if __name__ == "__main__":
    seeds = tuple(int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else
                                   ["7", "8", "9", "10", "11"]))
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else [s.name for s in S.LIBRARY]
    matrix(names, {"default": {}}, seeds, tag="cli")
    print(f"\nlogged to {LOG}")
