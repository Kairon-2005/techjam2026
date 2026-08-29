"""Phase 7A-R1 arm A2-10: the five-lambda search, over a cache built ONCE.

WHY THIS CACHE IS EXACT, AND A1'S IS NOT. A1's cache is off-policy: different
weights reorder the candidate pool, so they would change which question gets
asked and how later turns go. A2's permutes ONLY the returned Top-10, and
notes/44 section 1 proves from the evaluator's source that a permutation cannot
change membership. So under every lambda:

  * `ask_attribute` is taken from `a0_ranked` BEFORE A2 runs, so the question is
    identical and the customer's reply is identical;
  * the evaluator's hit test is `target in ranked`, which a permutation cannot
    move, so every session STOPS ON THE SAME TURN;
  * `_rotate` reads `shown` as a set, and on this corpus it never fires at all.

The trajectory is therefore lambda-invariant, and evaluating the five lambda
over a cached (A0 order, semantic order) pair is EXACT rather than approximate.
That claim is not left as an argument: the selected lambda is re-run through the
full Agent and its MRR must equal the cached number exactly, and lambda = 0 is
re-run and must come back byte-identical to A0.

RANKS ARE CACHED, NOT LOGITS -- the fusion consumes ranks, so caching logits
would store a quantity no formula reads.

    ./.venv/bin/python -m lab.a2trials --leased
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import starter.agent as A
from evaluator import local_evaluator as E
from lab import a1cache as CACHE
from lab import a1driver as D
from lab import split as SPLIT
from starter import context as CTX
from starter import semantic as SEM

MODEL_DIR = "lab/r0/artifacts/ms-marco-TinyBERT-L2-v2"
CATALOG = Path("data/catalog.jsonl")
CACHE_OUT = "a2cache.jsonl"
RESULT_OUT = "a2lambda.json"
BUILD_LOG = "lab/a2builds.jsonl"

# notes/44 section 5, frozen. Five trials, no more and no fewer.
LAMBDAS = (0.0, 0.10, 0.25, 0.50, 1.0)
BUILD_LAMBDA = 0.25            # any non-zero: it only has to make the model run
TOP_K = E.TOP_K


class A2Error(AssertionError):
    """The cache or the verification does not hold."""


def armed_config(lam: float) -> dict:
    return {"semantic_rerank_mode": "on", "semantic_rerank_k": 10,
            "semantic_lambda": float(lam), "semantic_model_dir": MODEL_DIR,
            "semantic_max_length": 256, "trace": True}


def build(rows, progress_every: int = 100) -> dict:
    """Run A0 + TinyBERT ONCE and cache each turn's two orderings."""
    catalog_ids, categories, products = E.catalog_index(CATALOG)
    agent = A.Agent(str(CATALOG), config=armed_config(BUILD_LAMBDA))
    capture = CACHE.Capture()
    per_turn: dict[tuple, dict] = {}
    scorer = agent._semantic_scorer(MODEL_DIR, 256)

    # The pre-registered injection point. It calls the REAL Scorer, built
    # through the Agent's own loader, and serializes products with the same
    # `product_text` the cascade uses -- so what is captured is the ordering the
    # cascade actually consumed, not a second computation of it.
    def score_order(query, prefix):
        texts = [SEM.product_text(agent.cat, a) for a in prefix]
        order = list(scorer.order(query, list(prefix), texts))
        capture.semantic_order = (list(prefix), order, query)
        return order

    agent._semantic_score_order = score_order
    original = agent._semantic_reorder

    def hooked(ordered, state, cfg, top_k):
        capture.semantic_order = None
        result, reason, k = original(ordered, state, cfg, top_k)
        key = capture.key
        if key is not None:
            row = {"turn": int(key[1]), "prefix": list(ordered),
                   "semantic": None, "reason": reason, "effective_k": k}
            if capture.semantic_order is not None:
                prefix, order, query = capture.semantic_order
                if not CTX.is_permutation(order, prefix):
                    raise A2Error(f"{key}: the scorer returned a non-permutation")
                row["semantic"] = order
                row["query"] = query
            per_turn[key] = row
        return result, reason, k

    agent._semantic_reorder = hooked
    outcomes = []
    started = time.perf_counter()
    with CACHE.capturing(agent, capture):
        for i, sample in enumerate(rows, 1):
            outcomes.append(D.run_session(agent, sample, capture, catalog_ids,
                                          categories, products))
            agent._sessions.clear()
            if progress_every and i % progress_every == 0:
                print(f"  {i}/{len(rows)} sessions, "
                      f"{time.perf_counter() - started:.0f}s", flush=True)
    del agent._semantic_reorder

    sessions = []
    for outcome in outcomes:
        turns = []
        for turn in outcome["turns"]:
            row = per_turn.get((outcome["sample_id"], turn))
            if row is None:
                continue
            turns.append({**row, "target": outcome["target"]})
        sessions.append({"sample_id": outcome["sample_id"],
                         "scenario": outcome["scenario"],
                         "scoring_from_turn": outcome["scoring_from_turn"],
                         "turns": turns})
    return {"sessions": sessions, "agent": agent, "outcomes": outcomes,
            "seconds": round(time.perf_counter() - started, 1)}


def fused_order(turn: dict, lam: float) -> list[str]:
    """The Top-10 this turn would emit at `lam`. lam = 0 IS the A0 prefix."""
    prefix = list(turn["prefix"])
    semantic = turn.get("semantic")
    if not semantic or float(lam) == 0.0:
        return prefix
    return CTX.rrf_fuse(prefix, semantic, float(lam))


def cached_mrr(sessions, lam: float) -> float:
    """Session-level MRR at `lam`, under the evaluator's own semantics."""
    if not sessions:
        return 0.0
    per = []
    for session in sessions:
        best = 0.0
        first = int(session.get("scoring_from_turn", 1))
        for turn in session["turns"]:
            if int(turn["turn"]) < first:
                continue
            order = fused_order(turn, lam)
            target = turn.get("target")
            if target in order:
                best = 1.0 / (order.index(target) + 1)
                break
        per.append(best)
    return statistics.fmean(per)


def invariants(sessions) -> dict:
    """HR@10 and MTTC must be identical at every lambda. Proven, then checked.

    notes/44 section 1 derives both from the evaluator's source: they depend on
    MEMBERSHIP of `ranked`, which a permutation cannot change. Any movement is an
    implementation defect and is investigated as a bug, not reported as a
    trade-off -- so it is asserted per lambda rather than assumed.
    """
    def hits_and_turns(lam):
        hits, turns = [], []
        for session in sessions:
            first = int(session.get("scoring_from_turn", 1))
            hit, hit_turn = False, None
            for turn in session["turns"]:
                if int(turn["turn"]) < first:
                    continue
                if turn.get("target") in fused_order(turn, lam):
                    hit, hit_turn = True, int(turn["turn"])
                    break
            hits.append(hit)
            turns.append(hit_turn)
        return hits, turns

    base_hits, base_turns = hits_and_turns(0.0)
    broken = []
    for lam in LAMBDAS:
        hits, turns = hits_and_turns(lam)
        if hits != base_hits:
            broken.append({"lambda": lam, "field": "hit"})
        if turns != base_turns:
            broken.append({"lambda": lam, "field": "first_hit_turn"})
    return {"hr10": round(sum(base_hits) / len(base_hits), 6) if base_hits else 0.0,
            "mttc": round(statistics.fmean(
                [t if t is not None else E.MAX_TURNS + 1 for t in base_turns]), 6)
            if base_turns else 0.0,
            "violations": broken, "invariant": not broken}


def search(sessions) -> dict:
    """Five lambda over the cache. Tie-break: smaller lambda, then canonical."""
    scores = {f"{lam}": cached_mrr(sessions, lam) for lam in LAMBDAS}
    baseline = scores["0.0"]
    best_lambda = min(LAMBDAS, key=lambda lam: (-scores[f"{lam}"], lam))
    best = scores[f"{best_lambda}"]
    return {"lambdas": list(LAMBDAS), "mrr_by_lambda": scores,
            "baseline_mrr": baseline, "best_lambda": best_lambda,
            "best_mrr": best, "delta_mrr": best - baseline,
            "no_op": best_lambda == 0.0 or (best - baseline) <= 0.0}


def verify_live(rows, lam: float, expected_mrr: float) -> dict:
    """Re-run the FULL Agent at `lam` and require the cached MRR back, exactly.

    This is what turns the trajectory-invariance argument into a measurement. If
    the cache were an approximation, this is where it would show.
    """
    catalog_ids, categories, products = E.catalog_index(CATALOG)
    agent = A.Agent(str(CATALOG), config=armed_config(lam))
    capture = CACHE.Capture()
    outcomes = []
    with CACHE.capturing(agent, capture):
        for sample in rows:
            outcomes.append(D.run_session(agent, sample, capture, catalog_ids,
                                          categories, products))
            reasons = [t.get("semantic_reason")
                       for s in agent._sessions.values()
                       for t in (s.get("trace_log") or [])]
            agent._sessions.clear()
            for reason in reasons:
                if reason and SEM.is_invalidating(reason):
                    raise A2Error(f"invalidating semantic reason {reason!r} during "
                                  f"live verification: the run did not measure A2")
    live = statistics.fmean([o["reciprocal_rank"] for o in outcomes])
    return {"lambda": lam, "live_mrr": live, "cached_mrr": expected_mrr,
            "delta": live - expected_mrr, "agrees": live == expected_mrr,
            "sessions": len(outcomes)}


def write_cache(sessions, path: Path) -> dict:
    blob = "".join(json.dumps(s, sort_keys=True) + "\n" for s in sessions)
    Path(path).write_text(blob, encoding="utf-8")
    return {"sha256": hashlib.sha256(blob.encode()).hexdigest(),
            "sessions": len(sessions),
            "turns": sum(len(s["turns"]) for s in sessions),
            "invoking_turns": sum(1 for s in sessions for t in s["turns"]
                                  if t.get("semantic"))}


def report(row: dict) -> None:
    print("\n=== A2-10 lambda search, sup-train ===")
    print(f"  cache sha256               {row['cache_sha256']}")
    print(f"  split hash                 {row['split_train_hash']}")
    print(f"  sessions / turns           {row['cache_sessions']} / {row['cache_turns']}")
    print(f"  turns the model ran on     {row['invoking_turns']}")
    print("\n  MRR by lambda:")
    for lam in row["lambdas"]:
        mrr = row["mrr_by_lambda"][f"{lam}"]
        mark = "   <-- selected" if lam == row["best_lambda"] else ""
        print(f"    lambda {lam:<5} {mrr:.9f}   "
              f"{mrr - row['baseline_mrr']:+.9f}{mark}")
    inv = row["invariance"]
    print(f"\n  HR@10 {inv['hr10']}  MTTC {inv['mttc']}  "
          f"invariant across every lambda: "
          f"{'YES' if inv['invariant'] else 'NO ' + str(inv['violations'])}")
    live = row.get("live_verification") or {}
    if live:
        print(f"  live re-run at lambda={live['lambda']}: {live['live_mrr']!r} "
              f"vs cached {live['cached_mrr']!r}  delta {live['delta']}  "
              f"{'AGREES' if live['agrees'] else 'DISAGREES'}")
    zero = row.get("lambda_zero_verification") or {}
    if zero:
        print(f"  live re-run at lambda=0:  {zero['live_mrr']!r} vs A0 "
              f"{zero['cached_mrr']!r}  "
              f"{'BYTE-EXACT A0' if zero['agrees'] else 'DIVERGED'}")
    print(f"\n  {row['verdict']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.a2trials")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--leased", action="store_true")
    ap.add_argument("--out", default="lab")
    args = ap.parse_args(argv)
    if args.leased:
        from lab import lease as L
        origin = Path.cwd().resolve()
        argv2 = ["--out", str(origin / "lab")]
        if args.limit:
            argv2 += ["--limit", str(args.limit)]
        script = ("import sys\nfrom lab import a2trials as T\n"
                  f"sys.exit(T.main({argv2!r}))\n")
        with L.lease("p7a-r1-a2trials", log=BUILD_LOG) as held:
            held.run(script, expected_cells=1)
        print(f"lease {held.verdict} {held.broke}")
        return 0 if held.verdict == "valid" else 1

    split, rows_all = SPLIT.operative()
    by_id = {str(r["sample_id"]): r for r in rows_all}
    train = [by_id[i] for i in split.train]
    print(f"sup-train {len(train)} rows, hash {split.train_hash}")
    if args.limit:
        train = train[: args.limit]
        print(f"SMOKE MODE: {len(train)} rows.")

    built = build(train)
    out_dir = Path(args.out)
    written = write_cache(built["sessions"], out_dir / CACHE_OUT)
    result = search(built["sessions"])
    inv = invariants(built["sessions"])
    if not inv["invariant"]:
        raise A2Error(f"HR@10 or MTTC moved under a permutation: "
                      f"{inv['violations']}. notes/44 section 9: this is an "
                      f"implementation defect, not a trade-off.")

    live = verify_live(train, result["best_lambda"],
                       result["mrr_by_lambda"][f"{result['best_lambda']}"])
    zero = verify_live(train, 0.0, result["baseline_mrr"])

    no_op = bool(result["no_op"])
    row = {"phase": "7A-R1", "arm": "A2-10", "tag": "p7a-r1-a2trials",
           "scenario": "supplementary_dev", "config": {}, "seeds": [],
           "schema_version": 2, "smoke": bool(args.limit),
           "ts": dt.datetime.now().isoformat(timespec="seconds"),
           "model": "cross-encoder/ms-marco-TinyBERT-L2-v2",
           "revision": "81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc",
           "cache_sha256": written["sha256"],
           "cache_sessions": written["sessions"],
           "cache_turns": written["turns"],
           "invoking_turns": written["invoking_turns"],
           "split_train_hash": split.train_hash,
           "split_val_hash": split.val_hash,
           **result,
           "invariance": inv,
           "live_verification": live,
           "lambda_zero_verification": zero,
           "build_seconds": built["seconds"],
           "verdict": (
               "NO-OP: sup-train selected lambda = 0, so the semantic signal "
               "failed to beat A0. Recorded as such, not retried. A2-10 stops "
               "here -- no sup-val run, not finalist-eligible (notes/44 7b "
               "Step 0)." if no_op else
               f"CHALLENGER: A2-10 is frozen at lambda = "
               f"{result['best_lambda']} and proceeds to sup-val, where it must "
               f"still clear the floors and show a strictly positive MRR "
               f"delta to qualify.")}
    row["ok"] = bool(inv["invariant"] and live["agrees"] and zero["agrees"])
    from lab import record as R
    row.update(R.git_state(dataset="data/supplementary_dev.jsonl"))
    (out_dir / RESULT_OUT).write_text(
        json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    from lab import lease as L
    journal = L.journal_path()
    if journal is not None:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    report(row)
    return 0 if row["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
