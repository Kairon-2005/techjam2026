"""Capability scorecard: per-scenario, multi-seed, paired against each config's
own clean baseline.

Reading rule that this report enforces:

    penalty(config, scenario) = score(config, scenario) - score(config, clean)

Comparing raw scores down a column is misleading, because a config that is
already weak on the clean set (override=erase, say) will look weak everywhere.
The penalty isolates how much THAT capability costs THAT config.

Randomised scenarios (which donor constraint, which turns get stonewalled) are
run over several seeds and reported as mean +/- sd, because a single seed on a
30-session subset is noise.

Usage:  python3 -m lab.capability                    # every scenario
        python3 -m lab.capability uncooperative      # selected scenarios
        SEEDS=3 python3 -m lab.capability            # fewer seeds, faster
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

from lab import scenarios as S

LOG = Path("lab/capability.jsonl")
SEEDS = tuple(range(7, 7 + int(os.environ.get("SEEDS", "5"))))

CONFIGS: dict[str, dict] = {
    "default":        {},
    "compat_0.9287":  {"ask_policy": "other"},
    "override=slot":  {"on_override": "slot"},
    "no_softslot":    {"slot_soft": 0.0},
    "no_pop":         {"w_pop": 0.0},
}


def _metrics(res: dict) -> dict:
    return {"score": res["recommended_technical_score"], "hr10": res["hit_rate_at_10"],
            "mrr": res["mrr"], "mttc": res["mttc"]}


def _agg(runs: list[dict]) -> dict:
    out = {}
    for key in ("score", "hr10", "mrr", "mttc"):
        vals = [r[key] for r in runs]
        out[key] = statistics.fmean(vals)
        out[key + "_sd"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return out


def main(names: list[str] | None = None) -> None:
    samples, ids, cats, prods = S.load()
    chosen = [S.BY_NAME[n] for n in names] if names else S.LIBRARY
    clean = S.BY_NAME["clean"]

    # Each config's own clean baseline -- the reference for its penalties.
    base: dict[str, dict] = {}
    print("clean baselines (the reference each config is measured against)")
    print(f"  {'config':<16}{'score':>9}{'HR@10':>8}{'MRR':>8}{'MTTC':>7}")
    for label, cfg in CONFIGS.items():
        m = _metrics(S.run(clean, cfg, samples, ids, cats, prods))
        base[label] = m
        print(f"  {label:<16}{m['score']:>9.4f}{m['hr10']:>8.3f}{m['mrr']:>8.3f}{m['mttc']:>7.2f}",
              flush=True)

    rows = []
    for scenario in chosen:
        if scenario.name == "clean":
            continue
        started = time.time()
        print(f"\n=== {scenario.name} ===")
        print(f"  {scenario.probes}")
        if scenario.notes:
            print(f"  note: {scenario.notes}")
        print(f"  {'config':<16}{'score':>9}{'sd':>7}{'penalty':>10}"
              f"{'HR@10':>8}{'MRR':>8}{'MTTC':>7}")
        cells = {}
        for label, cfg in CONFIGS.items():
            runs = [_metrics(S.run(scenario, cfg, samples, ids, cats, prods, seed=sd))
                    for sd in SEEDS]
            agg = _agg(runs)
            pen = agg["score"] - base[label]["score"]
            cells[label] = {**agg, "penalty": round(pen, 4)}
            print(f"  {label:<16}{agg['score']:>9.4f}{agg['score_sd']:>7.4f}{pen:>+10.4f}"
                  f"{agg['hr10']:>8.3f}{agg['mrr']:>8.3f}{agg['mttc']:>7.2f}", flush=True)
        # Which config loses least to this capability, not which scores highest.
        best = min(cells.items(), key=lambda kv: -kv[1]["penalty"])
        spread = max(c["penalty"] for c in cells.values()) - min(c["penalty"] for c in cells.values())
        verdict = (f"smallest penalty: {best[0]} ({best[1]['penalty']:+.4f})" if spread > 0.002
                   else "FLAT across configs -- no module here is responsible for this capability")
        print(f"  -> {verdict}   ({time.time()-started:.0f}s)")
        rows.append({"scenario": scenario.name, "seeds": list(SEEDS),
                     "baselines": base, "cells": cells, "spread": round(spread, 4)})

    with LOG.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
