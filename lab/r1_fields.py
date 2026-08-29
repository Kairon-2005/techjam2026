"""Phase 7A-R1 integrated feasibility for the semantic FIELD STORE and the real
`sup-train` query and product text. NO labels, NO quality metric, NO MRR.

WHAT NOTES/44 SECTION 8 ASKS FOR, AND WHAT THIS FILE CAN ANSWER. R0.1 measured
synthetic fixtures; section 8 requires a remeasurement on real `sup-train`
queries and product texts. That splits in two:

  * the FIELD STORE -- the opt-in `sem_title` / `sem_desc` the catalog builds
    only when the cascade is armed: its byte size, its additional cold load and
    its RSS delta, each measured in its own process against the real 60 MB
    catalog. **This file measures all of it.**
  * the MODEL -- Top-10 p95, offline load and deterministic output on those same
    texts. That needs onnxruntime, tokenizers and the pinned TinyBERT artifact.
    **This file measures none of it and does not pretend to**; it writes the
    exact queries and product texts out so the model half runs on this corpus
    and not on a fresh one.

The queries and texts here are the REAL ones: `semantic.eligible` and
`semantic.build_query` are pure functions of session state, so they are applied
to the frozen snapshots of an actual A0 `sup-train` run, and the product texts
come from `semantic.product_text` over the real Top-10 each turn emitted. No
target, no label and no `sup-val` row is read.

    python3 -m lab.r1_fields --leased
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

CATALOG = Path("data/catalog.jsonl")
OUT = Path("lab/r1_fields.json")
TEXTS = Path("lab/r1_texts.jsonl")
BUILD_LOG = "lab/r1builds.jsonl"

# notes/44 section 8. The two this file can decide; the rest wait for the model.
COLD_LOAD_CAP_S = 5.0
RSS_DELTA_CAP_MB = 400.0
REPS = 3                       # per catalog stage, each in a fresh interpreter


def _rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw if sys.platform == "darwin" else raw * 1024


def catalog_stage(semantic: bool, catalog: Path = CATALOG) -> dict:
    """One catalog build, in THIS process, from cold.

    A separate process per stage is the point: RSS is a high-water mark, so
    measuring the lean build and the semantic build in one interpreter would
    report the second delta against the first one's peak and understate it.

    `extras=False` because that is what the shipped Agent asks for -- w_pos and
    w_card are both 0, so `build_extras` resolves False -- and measuring a
    configuration nobody runs would gate the arm on work it never does.
    """
    from starter import catalog as C
    before = _rss_bytes()
    t0 = time.perf_counter()
    cat = C._catalog(catalog, extras=False, semantic_fields=semantic)
    # The indexes are lazy. Touch what a turn touches, or "cold load" measures
    # a constructor that deferred all of its work.
    cat.text, cat.cats, cat.feat, cat.idf("dress")
    build_s = time.perf_counter() - t0
    store_bytes = 0
    for name in ("sem_title", "sem_desc"):
        field = getattr(cat, name, None) or {}
        store_bytes += sum(len(k) + len(v) for k, v in field.items())
    return {"semantic_fields": semantic,
            "build_s": round(build_s, 4),
            "rss_before_bytes": before,
            "rss_peak_bytes": _rss_bytes(),
            "rss_delta_bytes": _rss_bytes() - before,
            "field_store_bytes": store_bytes,
            "products": len(cat.text),
            "cpu_count": os.cpu_count()}


def catalog_cost(catalog: Path = CATALOG, reps: int = REPS) -> dict:
    """Both stages, `reps` fresh interpreters each, compared on medians.

    Repetitions because the difference is small enough to be noise: a single
    pair can put the semantic build ahead of the lean one, and reporting that as
    a speedup would be reading run-to-run variance as a result. The spread is
    reported alongside so a reader can see whether the difference clears it.
    """
    stages: dict[str, list[dict]] = {"lean": [], "semantic_on": []}
    for rep in range(int(reps)):
        for semantic in (False, True):
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import json;from lab import r1_fields as R;"
                 f"print(json.dumps(R.catalog_stage({semantic!r}, "
                 f"__import__('pathlib').Path({str(catalog)!r})))) "],
                capture_output=True, text=True)
            if proc.returncode:
                raise RuntimeError(f"catalog stage semantic={semantic} rep {rep} "
                                   f"failed:\n{proc.stderr[-2000:]}")
            stages["semantic_on" if semantic else "lean"].append(
                json.loads(proc.stdout.strip().splitlines()[-1]))

    def med(rows, key):
        return statistics.median([r[key] for r in rows])

    def spread(rows, key):
        vals = [r[key] for r in rows]
        return round(max(vals) - min(vals), 4)

    lean, sem = stages["lean"], stages["semantic_on"]
    extra_load = med(sem, "build_s") - med(lean, "build_s")
    extra_rss = (med(sem, "rss_delta_bytes") - med(lean, "rss_delta_bytes")) / 1e6
    load_spread = max(spread(lean, "build_s"), spread(sem, "build_s"))
    return {
        "reps": int(reps),
        "lean": {"build_s_median": round(med(lean, "build_s"), 4),
                 "build_s_spread": spread(lean, "build_s"),
                 "rss_delta_mb_median": round(med(lean, "rss_delta_bytes") / 1e6, 2),
                 "runs": lean},
        "semantic_on": {"build_s_median": round(med(sem, "build_s"), 4),
                        "build_s_spread": spread(sem, "build_s"),
                        "rss_delta_mb_median": round(med(sem, "rss_delta_bytes") / 1e6, 2),
                        "runs": sem},
        "additional_cold_load_s": round(extra_load, 4),
        "additional_cold_load_within_noise": abs(extra_load) <= load_spread,
        "cold_load_run_spread_s": load_spread,
        "additional_rss_mb": round(extra_rss, 2),
        "field_store_mb": round(med(sem, "field_store_bytes") / 1e6, 2),
        "lean_field_store_bytes": int(med(lean, "field_store_bytes")),
        "cold_load_cap_s": COLD_LOAD_CAP_S,
        "rss_delta_cap_mb": RSS_DELTA_CAP_MB,
        "cold_load_pass": extra_load <= COLD_LOAD_CAP_S,
        "rss_pass": extra_rss <= RSS_DELTA_CAP_MB,
    }


def real_texts(limit: int = 0) -> dict:
    """The queries and product texts an armed cascade would actually see.

    Runs A0 over `sup-train` and applies `eligible` and `build_query` to each
    turn's FROZEN snapshot. Pure functions of state, so this is what the
    cascade would have constructed -- not an approximation of it, and not
    something reconstructed from a trace.
    """
    import starter.agent as A
    from evaluator import local_evaluator as E
    from lab import a1cache as CACHE
    from lab import a1driver as D
    from lab import split as SPLIT
    from starter import semantic as SEM

    split, rows = SPLIT.operative()
    by_id = {str(r["sample_id"]): r for r in rows}
    train = [by_id[i] for i in split.train]
    if limit:
        train = train[:limit]
    catalog_ids, categories, products = E.catalog_index(CATALOG)
    agent = A.Agent(str(CATALOG), config={"semantic_rerank_mode": "on",
                                          "semantic_lambda": 0.25,
                                          "semantic_rerank_k": 10})
    capture = CACHE.Capture()
    started = time.perf_counter()
    with CACHE.capturing(agent, capture):
        for i, sample in enumerate(train, 1):
            D.run_session(agent, sample, capture, catalog_ids, categories, products)
            agent._sessions.clear()
            if i % 100 == 0:
                print(f"  {i}/{len(train)} sessions, "
                      f"{time.perf_counter() - started:.0f}s", flush=True)

    records: list[dict] = []
    reasons: dict[str, int] = {}
    ks: dict[int, int] = {}
    for key in sorted(capture.snapshots):
        snap = capture.snapshots[key]
        uncredible = agent._uncredible(snap.state)
        eligible = SEM.eligible(snap.state, uncredible)
        prefix = list(snap.live_order)[:E.TOP_K]
        k = SEM.effective_k(10, E.TOP_K, len(prefix))
        query = SEM.build_query(snap.state, uncredible) if eligible else ""
        reason = (SEM.REASON_INELIGIBLE if not eligible else
                  SEM.REASON_PREFIX_TOO_SHORT if k < 2 else
                  SEM.REASON_EMPTY_QUERY if not query else
                  SEM.REASON_RERANKED)
        reasons[reason] = reasons.get(reason, 0) + 1
        ks[k] = ks.get(k, 0) + 1
        if reason != SEM.REASON_RERANKED:
            continue
        texts = [SEM.product_text(agent.cat, a) for a in prefix[:k]]
        records.append({"sample_id": snap.sample_id, "turn": snap.turn,
                        "scenario": by_id[snap.sample_id]["scenario_type"],
                        "query": query, "effective_k": k, "texts": texts})

    def stats(values):
        values = sorted(values)
        if not values:
            return {"n": 0}
        return {"n": len(values), "min": values[0], "max": values[-1],
                "p50": statistics.median(values),
                "p95": values[min(len(values) - 1, int(0.95 * len(values)))]}

    queries = [r["query"] for r in records]
    texts = [t for r in records for t in r["texts"]]
    return {
        "sessions": len(train),
        "turns": len(capture.snapshots),
        "reason_counts": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "effective_k_counts": {str(k): v for k, v in sorted(ks.items())},
        "invoking_turns": len(records),
        "query_chars": stats([len(q) for q in queries]),
        # The 200-char cap exists so the query cannot crowd the product out of
        # the 256-token window. How often it BINDS is the thing worth knowing.
        "queries_at_the_cap": sum(1 for q in queries if len(q) >= SEM.QUERY_CHARS - 1),
        "query_cap_chars": SEM.QUERY_CHARS,
        "product_text_chars": stats([len(t) for t in texts]),
        "product_texts": len(texts),
        "empty_product_texts": sum(1 for t in texts if not t),
        "records": records,
        "seconds": round(time.perf_counter() - started, 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.r1_fields")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--leased", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    if args.leased:
        from lab import lease as L
        origin = Path.cwd().resolve()
        argv2 = ["--out", str(origin / "lab")]
        if args.limit:
            argv2 += ["--limit", str(args.limit)]
        script = ("import sys\nfrom lab import r1_fields as R\n"
                  f"sys.exit(R.main({argv2!r}))\n")
        with L.lease("p7a-r1-fields", log=BUILD_LOG) as held:
            held.run(script, expected_cells=1)
        print(f"lease {held.verdict} {held.broke}")
        return 0 if held.verdict == "valid" else 1

    out_dir = Path(args.out) if args.out else Path("lab")
    print("=== catalog field store, one process per stage ===", flush=True)
    catalog = catalog_cost()
    print("=== real sup-train queries and product texts ===", flush=True)
    texts = real_texts(args.limit)
    records = texts.pop("records")
    payload = {"phase": "7A-R1", "component": "semantic field store",
               "ts": dt.datetime.now().isoformat(timespec="seconds"),
               "tag": "p7a-r1-fields", "scenario": "supplementary_dev",
               "config": {}, "seeds": [], "schema_version": 2,
               "smoke": bool(args.limit),
               "catalog": catalog, "corpus": texts,
               "model_measured": False,
               "model_gates_pending": ["topk_p95_ms", "offline_load",
                                       "deterministic_output"],
               "model_absent_note":
                   "onnxruntime, tokenizers and the pinned TinyBERT artifact are "
                   "not present in this environment, so no semantic latency, "
                   "offline-load or determinism number is produced here. Under "
                   "notes/44 section 0.5 a model_absent turn is "
                   "EXPERIMENT-INVALIDATING: running the A2 arm now would "
                   "measure A0 and report it under A2's name."}
    from lab import record as R
    payload.update(R.git_state(dataset="data/supplementary_dev.jsonl"))
    (out_dir / OUT.name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out_dir / TEXTS.name).open("w", encoding="utf-8") as fh:
        for row in records:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    from lab import lease as L
    journal = L.journal_path()
    if journal is not None:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    report(payload)
    return 0


def report(payload: dict) -> None:
    cat, corpus = payload["catalog"], payload["corpus"]
    print("\n=== semantic field store, real catalog ===")
    print(f"  field store                {cat['field_store_mb']} MB")
    noise = " (within run-to-run spread)" if cat["additional_cold_load_within_noise"] else ""
    print(f"  additional cold load       {cat['additional_cold_load_s']} s{noise} "
          f"(cap {cat['cold_load_cap_s']} s)  "
          f"{'PASS' if cat['cold_load_pass'] else 'FAIL'}")
    print(f"  run-to-run spread          {cat['cold_load_run_spread_s']} s "
          f"over {cat['reps']} reps per stage")
    print(f"  additional RSS             {cat['additional_rss_mb']} MB "
          f"(cap {cat['rss_delta_cap_mb']} MB)  "
          f"{'PASS' if cat['rss_pass'] else 'FAIL'}")
    print(f"  lean build / semantic      {cat['lean']['build_s_median']}s / "
          f"{cat['semantic_on']['build_s_median']}s  (medians)")
    print("\n=== real sup-train queries and product texts ===")
    print(f"  sessions / turns           {corpus['sessions']} / {corpus['turns']}")
    print(f"  turns that would invoke    {corpus['invoking_turns']}")
    print(f"  reasons                    {corpus['reason_counts']}")
    print(f"  effective_k                {corpus['effective_k_counts']}")
    print(f"  query chars                {corpus['query_chars']}")
    print(f"  queries at the 200 cap     {corpus['queries_at_the_cap']}")
    print(f"  product text chars         {corpus['product_text_chars']}")
    print(f"  product texts / empty      {corpus['product_texts']} / "
          f"{corpus['empty_product_texts']}")
    print("\n  MODEL GATES NOT MEASURED: topk_p95_ms, offline_load, "
          "deterministic_output")
    print("  " + payload["model_absent_note"][:200])


if __name__ == "__main__":
    sys.exit(main())
