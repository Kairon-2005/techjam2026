# Phase 7A-R0.1 pre-registration — TinyBERT at Top-10

## Read this first

**This was written AFTER observing A2-30's result**: `TinyBERT-L2` at
`semantic_rerank_k = 30` measured **69.68 ms** Top-30 p95 against a 25 ms cap,
7/7 repetitions, `notes/41`. That is a post-result hypothesis and it is
disclosed as one.

**It does not invalidate or replace R0.** `notes/41`'s verdict stands exactly
as written:

> **A2-30 is infeasible under the frozen 25 ms cap.**

That verdict is not edited, its cap is not relaxed, and its failed rows are
never reused as a pass. **A2-30 does not enter quality evaluation under any
circumstance**, whatever R0.1 finds.

**This is a new feasibility variant, not a corrected R0 result.** R0 asked
whether a semantic reranker over 30 candidates is affordable. The answer was
no. R0.1 asks a **different question** — whether one over **10** candidates is
— and it gets its own measurement, its own rows and its own verdict.

**The motivation is score topology, not the latency number.** HR@10 is already
**0.9950**: 199 of 200 targets reach the Top-10, so the opportunity is
**reordering ranks 2–10**, not reaching deeper. A reranker that only ever sees
the Top-10 is aimed at exactly where the headroom is, and `semantic_rerank_k =
30` was scoring 20 candidates that lie outside the region the metric rewards.
**Top-10 is the better-targeted variant on its own merits, and it happens also
to be cheaper.** Had the ordering been reversed — Top-30 passing, Top-10 not
yet tried — Top-10 would still have been worth registering.

**R0.1 is admissible without quality leakage** because R0 touched no quality
labels, no `sup-val`, no public confirmation and no sealed holdout, and neither
does R0.1. Nothing about the target has been observed, so no hypothesis formed
now can be contaminated by one.

## Frozen configuration

| | |
|---|---|
| model | **`cross-encoder/ms-marco-TinyBERT-L2-v2`** |
| revision | **`81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc`** — the same immutable revision R0 resolved |
| artifact | the existing upstream **`onnx/model_qint8_arm64.onnx`**, unchanged |
| runtime | **ONNX Runtime + `tokenizers` + numpy**. No `transformers`, no torch |
| threading | ORT default intra-op |
| max length | **256** — unchanged |
| pair encoding | the tokenizer's **native pair API**. No literal `" [SEP] "` |
| **`semantic_rerank_k`** | **10** |

**Cascade topology, unchanged in shape from `notes/40`:**

1. **A0 scores the complete candidate population first** — all 100, or all 1000
   under the starvation bypass.
2. **TinyBERT may reorder only A0's Top-10.**
3. **The A0 tail is unchanged**, appended in A0's order.

**No route gating, no fusion, no quality logic in R0.1.** Those are R1's
business, and putting any of them here would make this a quality experiment
wearing a feasibility label.

## Caps — every one unchanged from R0

Not one is relaxed, and none may be relaxed after measurement.

| cap | limit |
|---|---|
| **Top-10 end-to-end p95** | **≤ 25 ms** |
| additional cold load | ≤ 5.0 s |
| RSS delta | ≤ 400 MB |
| total artifact | ≤ 120 MB |
| largest individual file | < 95 MiB |
| offline reload | required |
| determinism | bit-identical ordering; scores equal to 1e-6 |

## Protocol — identical to R0

* the **same frozen fixtures**: 32 synthetic query templates, 100 catalog
  product blobs by `sha256(asin)` rank;
* **7 fresh processes**;
* the same **20 warm-ups**, the same 32 measured queries, the same p95
  statistic, the same model-absent RSS baseline;
* **zero target labels, zero quality metrics**;
* **no MiniLM re-runs** — both were discarded by R0 and stay discarded;
* **no concurrent A1 work**.

### Top-10 latency is MEASURED, never divided

**Dividing R0's number would give `69.68 / 3 ≈ 23.2 ms` — just under the
cap.** That arithmetic would decide the phase, and it is not evidence:

* batching has **fixed per-call cost** that does not scale with the batch, so a
  third of the pairs is not a third of the time;
* padding is to the **longest sequence in the batch**, and a smaller batch has
  a different longest member;
* R0's own diagnostics already show non-linearity — TinyBERT measured 67.8 ms
  at batch 1, 67.3 at batch 8 and 63.4 at batch 32 for the same 30 pairs.

**The difference between a pass and a fail here is smaller than the error of
the estimate.** Top-10 is measured end to end, on 7 fresh processes, or it is
not reported.

## Decision rule, fixed now

**If 7/7 complete and every cap passes:**

* **A2-10 is frozen as the sole semantic candidate for R1.**
* The quality phase has exactly **three** arms: **A0, A1, A2-10**.
* **A2-30 remains a failed feasibility arm and never enters quality
  evaluation.**
* Commit the R0.1 evidence, then write the **R1 quality pre-registration**.
* **Do not implement fusion and do not touch labelled validation.**

**If any cap fails:**

* **Semantic reranking is feature-off / showcase evidence only.**
* **Do not try `max_length = 128`, a different cap, CoreML, another model, or
  Top-5 in this phase.** Each would be a new hypothesis chosen after a second
  failure, and the phase does not get unlimited attempts.
* **Proceed to A1 only.**
* Record that **the frozen CPU latency requirement excluded the neural arm** —
  a real finding about this submission's constraints, not a defeat to be
  engineered around.

## R1 design, to be specified only if A2-10 passes

Before any labelled evaluation, R1 must fix:

* **exact semantic query construction** — which session state becomes the query
  string;
* **exact product text construction** — which catalog fields, in what order,
  truncated how;
* **semantic-only ordering versus fusion with A0**;
* **any fusion weight search on `sup-train` only**, with the same
  cached-feature and split discipline A1 obeys;
* **route eligibility.**

**Recommended robustness boundary**, to be frozen in R1 before `sup-val`:

* **enable** semantic reranking only for `browsing`, or evidence-sparse
  `mixed`;
* **disable** it when there is an override, active negative evidence, an
  abandoned category, or multiple hard constraints;
* **Buying and high-precision traffic stay on A0.**

The reasoning is `showcase_dense`'s: it won on `clean` and `browsing` while
dropping `boundary` MRR from 1.000 to 0.870. `boundary` is at **1.000000**
today and has nothing to gain and everything to lose, and the slices with room
are `browsing` (0.809) and `buying` (0.851). **This policy must be frozen
before `sup-val` and must not be chosen after seeing a quality result.**

## Stop conditions

Stop if: any cap is relaxed; A2-30's rows are reused; a second variant is
proposed after R0.1 fails; any label, `sup-val`, public sample or sealed
holdout is read; A1 work begins in the same run; or Top-10 latency is inferred
rather than measured.
