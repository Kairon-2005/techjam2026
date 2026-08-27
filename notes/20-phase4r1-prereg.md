# Phase 4R1 pre-registration — genuine dual-track dense retrieval

Written before the dense route is implemented. Recorded at `b334e6c`, 177
tests passing, default = Phase 2 arm C retrieval + Phase 3 question utility.

## Objective

Make Browsing and Buying use genuinely different candidate-generation paths,
without losing the Phase 3 default's score, starvation recovery, CPU
feasibility or reproducibility. **This is the first phase permitted to claim
the Pillar I data-plane differentiation**, and it may only claim it on
candidate-topology evidence, never on a route label.

## Arms

| arm | Buying | Browsing | Mixed |
|---|---|---|---|
| **A** | Phase 3 lexical default | lexical default | lexical default |
| **B** | lexical default | **dense-only** candidate source | explicit hybrid |
| **C** | lexical default | lexical + dense **RRF** | explicit hybrid RRF |

A is the frozen Phase 3 baseline. Buying stays lexical in every arm — no dense
injection by default, in any of them.

## Parameters, fixed now

| parameter | value |
|---|---|
| representation | Reflective Random Indexing → sign signatures |
| `dense_dim` | **32** |
| `dense_seed` | **20260827** |
| index vector non-zeros | 6 |
| terms per document | 14, highest-idf first |
| `dense_depth` (browsing) | **300** |
| lexical depth (browsing) | 1200, unchanged |
| fusion | **Weighted RRF**, `k = 60` |
| `rrf_weight_lexical` | **1.0** |
| `rrf_weight_dense` | **1.0** |
| funnel | unchanged: Top-100, quotas .70/.20/.10 |
| Category plane | **OFF** — unchanged, and not reopened here |
| route transition | unchanged `_retarget()`; Mixed → Buying on any usable slot, Mixed → Browsing on a category with no slot |

No parameter search. One implementation, one B/C matrix.

## Artefact identity

The dense artefact is built in-process from the frozen catalog and the fixed
seed, so it is identified by `(catalog_sha256, dense_dim, dense_seed,
builder_version)`. That tuple is recorded on every row. **No file is
downloaded and nothing is fetched at scoring time.**

## Predictions

1. **Topology will differ decisively.** R0 measured BM25↔dense Top-100 overlap
   at mean 0.020, so Browsing will carry material dense-only candidates while
   Buying carries none. This is close to a restatement of the code and is the
   Pillar I evidence.
2. **B will lose score.** Dense-only browsing discards the lexical route that
   currently earns browsing MRR 0.809. R0 found the target only-by-dense on
   1 query in 60.
3. **C will be roughly score-neutral**, because RRF keeps the lexical ranking
   and dense contributes tail candidates the funnel mostly declines to
   promote.
4. Least confident: whether C improves browsing MRR at all. R0's evidence says
   the dense neighbours are different but rarely contain the target.

**The honest expected outcome is that Phase 4 closes the Pillar I architecture
gap without a score gain.** If that happens it is reported as such, not dressed
up.

## Acceptance gates, against frozen Phase 3 C

Official: score drop ≤ `0.002` · HR@10 ≤ `0.005` · MRR ≤ `0.003` · Buying MRR
≤ `0.005` · Browsing MRR ≤ `0.005` (improvement preferred) · Boundary and
Intent Override HR@10 ≤ `0.01`.

Robustness: `vague_start` and `uncooperative` ≤ `0.010` · override and
contradiction guards ≤ `0.005`.

Supplementary veto: overall score drop > `0.01` reject · HR@10 drop > `0.01`
reject · any slice score drop > `0.02` reject.

Feasibility: resident index + model memory < `160 MB` · end-to-end query p95
< `100 ms` · no network at scoring time · no unbounded session or cache state ·
full suite passes · `ask_policy="other"` anchor reproducible.

Architecture — **all four required, and they gate closure independently of
score**: Browsing shows non-zero material dense-only contribution · Buying
remains predominantly lexical/constraint-derived · Mixed has a recorded
transition and fusion explanation · **if candidate topology does not differ,
Phase 4 does not close even if the score rises.**

## Stop rule

One feasibility choice (made in R0), one B/C matrix. If the dense route fails
score or robustness gates it stays feature-off and is reported honestly. No
model cycling, no weight search. `supplementary_holdout` is not run. No
reranking, personalization or Phase 5 before Phase 4 has a recorded decision.

## Evidence recorded per arm

Official overall and four slices · existing robustness scenarios ·
supplementary dev overall and four slices · per-route candidate counts ·
dense-only / lexical-only / overlapping counts · Recall@pool by source and
route · RRF contribution to the final Top-10 · route transition counts · index
build time, resident memory, query p50/p95 · artefact identity and offline
status.

Seeds `(7,8,9,10,11)`. Sealed supplementary holdout stays sealed.
