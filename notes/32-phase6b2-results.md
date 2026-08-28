# Phase 6B2 results — clarification decision extraction

Measurement commit `7e66115`, comparator `baf7c05`. All rows leased, isolated,
`matrix_complete`, `citable()`.

## Verdict: **NOT ADOPTED.** `question_context_mode` stays `"off"`.

Correctness is complete and clean. The performance evidence points one way and
points hard, and the pre-registered stop condition applies: no adoption, no
tuning, stop for review.

| gate | result | evidence status |
|---|---|---|
| A — pure-function correctness | **PASS** | citable |
| B — shadow agreement | **PASS**, 8,483 turns, zero disagreements | citable |
| C — behaviour preservation | **PASS**, bit-exact | citable |
| D — performance | **FAIL** on the measurements taken | **diagnostic only — see Correction 1** |
| hard gate — no lazy-index construction | **PASS** | citable |

## Correction 1 — gate D is diagnostic, gates A–C are citable

*Recorded 2026-08-28, after the fact, correcting how this document's own
evidence was presented. No measurement is changed or removed.*

**The A–C rows are citable.** They were produced by `lab/record.py` under a
`lab/lease.py` lease, are `matrix_complete`, pass `lab.provenance.citable()`,
and are in `lab/results.jsonl` under tags `p6b2b-shadow`, `p6b2-official`,
`p6b2-supplementary` and `p6b2-robustness`.

**The gate-D benchmark is not.** It fails this project's own reproducibility
standard on three counts, each independently disqualifying:

1. **Only 4 of 7 pre-registered repetitions completed.** The pre-registration
   said "≥ 7 paired repetitions"; four is not seven, and a median over four is
   not the statistic that was registered.
2. **The benchmark harness and its exact invocation are not committed.** There
   is no benchmark runner in the tree. The numbers below came from an ad-hoc
   script that no longer exists, so no one — including me — can re-run the
   measurement that produced them. This is precisely the failure
   `lab/record.py`'s docstring was written about: Phase 1's uncooperative
   figure came from an unlogged ad-hoc script and disagreed with the committed
   harness by 0.0074, invisibly, for days.
3. **The repetitions are not in an append-only benchmark ledger.** They have no
   row key, no lease, no input fingerprints, and nothing can be invalidated
   later because there is nothing to invalidate. They exist only as prose in
   this file.

**Therefore the four rows in §D may be quoted only as diagnostic
measurements — never as a passed or failed gate under the project's
reproducibility standard, and never in an external write-up as a benchmark
result.**

## Correction 2 — what the four measurements do and do not establish

**They do establish the direction and the magnitude of the problem.** Four
independent repetitions on alternating arm order agreed to within 0.0045 of
ratio (1.3969–1.4014), the overhead exceeded its own gate by 25×, and the cause
was counted from source rather than inferred from the clock: eager
`CandidateStats` performs 16 bounded window passes per dispatch where the
legacy path performs at most 12. A scan-count argument does not need a citable
benchmark to be sound, and it is the scan count — not the wall clock — that
carries the conclusion.

**They do not establish that the benchmark itself is sound.** The conservative
statement, and the only one this document supports, is:

> The current implementation stays **OFF**. Eager total-statistics
> construction is a real and large cost, established by source-level scan
> counting and corroborated by four diagnostic timings. The benchmark that
> produced those timings does not meet the project's reproducibility standard
> and is not offered as evidence that any performance gate was formally
> evaluated.

Nothing here promotes 6B2 or changes its gate. **The verdict remains NOT
ADOPTED**, for the same reason and with the same default.

## A — pure-function correctness

A **4,320-cell** equivalence grid crosses five ask policies, four asked
histories, three levels each of `dry_streak` / `uncertain_streak` /
`dry_others`, `question_utility` on and off, `overgeneral_cats` ∈ {0,2,6},
`pool_depth` ∈ {0,1,2,30} and `ask_fallback_after` ∈ {0,1}. Every cell compares
attribute, write set and resulting state. Plus empty and one-item pools,
exhausted `PROBE_ORDER` and `PROBE_ORDER[:-1]`, and every-easy-facet-asked.

**The grid found a real difference, in write ORDER.** Legacy writes
`last_coverage` and `last_weighed` inside `_pool_attribute` and `last_bits`
after it returns; the patch has a fixed field order. The four keys are
independent and each is written at most once, so order cannot affect the
result — the oracle therefore compares set membership and final values, and
**separately asserts no key is written twice**, which is what makes order
irrelevant rather than merely assumed so.

**Negative controls.** Three tests force a disagreement and require the
comparator to see it. The patch control deliberately uses a **no-write**
branch, because a branch that already writes all four would make "tidying" a
no-op and prove nothing. Zero disagreements is only evidence if the comparator
can produce one.

## B — shadow agreement, the only pre-adoption independent evidence

| scenario | turns | compared | attribute | state | message |
|---|---|---|---|---|---|
| clean | 411 | 411 | 0 | 0 | 0 |
| vague_start | 503 | 503 | 0 | 0 | 0 |
| uncooperative | 560 | 560 | 0 | 0 | 0 |
| override_genuine | 421 | 421 | 0 | 0 | 0 |
| override_category | 413 | 413 | 0 | 0 | 0 |
| contradiction | 632 | 632 | 0 | 0 | 0 |
| supplementary_dev | 5,544 | 5,544 | 0 | 0 | 0 |
| **total** | **8,483** | **8,483** | **0** | **0** | **0** |

### The design's central case occurs in the wild

Selection/render pairs across all scenarios:

| pair | count |
|---|---|
| `first_two_other/open` | 10,754 |
| `pool_selection/structured` | 5,373 |
| `give_up/open` | 1,130 |
| `pool_selection/open` | 877 |
| **`easier/structured`** | **249** |
| `easier/open` | 214 |

**`easier/structured` fires 249 times.** That is the counterexample the design
was built around — selection takes the easier branch while `_compose` renders
the structured message from options the same call wrote a few lines earlier.
Revision 1's single `mode` field could not have represented those 249 turns.

## C — behaviour preservation

Bit-exact on all seven scenarios, all four official slices, and the compat
anchor `0.928708`. A/B/C share one agent blob, scenario hash and catalog hash.

## D — performance: FAIL on the measurements taken — **diagnostic, not citable**

*Read this section only with Correction 1 above. The harness that produced
these four rows is not committed, the run is 4/7, and none of it is in a
ledger. The numbers are reported because suppressing them would be worse;
they are not a gate evaluation.*

Frozen protocol: 1,000 warm-ups, 10,000 measured complete dispatches per arm
per repetition, alternating legacy-first / pure-first.

| rep | legacy | pure | ratio | delta |
|---|---|---|---|---|
| 1 | 6.2245 ms | 8.7071 ms | 1.3988 | +2.4826 |
| 2 | 6.2192 ms | 8.7154 ms | 1.4014 | +2.4962 |
| 3 | 6.2258 ms | 8.6968 ms | 1.3969 | +2.4710 |
| 4 | 6.1968 ms | 8.6561 ms | 1.3969 | +2.4593 |
| **median** | | | **1.3979** | **+2.4768 ms** |

| pre-registered gate | required | observed | |
|---|---|---|---|
| median ratio | ≤ 1.20 | 1.3979 | exceeded |
| absolute median overhead | ≤ 0.10 ms | +2.4768 ms | exceeded |

**The two gates above are also mutually incoherent** and must not be reused
unchanged. Against a ~6.2 ms baseline, ratio ≤ 1.20 permits +1.24 ms while the
absolute gate permits +0.10 ms — a 12× disagreement about what "acceptable"
means. An implementation could satisfy either and fail the other. This was not
noticed when the pair was registered; it is fixed in the 6B2-R2
pre-registration with branch-specific gates.

**Only 4 of the 7 pre-registered repetitions completed.** Reps 5–7 were
launched as a second batch, produced no output, and were killed after 4 h 11 m
for a job that should take ~7 minutes.

**The cause of that stall is unknown and is not claimed here.** No diagnostic
was captured while the process was alive — no PID state, no CPU%, no CPU time,
no RSS — so there is nothing to attribute it to. "The harness looked correct"
is not evidence of a cause; it is an absence of one. The stall is recorded as
an unexplained incomplete run. 6B2-R2's harness captures exactly those four
process facts at 2× expected duration and aborts at 15 minutes, so that if it
recurs there will be something to reason from.

**It does not change the verdict**, which was already NOT ADOPTED and rests on
the scan count below rather than on the clock. Ratio variance across the four
completed reps is 0.0045 (1.3969–1.4014); the direction and magnitude are not
in doubt. That is a statement about what four diagnostic timings show, not a
claim that the missing three would have been unremarkable.

### Cause, counted from source

Counted per dispatch over the bounded window, five facets in `ATTR_VOCAB`,
none already asked. `_overgeneral` reads `cat.cats`; every other pass reads
`cat.text` and costs ~1.1 ms at `pool_depth=30`.

| arm | `cat.cats` | `cat.text` passes | total |
|---|---|---|---|
| legacy, `question_utility=False` (entropy ≤5 + winner coverage 1) | 1 | **6** | 7 |
| legacy, `question_utility=True` — **the live config** (entropy 5 + coverage 5 + winner coverage 1) | 1 | **11** | 12 |
| pure eager (entropy 2/attribute + coverage 1/attribute, always) | 1 | **15** | 16 |

The `≤ 7` figure quoted in the first draft of this section is the
`question_utility=False` arm; under the shipped default (`True`) legacy does
11 text passes, not 6. **The correct live comparison is 15 against 11,
or 1.36× the text-scan work — which is what the observed 1.40× wall-clock
reflects.** The eager arm is also flat: it does 15 passes whether five facets
are unasked or one, so on a turn with three facets already asked legacy does 5
text passes and eager still does 15.

**`CandidateStats` is a complete precomputation; legacy is lazy.** Legacy skips
already-asked attributes, computes only the entropy variant its config needs,
and takes coverage for the winner alone.

This is a **design consequence, not an implementation slip**: a frozen,
fully-populated statistics object was chosen precisely so the decision function
could be pure and total. That choice has a measured price.

## Hard gate — no lazy-index construction

`FacetIndex` and `DenseIndex` remain unbuilt in all three modes.
`CategoryIndex` is built by **retrieval** under `deep_funnel` in every mode
including `off`, so the question path adds nothing.

## Decision

Per the pre-registered stop condition — *stop immediately on performance-gate
failure* — there is **no adoption commit**. Default stays `"off"`. The legacy
controller remains the single live implementation, and the pure controller
ships behind the flag with its correctness evidence intact.

**No tuning was attempted.** Making `CandidateStats` lazy, or computing only
the entropy variant the config needs, would plainly narrow the gap — but that
is a design change, it would invalidate the 4,320-cell grid and the 8,483-turn
agreement evidence gathered against the current shape, and the stop condition
exists precisely to prevent optimising after seeing the number.

**This result is not superseded by what follows.** Phase 6B2-R2 is a
separately pre-registered attempt at a different design — staged construction
rather than a lazy `CandidateStats` — with its own gates, its own harness and
its own re-run of A, B and C. It inherits none of this phase's evidence. If
R2 succeeds, 6B2 as recorded here still failed; if R2 fails, 6B2 control is
abandoned for this submission. See `notes/33-phase6b2-r2-prereg.md`.

## What survives for a future attempt

The correctness apparatus is reusable as-is: the grid, the write-tracking
oracle, the negative controls, and the shadow comparator. A future revision
would need a new pre-registration for a lazy statistics contract, and would
have to re-run gates A–C against it rather than inheriting these results.
