# Phase 7A design — reranker feasibility, and what it is allowed to prove

**Design and pre-registration only. No implementation, no weight changes, no
experiments.** Written before any Phase 7 code exists.

Phase 6 is closed (`notes/39`). This asks one question: **can a better reranker
move the exact purchased item further up the list than the current heuristic
does — and can it be shown without fooling ourselves on 200 samples we have
already used for everything else?**

## The objective is MRR on the exact purchased item

Not "demonstrate a reranker". The metric that pays is the rank of the **one
correct `parent_asin`**, and the baseline says exactly where the room is.

Current heuristic reranker, `clean`, citable (`p6c1-arm-a`, mode `off`):

| | value | headroom |
|---|---|---|
| **HR@10** | **0.9950** | **≈ none** — 199 of 200 targets already reach Top-10 |
| **MRR** | **0.8526** | **≈ 0.142** against a 0.995 ceiling |
| MTTC | 2.060 | not a 7A target |
| score | 0.932067 | |

| official slice | HR@10 | MRR |
|---|---|---|
| `boundary` | 1.0000 | **1.000000** — nothing to win |
| `intent_override` | 1.0000 | 0.922222 |
| `buying` | 0.9875 | 0.851181 |
| **`browsing`** | 1.0000 | **0.809375** — the weakest, and the largest single opportunity |

**So 7A is a reordering problem, not a retrieval problem.** Any arm whose
mechanism is "find more candidates" is aimed at the 0.5% that HR@10 is still
missing. The work is in positions 2–10 of lists that already contain the answer,
and `browsing` is where the most of it sits.

This also sets the honest ceiling: an arm that perfected every ranking would
gain ≈ 0.142 MRR and correspondingly less composite score. **Any arm reporting
a larger gain has a bug, not a breakthrough.**

## Gate 0 — runtime availability, before anything else

Measured on this host, 2026-08-29:

| runtime | status |
|---|---|
| `sentence_transformers` | **absent** |
| `transformers` | **absent** |
| `torch` | **absent** |
| `onnxruntime` | **absent** |
| `sklearn` | **absent** |
| `scipy` | **absent** |
| **`numpy`** | **absent** |

**There is no ML runtime, and no numeric stack at all.** The project is stdlib
only, deliberately — `_DenseIndex`'s docstring records the standard it was
built to: *"Everything is stdlib, deterministic from a fixed seed, and nothing
is fetched at any point."*

**Gate 0 must therefore be settled before any arm is built**, and it is a
question about the submission environment rather than about modelling:

1. May a third-party dependency be added at all?
2. If yes, may model weights be **vendored** into the repository, and within
   what size budget? Nothing may be fetched at judging time.
3. What is the judging host's CPU, RAM and per-turn latency budget?

**If any of these is "no" or "unknown", the CPU-local semantic arm is
infeasible and 7A says so and stops** — rather than building it, measuring it,
and discovering at submission that it cannot ship. That outcome is a legitimate
result of this phase, not a failure of it.

## The three arms, and they are fixed here

Exactly three, defined completely before any measurement. **No fourth arm may
be added after seeing a number.**

### A0 — the current heuristic (control)

`w_bm25`, `w_cat`, `w_exact`, `w_field`, `w_idf`, `w_neg`, `w_phrase`,
`w_pop`, `w_soft*` as shipped. Not tuned in 7A. It is the thing to beat and the
thing every other arm is paired against.

### A1 — lightweight lexical / feature reranker, stdlib only

Features already computable from the existing indexes: exact-token overlap,
field-weighted match position, IDF-weighted phrase coverage, category
agreement, popularity prior, evidence-slot satisfaction, and the negative-
constraint penalty. Combined by a **fixed, hand-specified linear form whose
weights are set on `supplementary_dev` only** (see below).

Feasible today: no new dependency, no artifact, cold start unchanged.

### A2 — CPU-local semantic reranker

A small cross-encoder or bi-encoder scoring (query, product) pairs, run on CPU,
with weights vendored offline. **Gated entirely on Gate 0.** If Gate 0 fails,
A2 is not built and the phase records why.

`_DenseIndex` already exists and is **not** A2: it is a stdlib random-indexing
*candidate source* with a measured BM25 overlap of 0.020, and it is a
recall-side mechanism aimed at the part of the problem that has no headroom.

## `score_default` and `showcase_model` are separate, and stay separate

The repository already distinguishes these (`starter/agent.py:PROFILES`), and
`showcase_dense` is the precedent: it improves `clean` and `browsing` MRR,
drops `boundary` MRR from 1.000 to 0.870, fails the contradiction guard, and is
therefore *"never the robust default"*.

| profile | meaning | may claim |
|---|---|---|
| **`score_default`** | the submission configuration | the score, and only this one may |
| **`showcase_model`** | architecture demonstration | capability, never the score |

**An arm that needs a dependency, an artifact, or a latency budget the
submission cannot guarantee goes to `showcase_model` and its number is never
reported as the submission's score.** Deciding that after seeing which arm won
is exactly the failure this table exists to prevent, so the rule is fixed now:
**`score_default` admits an arm only if it passes Gate 0 with no unresolved
question, and passes every gate below.**

## Feasibility measurements, before any quality claim

For each arm that clears Gate 0, measured through the committed harnesses
(`lab/benchmark.py` for latency and RSS, `lab/shards.py` for scenarios):

| | requirement |
|---|---|
| **cold start** | time from process start to first response, reported separately from steady-state. `Agent()` already costs ~10.7 s on the 58 MB catalog; an arm that adds materially to that must say so. |
| **per-turn latency** | seven fresh-process paired repetitions, R2.1 gate rules. **Phase 6C1's lesson applies: a bound on the NUMBER of operations is not a bound on cost** — the profile decision was 240 bounded checks and cost 1.475 ms because each scanned ~1.1 kB. Per-candidate cost × candidates is the quantity, and it is measured, not asserted. |
| **peak RSS** | from the harness's recorded `peak_rss_bytes`, against the 452.9 MB current median. |
| **artifact path** | any model weights vendored in-repo, loaded from disk, **nothing fetched at any point**, and the load cost counted in cold start. |
| **determinism** | identical output for identical input across processes, as every other decision in this project must be. |

## The overfitting prohibition, and how selection is separated

**The public 200 has been used in every phase from 1 to 6C1.** Selecting a
model or a hyperparameter by searching over it and then reporting the same 200
as validation would produce a number that means nothing, and it would look
exactly like a good result.

Pre-registered, and binding:

* **Selection happens on `supplementary_dev` (1,000 synthetic sessions) only.**
  Weights, thresholds, feature sets, model choice — all fixed there.
* **The public 200 is a CONFIRMATION set.** Each arm is evaluated on it
  **once**, after its definition is frozen and committed.
* **No arm may be modified after seeing its public number.** A modified arm is
  a new arm, needs a new pre-registration, and the original result stands.
* **The number of arms is fixed at three**, here, before any measurement.
  Reporting the best of many arms searched on the confirmation set is the same
  error wearing a different hat.
* **The sealed holdout is not run.** Not in 7A, not for selection, not for
  confirmation.
* Every reported figure is **paired** — same seeds, same scenarios, A0 against
  the arm — and reported with its **paired-delta SD**, which is never used as a
  loss budget (the R2.1 correction).

**If an arm's `supplementary_dev` gain does not survive on the public 200, that
is the honest outcome and it is reported as such**, not re-tuned.

## Every experiment reports the full set

No arm may be judged on `clean` alone. Every measurement reports, in one table:

* `clean` composite, HR@10, MRR, MTTC;
* **all four official slices** — `boundary`, `browsing`, `buying`,
  `intent_override` — each separately, never averaged;
* **robustness**: `vague_start`, `uncooperative`, `override_genuine`,
  `override_category`, `contradiction`;
* **`supplementary_dev`**, as a veto signal and never as a score.

`showcase_dense` is the reason this is mandatory: it wins on `clean` and
`browsing` while destroying `boundary`. A `clean`-only report would have shipped
it.

## Adoption gates for `score_default` (pre-registered now)

| gate | requirement |
|---|---|
| **MRR benefit** | `clean` MRR improves by **≥ +0.010** — about 7% of the available 0.142 headroom — on the confirmation set, paired against A0 |
| **no slice regression** | **no official slice's MRR may decrease at all.** `boundary` is at 1.000000 and any movement there is a loss |
| **composite floor** | `clean` composite **must not decrease** from 0.932067 |
| **robustness** | no stochastic scenario's mean paired Δscore below **−0.005**, and paired HR@10 drop **≤ 0.01** |
| **supplementary veto** | `supplementary_dev` must not regress |
| **latency** | per-turn overhead **≤ 0.50 ms** median, seven citable repetitions; four of seven is not a result |
| **memory** | peak RSS increase **≤ 50 MB** |
| **Gate 0** | passed with no unresolved question |

Any one failing keeps `score_default` unchanged. There is no weighted trade
among them.

## Stop conditions

Stop if: Gate 0 cannot be answered; any arm is selected or tuned on the public
200; a fourth arm is proposed after a measurement; the sealed holdout is
touched; an artifact is fetched rather than vendored; or an arm's gain is
reported without its four slices, robustness and supplementary.

## Predictions

1. **Gate 0 fails for A2 on this host as it stands.** There is no numeric stack
   at all, so A2 requires both a dependency decision and a vendoring decision,
   and neither is mine to make.
2. **A1's headroom is small.** The heuristic has been tuned across six phases;
   `boundary` MRR is already 1.000 and `intent_override` 0.922, so nearly all
   available gain sits in `browsing` (0.809) and `buying` (0.851).
3. **The binding constraint will be slice regression, not mean gain.** Any
   reranker aggressive enough to move `browsing` will move `boundary`, which
   has nothing to gain and everything to lose.
4. **If an arm reports more than +0.142 MRR on `clean`, it is a bug.** That is
   the whole distance to the HR@10 ceiling.
