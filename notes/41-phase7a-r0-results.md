# Phase 7A-R0 results — A2 is infeasible under the frozen caps

Design frozen at `4be1b68` (`notes/40` revision 5). R0 ran against that
document and changed nothing in it.

**No labels, no quality metric, no MRR, no `sup-val`, no public 200, no sealed
holdout were touched.** The fixtures are 32 synthetic query templates and 100
catalog product blobs chosen by `sha256(asin)` rank. Nothing in the harness can
see a target.

## Verdict: **no candidate passes every hard cap. A2 is infeasible.**

| candidate | reps | Top-30 p95 | cold load | RSS Δ | artifact | max file | determinism |
|---|---|---|---|---|---|---|---|
| `ms-marco-TinyBERT-L2-v2` | 7/7 | **69.68 ms** | 0.98 s | 210.8 MB | 5.46 MB | 4.31 MiB | bit-identical |
| `ms-marco-MiniLM-L2-v2` | 7/7 | **387.22 ms** | 1.13 s | 656.9 MB | 16.74 MB | 15.06 MiB | bit-identical |
| `ms-marco-MiniLM-L6-v2` | 7/7 | **1084.52 ms** | 1.52 s | 672.4 MB | 24.15 MB | 22.13 MiB | bit-identical |

| candidate | discarded because |
|---|---|
| `TinyBERT-L2-v2` | **p95 69.7 ms > 25 ms** |
| `MiniLM-L2-v2` | **p95 387.2 ms > 25 ms**; **RSS 657 MB > 400 MB** |
| `MiniLM-L6-v2` | **p95 1084.5 ms > 25 ms**; **RSS 672 MB > 400 MB** |

**21 of 21 repetitions completed. Zero aborts, zero errors, zero failed
downloads.** No cap was relaxed, and none may be.

## What passed, and why it does not help

Everything except latency and memory was comfortable:

* **Artifact size** — 5.46 to 24.15 MB against a 120 MB cap, largest single
  file 22.13 MiB against 95 MiB. The delivery constraint I worried about in
  revision 4 is not close to binding.
* **Cold load** — 0.98 to 1.52 s against a +5.0 s cap, of which the ONNX
  session is only 0.06 to 0.14 s. The rest is interpreter and runtime import.
* **Licenses** — all three Apache-2.0.
* **Determinism** — bit-identical ordering across all 7 processes for every
  candidate, at the pre-registered 1e-6 score tolerance.
* **Offline reload** — verified with `HF_HUB_OFFLINE=1` and
  `TRANSFORMERS_OFFLINE=1` before benchmarking.

**Latency is the binding constraint, and it is not close.** TinyBERT-L2 needs
to be **2.8× faster** to reach the cap; MiniLM-L6 needs **43×**.

## Prediction 4 was wrong

`notes/40` predicted: *"A2's binding constraint will be cold load, not steady
latency."* **That is wrong.** Cold load came in at 0.98–1.52 s against a 5.0 s
allowance — never within 3× of binding — while p95 latency missed by 2.8× on
the best candidate. I reasoned from the catalog's 10.7 s load being the
system's largest fixed cost and assumed a model would add to that budget; the
ONNX session actually loads in 62–144 ms. The cost is per-turn inference, not
startup.

## The frozen shortlist, resolved

All three pinned to immutable revisions before any download:

| model | revision | license |
|---|---|---|
| `cross-encoder/ms-marco-TinyBERT-L2-v2` | `81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc` | apache-2.0 |
| `cross-encoder/ms-marco-MiniLM-L2-v2` | `1b5cd67b15209f24824c50370e0397743aa9b787` | apache-2.0 |
| `cross-encoder/ms-marco-MiniLM-L6-v2` | `233902d25c440f23af6f7d6e94d2946bac0bee0a` | apache-2.0 |

Primary source: `https://huggingface.co/<model-id>`.

**No self-export or self-quantization was needed.** All three publish
`onnx/model_qint8_arm64.onnx` upstream — exactly the dynamic-INT8 ARM64
artifact the design specifies — so the measured artifact is the upstream one at
a pinned revision rather than something this project produced.

The complete offline artifact per candidate is six files:
`onnx/model_qint8_arm64.onnx`, `config.json`, `tokenizer.json`,
`tokenizer_config.json`, `special_tokens_map.json`, `vocab.txt`.

## Three harness corrections, all made before the seven repetitions

Each was found by a smoke run and fixed before any gated measurement. Recorded
because each would have produced a defensible-looking number for the wrong
thing.

**1. `transformers` pulls in torch; the shipped path needs neither.** Measured
on this host:

| imports | RSS | torch loaded |
|---|---|---|
| `numpy + onnxruntime + tokenizers` | **53.2 MB** | **no** |
| `numpy + onnxruntime + transformers` | 346.1 MB | yes |

The harness now uses `tokenizers` directly — which *is* the native tokenizer,
since `transformers` wraps it — through `Tokenizer.encode_batch([(query,
passage)])`. **No literal `" [SEP] "` anywhere**, per the design.

**2. `intra_op_num_threads = 1` was my own arbitrary choice**, annotated "one
request at a time". That conflates request concurrency with intra-op
parallelism: a single request should use the cores it has. On TinyBERT it cost
97.9 ms median against 70.7 ms on ORT's default. **Measured on my setting, the
caps would have been failed by the harness rather than by the model.** All
figures above use ORT's default threading, 10 of 10 cores, recorded per row.

**3. The first draft ran through `optimum` with `return_tensors="pt"`**,
reporting a 530 MB "RSS delta" that was mostly torch's import. Replaced with a
direct `onnxruntime.InferenceSession`, and RSS is now measured against a
model-absent baseline of **53.8 MB**.

**The ordering signature is identical across all three implementations**
(`41a5acc6cda2f562351f40a65dac3ef3`) — optimum+torch, ORT+transformers, and
ORT+tokenizers all produce the same ranking. Only the plumbing changed, which
is what makes these corrections methodology rather than tuning.

## Stability

Per-repetition Top-30 p95, sorted:

| candidate | p95 across 7 repetitions (ms) |
|---|---|
| TinyBERT-L2 | 67.0, 67.8, 68.7, **69.7**, 75.9, 81.8, 89.5 |
| MiniLM-L2 | 244.4, 259.3, 272.5, **387.2**, 398.4, 466.3, 625.6 |
| MiniLM-L6 | 741.2, 818.7, 1021.9, **1084.5**, 1101.3, 1200.8, 1232.4 |

TinyBERT's spread is tight and its **best** repetition (67.0 ms) is still
2.7× the cap. **No repetition of any candidate came within 2.6× of passing**,
so the verdict does not depend on which repetition is taken as
representative.

Batch diagnostics, never gated, median ms: TinyBERT 67.8 / 67.3 / 63.4 at
batch 1 / 8 / 32; MiniLM-L2 162.2 / 229.7 / 222.5; MiniLM-L6 441.0 / 738.2 /
711.2.

## What would change the answer, and is NOT being done

Recorded as information for a future pre-registration, **not** acted on:

* **`max_length = 256`** is generous for product text; 128 would roughly halve
  the compute.
* **`semantic_rerank_k = 30`** is frozen; Top-10 would be ~3× cheaper.
* **A bi-encoder with precomputed embeddings** would move nearly all inference
  offline, at the cost of artifact size and a different quality profile.

**All three are frozen design parameters.** Changing any of them now — after
seeing that the current ones fail — is precisely the move `notes/40` forbids.
A future phase wanting them needs its own pre-registration, written before the
measurement that would motivate it.

## Consequences

* **A2 is infeasible.** It is not built, not promoted to `showcase_model`, and
  no quality experiment runs against it.
* **A1 is unaffected.** It adds no dependency and no artifact, and `notes/40`
  says explicitly that unresolved A2 packaging does not stop A1.
* **`score_default` is unchanged.** No dependency, no model, no artifact enters
  the tree that produces a score. The lexical path remains the only path, which
  is what the fallback requirement always demanded.
* **R1 is not started.** This document ends R0.

## Reproduction

The spike ran in an isolated worktree with its own virtualenv. **The system
Python was never modified** — verified before and after: `numpy` and `torch`
remain absent from it.

`lab/r0/` holds `r0_bench.py`, `r0-requirements.txt` (44 pinned packages),
`artifacts/manifest.json` (revisions, per-file sizes), `r0_results.jsonl` (21
rows) and `r0_verdict.json`. **The model artifacts themselves are not
committed**: A2 is infeasible, so vendoring 46 MB of weights the submission
will never load would be carrying dead payload.
