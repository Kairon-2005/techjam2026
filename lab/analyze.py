"""Render lab/experiments.jsonl as an ablation table (latest run per config)."""
from __future__ import annotations
import json
from pathlib import Path

LOG = Path("lab/experiments.jsonl")

def main() -> None:
    rows = [json.loads(l) for l in LOG.open(encoding="utf-8") if l.strip()]
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["name"]] = r
    ordered = sorted(latest.values(), key=lambda r: -r["score"])
    print(f"{'config':<22}{'score':>8}{'HR@10':>8}{'MRR':>8}{'MTTC':>7}   "
          f"{'bnd':>5}{'brw':>6}{'buy':>6}{'ovr':>6}")
    print("-" * 78)
    for r in ordered:
        b = r.get("by_scenario", {})
        print(f"{r['name']:<22}{r['score']:>8.4f}{r['hr10']:>8.3f}{r['mrr']:>8.3f}"
              f"{r['mttc']:>7.2f}   {b.get('boundary',0):>5.2f}{b.get('browsing',0):>6.2f}"
              f"{b.get('buying',0):>6.2f}{b.get('intent_override',0):>6.2f}")
    print(f"\n{len(rows)} runs logged, {len(latest)} distinct configs")

if __name__ == "__main__":
    main()
