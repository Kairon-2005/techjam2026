# Phase 6B2 results — clarification decision extraction

Measurement commit `7e66115`, comparator `baf7c05`. All rows leased, isolated,
`matrix_complete`, `citable()`.

## Verdict: **NOT ADOPTED.** `question_context_mode` stays `"off"`.

Correctness is complete and clean. **The performance gate fails decisively**,
and the pre-registered stop condition applies: no adoption, no tuning, stop
for review.

| gate | result |
|---|---|
| A — pure-function correctness | **PASS** |
| B — shadow agreement | **PASS**, 8,483 turns, zero disagreements |
| C — behaviour preservation | **PASS**, bit-exact |
| D — performance | **FAIL**, ratio 1.398 against ≤1.20 |
| hard gate — no lazy-index construction | **PASS** |

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

## D — performance: FAIL

Frozen protocol: 1,000 warm-ups, 10,000 measured complete dispatches per arm
per repetition, alternating legacy-first / pure-first.

| rep | legacy | pure | ratio | delta |
|---|---|---|---|---|
| 1 | 6.2245 ms | 8.7071 ms | 1.3988 | +2.4826 |
| 2 | 6.2192 ms | 8.7154 ms | 1.4014 | +2.4962 |
| 3 | 6.2258 ms | 8.6968 ms | 1.3969 | +2.4710 |
| 4 | 6.1968 ms | 8.6561 ms | 1.3969 | +2.4593 |
| **median** | | | **1.3979** | **+2.4768 ms** |

| gate | required | actual | |
|---|---|---|---|
| median ratio | ≤ 1.20 | **1.3979** | **FAIL** |
| absolute median overhead | ≤ 0.10 ms | **+2.4768 ms** | **FAIL** |

**Only 4 of the 7 pre-registered repetitions completed.** Reps 5–7 were
launched as a second batch, produced no output, and were killed after 4 h 11 m
for a job that should take ~7 minutes. I could not attribute that to a specific
cause and am not claiming one; it is recorded as an incomplete run rather than
quietly dropped.

**It does not change the verdict.** Ratio variance across the four completed
reps is **0.0045** (1.3969–1.4014) against a gate margin of 0.20, and the
overhead exceeds its gate by 25×. Three more repetitions of a measurement that
stable cannot move 1.398 to 1.20.

### Cause, counted from source

| | window passes per dispatch |
|---|---|
| legacy: `_overgeneral` 1 + entropy ≤5 + coverage for the **winner only** 1 | **≤ 7** |
| pure: `_overgeneral` 1 + entropy **2 per attribute, always** 10 + coverage **per attribute, always** 5 | **16** |

**`CandidateStats` is a complete precomputation; legacy is lazy.** Legacy skips
already-asked attributes, computes only the entropy variant its config needs,
and takes coverage for the winner alone. 16/7 = 2.29× the scan work, observed
as 1.40× wall-clock.

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

## What survives for a future attempt

The correctness apparatus is reusable as-is: the grid, the write-tracking
oracle, the negative controls, and the shadow comparator. A future revision
would need a new pre-registration for a lazy statistics contract, and would
have to re-run gates A–C against it rather than inheriting these results.
