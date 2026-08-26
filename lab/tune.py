"""Random search over rerank weights with honest k-fold cross-validation.

One full evaluation yields per-session results, so fold metrics are computed
post-hoc for free. We report:
  * full  — score on all 200 sessions (optimistic, this is what we tuned on)
  * cv    — 5-fold estimate of the *tuning procedure*: for each fold, pick the
            config that maximises the other four folds, then score it on the
            held-out fold. This is the number that estimates private-set
            performance.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
import starter.agent as A

CATALOG, DATASET = "data/catalog.jsonl", "data/public_set.jsonl"
STORE = Path("lab/tuning_runs.jsonl")

GRID = {
    "candidates": [50, 100, 150, 200],
    "w_bm25":   [0.0, 0.15, 0.3, 0.5],
    "w_phrase": [2.0, 3.0, 4.0, 5.0],
    "w_idf":    [0.25, 0.5, 1.0],
    "w_cat":    [0.25, 0.5, 1.0],
    "w_pop":    [2.0, 2.5, 3.0, 4.0],
    "w_exact":  [0.5, 1.0, 1.5, 2.0],
    "w_field":  [0.0, 1.0, 2.0],
}
FIXED = {"rerank": True, "ask_policy": "other", "on_override": "keep",
         "chrome_stop": True, "pop_mode": "log"}


def score_of(sessions: list[dict]) -> float:
    n = len(sessions)
    if not n:
        return 0.0
    hr = sum(s["hit"] for s in sessions) / n
    mrr = statistics.fmean(s["reciprocal_rank"] for s in sessions)
    mttc = statistics.fmean(s["first_hit_turn"] or 11 for s in sessions)
    eff = max(0.0, min(1.0, (11 - mttc) / 10))
    return 0.50 * hr + 0.30 * mrr + 0.20 * eff


def folds(samples: list[dict], k: int = 5) -> list[set[str]]:
    """Stratified by scenario_type so every fold keeps the 40/40/15/5 mix."""
    buckets: dict[str, list[str]] = {}
    for s in samples:
        buckets.setdefault(s["scenario_type"], []).append(s["sample_id"])
    out: list[set[str]] = [set() for _ in range(k)]
    for ids in buckets.values():
        for i, sid in enumerate(sorted(ids)):
            out[i % k].add(sid)
    return out


def main(n_trials: int = 30, seed: int = 0) -> None:
    rng = random.Random(seed)
    samples = load_jsonl(DATASET)
    ids, cats, prods = catalog_index(CATALOG)
    seen: set[tuple] = set()
    runs: list[dict] = []
    for trial in range(n_trials):
        for _ in range(50):
            cfg = {k: rng.choice(v) for k, v in GRID.items()}
            key = tuple(sorted(cfg.items()))
            if key not in seen:
                seen.add(key)
                break
        full_cfg = {**FIXED, **cfg}
        res = evaluate(A.Agent(CATALOG, config=full_cfg), samples, ids, cats, prods)
        row = {"config": cfg, "score": res["recommended_technical_score"],
               "hr10": res["hit_rate_at_10"], "mrr": res["mrr"], "mttc": res["mttc"],
               "sessions": [{"sample_id": s["sample_id"], "hit": s["hit"],
                             "first_hit_turn": s["first_hit_turn"],
                             "reciprocal_rank": s["reciprocal_rank"]} for s in res["sessions"]]}
        runs.append(row)
        with STORE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"[{trial+1:>2}/{n_trials}] {res['recommended_technical_score']:.4f}  "
              f"hr={res['hit_rate_at_10']:.3f} mrr={res['mrr']:.3f} mttc={res['mttc']:.2f}  {cfg}",
              flush=True)
    report(runs, samples)


def report(runs: list[dict], samples: list[dict]) -> None:
    fold_ids = folds(samples)
    best = max(runs, key=lambda r: r["score"])
    print(f"\n=== 全集最优（在 200 条上调出来的，乐观）===")
    print(f"  score={best['score']:.4f}  hr10={best['hr10']:.3f} mrr={best['mrr']:.3f} "
          f"mttc={best['mttc']:.2f}")
    print(f"  {best['config']}")

    held = []
    for i, test in enumerate(fold_ids):
        train_pick = max(runs, key=lambda r: score_of([s for s in r["sessions"]
                                                       if s["sample_id"] not in test]))
        held.append(score_of([s for s in train_pick["sessions"] if s["sample_id"] in test]))
    print(f"\n=== 5 折交叉验证（估计调参流程的泛化能力）===")
    for i, h in enumerate(held):
        print(f"  fold {i}: held-out score = {h:.4f}")
    print(f"  均值 = {statistics.fmean(held):.4f}   标准差 = {statistics.pstdev(held):.4f}")
    gap = best["score"] - statistics.fmean(held)
    print(f"  过拟合缺口 = {gap:+.4f}  （全集最优 − 交叉验证均值）")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
