"""Phase 7A-R1 section 8: the MODEL half of integrated feasibility.

R0.1 measured 32 synthetic queries against 100 catalog blobs and reported
15.23 ms at Top-10. `notes/44` section 8 requires that remeasured after
integration, on real `sup-train` queries and product texts. Those are exactly
what `lab/r1_fields.py` wrote to `lab/r1_texts.jsonl` -- the queries
`semantic.build_query` built from real session state, and the products
`semantic.product_text` serialized from the real Top-10 each turn emitted -- so
this file measures the corpus that would actually reach the model, and not a
fresh one selected for the occasion.

GATED: Top-10 p95 <= 25 ms, additional cold load <= +5 s, RSS delta <= +400 MB,
offline load, deterministic output. Thread sensitivity at 1, 2, 4 and the ORT
default is a DIAGNOSTIC and gates nothing: R0.1's 15.23 ms was measured on 10
cores at the ORT default, and a judging host with fewer cores may differ. This
documents that risk instead of pretending one host generalises.

Seven fresh processes for the gated statistic, as R0 and R0.1 used: a p95 from
one process is a p95 of one process's scheduling.

    ./.venv/bin/python -m lab.r1_semantic --leased
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

TEXTS = Path("lab/r1_texts.jsonl")
MODEL_DIR = Path("lab/r0/artifacts/ms-marco-TinyBERT-L2-v2")
OUT = "r1_semantic.json"
BUILD_LOG = "lab/r1builds.jsonl"

MAX_LENGTH = 256
WARMUP = 20
GATED_REPS = 7
THREAD_REPS = 3
THREAD_SETTINGS = (1, 2, 4, 0)          # 0 == the ORT default
P95_CAP_MS = 25.0
COLD_LOAD_CAP_S = 5.0
RSS_DELTA_CAP_MB = 400.0


def _rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw if sys.platform == "darwin" else raw * 1024


def load_records(path: Path = TEXTS) -> list[dict]:
    return [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def rep(threads: int, index: int, texts: Path = TEXTS,
        model_dir: Path = MODEL_DIR) -> dict:
    """One repetition, in THIS process. Cold load is measured from process start.

    `threads` of 0 means the ORT default. R0 recorded that pinning intra-op to
    1 made TinyBERT 97.9 ms against 70.7 ms on the default, so the gated setting
    is the default and the pinned ones are diagnostics: measuring on the slower
    setting would fail the cap by a harness choice rather than by the model.
    """
    t_process = time.perf_counter()
    rss_before = _rss_bytes()

    # ONNX Runtime and tokenizers DIRECTLY. No transformers, no torch: R0
    # measured 53.2 MB for numpy+onnxruntime+tokenizers against 346.1 MB once
    # transformers pulled torch in, and a shipped ONNX Runtime component needs
    # neither.
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    records = load_records(texts)
    tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    tok.enable_truncation(max_length=MAX_LENGTH, strategy="only_second")
    tok.enable_padding()
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if threads:
        opts.intra_op_num_threads = int(threads)
    sess = ort.InferenceSession(
        str(model_dir / "onnx" / "model_qint8_arm64.onnx"),
        sess_options=opts, providers=["CPUExecutionProvider"])
    names = {i.name for i in sess.get_inputs()}

    def order(query: str, passages: list[str]) -> list[int]:
        encs = tok.encode_batch([(query, p) for p in passages])
        feed = {"input_ids": np.array([e.ids for e in encs], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encs],
                                           dtype=np.int64)}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.array([e.type_ids for e in encs],
                                              dtype=np.int64)
        feed = {k: v for k, v in feed.items() if k in names}
        logits = sess.run(None, feed)[0]
        scored = [(float(logits[i][0]), i) for i in range(len(passages))]
        return [i for _, i in sorted(scored, key=lambda kv: (-kv[0], kv[1]))]

    first = order(records[0]["query"], records[0]["texts"])
    cold_s = time.perf_counter() - t_process

    for row in records[:WARMUP]:
        order(row["query"], row["texts"])

    timings: list[float] = []
    orders: list[list[int]] = []
    for row in records:
        t = time.perf_counter()
        got = order(row["query"], row["texts"])
        timings.append((time.perf_counter() - t) * 1000)
        orders.append(got)
    signature = hashlib.sha256(json.dumps(orders).encode()).hexdigest()[:32]

    # A permutation that dropped or invented an index must never be counted as
    # a fast one. Checked here rather than assumed from the sort.
    bad = sum(1 for row, got in zip(records, orders)
              if sorted(got) != list(range(len(row["texts"]))))
    return {
        "threads": threads or "default", "rep": index,
        "records": len(records),
        "cold_load_s": round(cold_s, 4),
        "topk_p95_ms": round(statistics.quantiles(timings, n=20)[18], 4),
        "topk_p50_ms": round(statistics.median(timings), 4),
        "topk_max_ms": round(max(timings), 4),
        "rss_delta_bytes": _rss_bytes() - rss_before,
        "peak_rss_bytes": _rss_bytes(),
        "cpu_count": os.cpu_count(),
        "order_signature": signature,
        "bad_permutations": bad,
        "first_order_head": first[:5],
    }


def _child(threads: int, index: int) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c",
         "import json;from lab import r1_semantic as R;"
         f"print(json.dumps(R.rep({threads!r}, {index!r})))"],
        capture_output=True, text=True,
        env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    if proc.returncode:
        raise RuntimeError(f"threads={threads} rep={index} failed:\n"
                           f"{proc.stderr[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def measure() -> dict:
    """Seven gated processes at the ORT default, three each at 1, 2 and 4."""
    runs: dict[str, list[dict]] = {}
    for threads in THREAD_SETTINGS:
        reps = GATED_REPS if threads == 0 else THREAD_REPS
        key = "default" if threads == 0 else str(threads)
        runs[key] = []
        for index in range(reps):
            row = _child(threads, index)
            runs[key].append(row)
            print(f"  threads={key:<7} rep {index}  p95 {row['topk_p95_ms']:>8.3f} ms  "
                  f"cold {row['cold_load_s']:.3f}s  "
                  f"rss {row['rss_delta_bytes'] / 1e6:.1f} MB", flush=True)
    gated = runs["default"]
    p95 = statistics.median([r["topk_p95_ms"] for r in gated])
    cold = statistics.median([r["cold_load_s"] for r in gated])
    rss_mb = statistics.median([r["rss_delta_bytes"] for r in gated]) / 1e6
    signatures = {r["order_signature"] for r in gated}
    bad = sum(r["bad_permutations"] for rows in runs.values() for r in rows)
    return {
        "runs": runs,
        "gated_reps": len(gated),
        "topk_p95_ms_median": round(p95, 4),
        "topk_p95_ms_spread": round(max(r["topk_p95_ms"] for r in gated)
                                    - min(r["topk_p95_ms"] for r in gated), 4),
        "cold_load_s_median": round(cold, 4),
        "rss_delta_mb_median": round(rss_mb, 2),
        "order_signatures": sorted(signatures),
        "deterministic": len(signatures) == 1,
        "bad_permutations": bad,
        "offline": True,
        "thread_sensitivity_p95_ms": {
            key: round(statistics.median([r["topk_p95_ms"] for r in rows]), 4)
            for key, rows in runs.items()},
        "p95_cap_ms": P95_CAP_MS,
        "cold_load_cap_s": COLD_LOAD_CAP_S,
        "rss_delta_cap_mb": RSS_DELTA_CAP_MB,
        "p95_pass": p95 <= P95_CAP_MS,
        "cold_load_pass": cold <= COLD_LOAD_CAP_S,
        "rss_pass": rss_mb <= RSS_DELTA_CAP_MB,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.r1_semantic")
    ap.add_argument("--leased", action="store_true")
    ap.add_argument("--out", default="lab")
    args = ap.parse_args(argv)
    if args.leased:
        from lab import lease as L
        origin = Path.cwd().resolve()
        script = ("import sys\nfrom lab import r1_semantic as R\n"
                  f"sys.exit(R.main(['--out', {str(origin / 'lab')!r}]))\n")
        with L.lease("p7a-r1-semantic", log=BUILD_LOG) as held:
            held.run(script, expected_cells=1)
        print(f"lease {held.verdict} {held.broke}")
        return 0 if held.verdict == "valid" else 1

    records = load_records()
    print(f"{len(records)} real sup-train records from {TEXTS}", flush=True)
    got = measure()
    row = {"phase": "7A-R1", "component": "A2 semantic cascade, integrated",
           "tag": "p7a-r1-semantic", "scenario": "supplementary_dev",
           "config": {}, "seeds": [], "schema_version": 2,
           "ts": dt.datetime.now().isoformat(timespec="seconds"),
           "model": "cross-encoder/ms-marco-TinyBERT-L2-v2",
           "revision": "81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc",
           "records": len(records), **got}
    row["all_gates_pass"] = bool(
        got["p95_pass"] and got["cold_load_pass"] and got["rss_pass"]
        and got["deterministic"] and got["offline"]
        and got["bad_permutations"] == 0)
    from lab import record as R
    row.update(R.git_state(dataset="data/supplementary_dev.jsonl"))
    out = Path(args.out) / OUT
    out.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    from lab import lease as L
    journal = L.journal_path()
    if journal is not None:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    report(row)
    return 0 if row["all_gates_pass"] else 1


def report(row: dict) -> None:
    print("\n=== A2 integrated feasibility, real sup-train text ===")
    print(f"  records                    {row['records']} queries")
    print(f"  Top-10 p95                 {row['topk_p95_ms_median']} ms "
          f"(cap {row['p95_cap_ms']} ms, spread {row['topk_p95_ms_spread']} ms "
          f"over {row['gated_reps']} processes)  "
          f"{'PASS' if row['p95_pass'] else 'FAIL'}")
    print(f"  cold load                  {row['cold_load_s_median']} s "
          f"(cap {row['cold_load_cap_s']} s)  "
          f"{'PASS' if row['cold_load_pass'] else 'FAIL'}")
    print(f"  RSS delta                  {row['rss_delta_mb_median']} MB "
          f"(cap {row['rss_delta_cap_mb']} MB)  "
          f"{'PASS' if row['rss_pass'] else 'FAIL'}")
    print(f"  offline load               {'PASS' if row['offline'] else 'FAIL'}")
    print(f"  deterministic              {'PASS' if row['deterministic'] else 'FAIL'} "
          f"{row['order_signatures']}")
    print(f"  bad permutations           {row['bad_permutations']}")
    print(f"  thread sensitivity p95     {row['thread_sensitivity_p95_ms']}  "
          f"(DIAGNOSTIC, gates nothing)")
    print(f"  VERDICT                    "
          f"{'PASS' if row['all_gates_pass'] else 'FAIL'}")


if __name__ == "__main__":
    sys.exit(main())
