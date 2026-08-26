"""Capability scorecard: one score per (scenario, config) instead of one number.

A single public-set number hides which module is carrying the system and which
is untested. This runs the scenario library against a set of agent configs, so
each module can be developed against the scenario that actually exercises it.

Read a column to judge a config; read a row to find which module a capability
depends on. A row where every config ties is a capability nothing exercises.

Usage:  python3 -m lab.capability                 # default matrix
        python3 -m lab.capability override_genuine vague_start
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from lab import scenarios as S

LOG = Path("lab/capability.jsonl")

# Each config isolates one module so a row shows what that capability rests on.
CONFIGS: dict[str, dict] = {
    "default":       {},
    "ask=other":     {"ask_policy": "other"},
    "override=slot": {"on_override": "slot"},
    "override=erase": {"on_override": "erase"},
    "no_guidance":   {"overgeneral_cats": 0},
    "profile=1.0":   {"w_profile": 1.0},
    "no_softslot":   {"slot_soft": 0.0},
    "no_pop":        {"w_pop": 0.0},
}


def main(names: list[str] | None = None) -> None:
    samples, ids, cats, prods = S.load()
    chosen = [S.BY_NAME[n] for n in names] if names else S.LIBRARY
    width = max(len(c) for c in CONFIGS) + 2

    header = f"{'scenario':<22}" + "".join(f"{c:>{width}}" for c in CONFIGS)
    print(header)
    print("-" * len(header))

    rows = []
    for scenario in chosen:
        cells, started = {}, time.time()
        for label, cfg in CONFIGS.items():
            res = S.run(scenario, cfg, samples, ids, cats, prods)
            cells[label] = res["recommended_technical_score"]
        best = max(cells.values())
        spread = best - min(cells.values())
        line = f"{scenario.name:<22}"
        for label in CONFIGS:
            mark = "*" if cells[label] == best and spread > 0.002 else " "
            line += f"{cells[label]:>{width-1}.4f}{mark}"
        print(line + f"   ({time.time()-started:.0f}s)", flush=True)
        rows.append({"scenario": scenario.name, "probes": scenario.probes,
                     "scores": cells, "spread": round(spread, 4)})

    with LOG.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    print("\n* = best in row (only marked when the row actually separates configs)\n")
    print("what each row probes:")
    for scenario in chosen:
        flat = next(r for r in rows if r["scenario"] == scenario.name)["spread"]
        verdict = "SEPARATES" if flat > 0.002 else "flat - nothing here depends on these knobs"
        print(f"  {scenario.name:<22} {scenario.probes}")
        print(f"  {'':<22} spread={flat:.4f}  {verdict}")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
