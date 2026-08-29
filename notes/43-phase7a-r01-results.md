# Phase 7A-R0.1 results — A2-10 passes every frozen cap

Pre-registered at `0afe368` (`notes/42`), which was written before this run and
is not edited by it.

**No labels, no quality metric, no MRR, no `sup-val`, no public 200, no sealed
holdout.** Same frozen fixtures as R0: 32 synthetic query templates, 100 catalog
blobs by `sha256(asin)` rank.

## Verdict: **A2-10 passes. 7/7 repetitions, zero aborts, zero errors.**

`cross-encoder/ms-marco-TinyBERT-L2-v2` @ `81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc`,
`semantic_rerank_k = 10`.

| cap | measured | limit | |
|---|---|---|---|
| **Top-10 end-to-end p95** | **15.23 ms** | ≤ 25 ms | **PASS** |
| additional cold load | 0.47 s | ≤ 5.0 s | PASS |
| RSS delta | 134.53 MB | ≤ 400 MB | PASS |
| total artifact | 5.46 MB | ≤ 120 MB | PASS |
| largest individual file | 4.31 MiB | < 95 MiB | PASS |
| determinism | bit-identical across 7 processes | required | PASS |
| offline reload | verified before benchmarking | required | PASS |

Per-repetition Top-10 p95, sorted: **14.20, 14.59, 15.08, 15.23, 15.26, 15.35,
15.79 ms**. The **worst** repetition is 15.79 ms — still **1.6× inside** the
cap — so the verdict does not depend on which repetition is taken as
representative.

## A2-30 is untouched

`notes/41`'s verdict stands exactly as written: **A2-30 is infeasible under the
frozen 25 ms cap**, at 69.68 ms. Its cap was not relaxed, its rows were not
reused, and **A2-30 never enters quality evaluation**. R0.1 measured a
different configuration and produced its own rows.

## Measuring rather than dividing changed the number by 8 ms

`notes/42` forbade inferring Top-10 latency from R0's Top-30 figure. That was
the right call, and by more than expected:

| | value |
|---|---|
| naive division, `69.68 / 3` | **23.23 ms** |
| **measured** | **15.23 ms** |
| error | **8.0 ms** |

The division would have landed **1.77 ms under a 25 ms cap** — a pass on
arithmetic, with less margin than the estimate's own error. It was wrong in the
conservative direction here, but a decision that close to a threshold cannot
rest on an estimate whose error exceeds its margin. Scoring 10 pairs is
**cheaper than a third** of scoring 30, because padding is to the longest
sequence *in the batch* and a smaller batch has a shorter longest member.

## Environment

The R0.1 spike installed **only the three packages the frozen path uses** —
`onnxruntime==1.24.1`, `numpy==2.5.2`, `tokenizers==0.22.2` — at the versions
pinned in `lab/r0/r0-requirements.txt`. Verified in-process: **torch not
loaded, transformers not loaded.** Model-absent baseline RSS **53.9 MB**.

The artifact was fetched by **pinned-revision URL** rather than through
`huggingface_hub`, which keeps the measurement environment to exactly three
packages and exercises the path a vendored artifact would take:
`https://huggingface.co/<model>/resolve/81d1926f…/<file>`.
`onnx/model_qint8_arm64.onnx` sha256 `7497b40504d425ef…`, byte-identical in
size to R0's copy (5.46 MB total, 4.31 MiB largest).

The system Python was never modified — `numpy` and `torch` remain absent from
it.

## Consequences, per the pre-registered decision rule

* **A2-10 is frozen as the sole semantic candidate for R1.**
* The quality phase has exactly **three arms: A0, A1, A2-10.**
* **A2-30 remains a failed feasibility arm** and never enters quality
  evaluation.
* **Fusion is not implemented. No labelled validation is touched.** R1's
  quality pre-registration comes next and must fix semantic query
  construction, product text construction, semantic-only versus fusion, any
  fusion weight search on `sup-train` only, and route eligibility — all before
  `sup-val`.

## What this does and does not establish

**Establishes:** a 4.5 MB INT8 cross-encoder can rerank A0's Top-10 on CPU
within every resource cap this project froze before measuring, deterministically
and offline.

**Does not establish:** that it *should*. **No quality signal has been measured
at any point.** A2-10 might reorder the Top-10 worse than A0 does — R0.1 cannot
say, and its gates were never about that. That question belongs to R1, and R1
has not started.
