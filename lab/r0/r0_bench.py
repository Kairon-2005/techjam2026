"""Phase 7A-R0 feasibility benchmark. NO labels, NO quality metric, NO MRR.

Runs the workload frozen in notes/40-phase7a-design.md revision 5, identically
for every candidate:

    end-to-end scoring of a fixed Top-30 prefix --
    query construction and tokenization, 30 query-product pairs, model
    inference, score extraction, deterministic ordering.

Gated statistic is the BATCHED Top-30 that would actually ship. Batch 1/8/32
are diagnostics and are never gated: a production turn receives all 30
candidates at once, so gating batch 1 would select a model on a workload that
never occurs.

Pairs go through the tokenizer's NATIVE pair API. No literal " [SEP] " string:
BERT, RoBERTa, DeBERTa and T5 use different special-token contracts, and
hard-coding one silently mis-tokenizes for every family that does not use it.

This file reads no target, no label, no sup-val and no public sample. Its
fixtures are synthetic query templates and catalog product text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import statistics
import sys
import time
from pathlib import Path

SEQ_LEN = 256
PREFIX_K = 30          # the frozen semantic_rerank_k
WARMUP = 20
BATCH_DIAGNOSTICS = (1, 8, 32)

# 32 synthetic, unlabelled queries built from sup-train MESSAGE TEMPLATES only.
# No ground truth, no target, no label of any kind reaches this file.
QUERIES = [
    "I'm looking for a cotton summer dress, but I'm still exploring.",
    "I need running shoes for the gym. A key requirement is: breathable.",
    "Looking for a warm winter coat, something in wool.",
    "I want a slim fit shirt for work, long sleeve.",
    "Show me leather ankle boots in black.",
    "I need a lightweight jacket for hiking.",
    "Looking for a formal dress for an evening event.",
    "I want comfortable yoga leggings, high waisted.",
    "Something casual for travel, easy to pack.",
    "I need a swimsuit for summer holidays.",
    "Looking for a hooded sweatshirt in grey.",
    "I want a silk blouse for the office.",
    "Show me durable work trousers with pockets.",
    "I need a plus size cardigan, soft material.",
    "Looking for a denim jacket, relaxed fit.",
    "I want sandals for warm weather.",
    "Something sleeveless for hot days.",
    "I need thermal base layers for skiing.",
    "Looking for a petite blazer in navy.",
    "I want a maxi skirt with a floral pattern.",
    "Show me waterproof outdoor gear.",
    "I need socks that last, nothing thin.",
    "Looking for a v-neck sweater in merino.",
    "I want a crossbody bag for everyday use.",
    "Something oversized and comfortable for lounging.",
    "I need a belt in brown leather.",
    "Looking for polyester activewear that dries fast.",
    "I want a zip up fleece for cold mornings.",
    "Show me a short sleeve polo in white.",
    "I need gloves for winter cycling.",
    "Looking for a rain coat, packable.",
    "I want a linen shirt for summer.",
]


def _catalog_blobs(catalog: Path, n: int = 100) -> list[str]:
    """n product blobs, chosen deterministically by sha256(asin) rank."""
    rows = []
    with catalog.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            asin = str(rec.get("parent_asin") or "")
            if not asin:
                continue
            blob = " ".join(str(rec.get(f) or "") for f in
                            ("title", "features", "details"))
            rows.append((hashlib.sha256(asin.encode()).hexdigest(), asin, blob))
            if len(rows) >= 60000:          # bounded read; ranking is over this
                break
    rows.sort()
    return [" ".join(b.split())[:2000] for _, _, b in rows[:n]]


def _rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw if sys.platform == "darwin" else raw * 1024


def run(model_dir: Path, catalog: Path, rep: int) -> dict:
    """One repetition, in this process. Cold load is measured from import."""
    t_process = time.perf_counter()
    rss_before = _rss_bytes()

    # ONNX Runtime DIRECTLY, with numpy tensors. Not optimum, and no torch.
    #
    # The smoke run used optimum with return_tensors="pt" and reported a 530 MB
    # RSS delta -- which is torch's import, not the model. A shipped "ONNX
    # Runtime CPU" component does not need torch at all, so measuring one that
    # imports it would have gated every candidate on a dependency the proposal
    # does not include. Caught before the seven repetitions, not after.
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    blobs = _catalog_blobs(catalog)
    queries = list(QUERIES)

    t0 = time.perf_counter()
    # `tokenizers` directly, NOT transformers. Measured on this host:
    #   numpy + onnxruntime + tokenizers    ->  53.2 MB, torch NOT loaded
    #   numpy + onnxruntime + transformers  -> 346.1 MB, torch loaded
    # transformers lazily imports torch, and a shipped ONNX Runtime component
    # needs neither. tokenizers IS the native tokenizer -- transformers wraps
    # it -- so this is the same encoding through one fewer layer.
    tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    tok.enable_truncation(max_length=SEQ_LEN, strategy="only_second")
    tok.enable_padding()
    opts = ort.SessionOptions()
    # ORT's DEFAULT intra-op threading. An earlier draft pinned this to 1 with
    # the note "one request at a time", which conflated request concurrency
    # with intra-op parallelism: a single request should use the cores it has,
    # and pinning 1 thread made TinyBERT 97.9 ms median against 70.7 ms on the
    # default. Measured on the slower setting the caps would have been failed
    # by a harness choice rather than by the model.
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(
        str(model_dir / "onnx" / "model_qint8_arm64.onnx"),
        sess_options=opts, providers=["CPUExecutionProvider"])
    input_names = {i.name for i in sess.get_inputs()}
    load_s = time.perf_counter() - t0

    def score_prefix(query: str, passages: list[str], batch: int) -> list[int]:
        """End-to-end: tokenize pairs, infer, extract, order deterministically."""
        scores: list[float] = []
        for i in range(0, len(passages), batch):
            chunk = passages[i:i + batch]
            # NATIVE pair encoding: the tokenizer builds the special-token
            # layout for its own family. No literal " [SEP] " anywhere.
            encs = tok.encode_batch([(query, p) for p in chunk])
            feed = {"input_ids": np.array([e.ids for e in encs], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in encs],
                                               dtype=np.int64)}
            if "token_type_ids" in input_names:
                feed["token_type_ids"] = np.array([e.type_ids for e in encs],
                                                  dtype=np.int64)
            feed = {k: v for k, v in feed.items() if k in input_names}
            logits = sess.run(None, feed)[0]
            scores.extend(float(logits[j][0]) for j in range(len(chunk)))
        # Deterministic ordering: score desc, then original index asc.
        return [i for i, _ in sorted(enumerate(scores), key=lambda kv: (-kv[1], kv[0]))]

    prefix = blobs[:PREFIX_K]

    # cold load = process start -> first scored Top-30 returned
    first_order = score_prefix(queries[0], prefix, PREFIX_K)
    cold_s = time.perf_counter() - t_process

    for q in queries[:WARMUP]:
        score_prefix(q, prefix, PREFIX_K)

    # GATED: the batched Top-30 that would ship.
    gated = []
    for q in queries:
        t = time.perf_counter()
        score_prefix(q, prefix, PREFIX_K)
        gated.append((time.perf_counter() - t) * 1000)

    # DIAGNOSTIC ONLY, never gated.
    diag = {}
    for b in BATCH_DIAGNOSTICS:
        samples = []
        for q in queries[:16]:
            t = time.perf_counter()
            score_prefix(q, prefix, b)
            samples.append((time.perf_counter() - t) * 1000)
        diag[f"batch_{b}_median_ms"] = round(statistics.median(samples), 4)

    order_sig = hashlib.sha256(
        json.dumps([score_prefix(q, prefix, PREFIX_K) for q in queries[:8]]).encode()
    ).hexdigest()[:32]

    return {
        "rep": rep, "model_dir": model_dir.name,
        "cold_load_s": round(cold_s, 4),
        "artifact_load_s": round(load_s, 4),
        "top30_p95_ms": round(statistics.quantiles(gated, n=20)[18], 4),
        "top30_median_ms": round(statistics.median(gated), 4),
        "peak_rss_bytes": _rss_bytes(),
        "rss_delta_bytes": _rss_bytes() - rss_before,
        "intra_op_threads": opts.intra_op_num_threads or os.cpu_count(),
        "cpu_count": os.cpu_count(),
        "order_signature": order_sig,
        "first_order_head": first_order[:5],
        **diag,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--catalog", default="data/catalog.jsonl")
    ap.add_argument("--rep", type=int, required=True)
    a = ap.parse_args()
    try:
        out = run(Path(a.model_dir), Path(a.catalog), a.rep)
        print(json.dumps({"event": "result", "payload": out}), flush=True)
    except BaseException as exc:
        print(json.dumps({"event": "error", "rep": a.rep,
                          "model_dir": Path(a.model_dir).name,
                          "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
