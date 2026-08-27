# Phase 2B-R2 pre-registration

Written **before** the R2 matrix runs. Recorded at `f135ea3`, 148 tests
passing, `dual_plane` still default-off.

## What changed since R1, and why

R1 failed non-inferiority by −0.0449. Two specification errors were diagnosed,
and R2 fixes both rather than tuning around them.

| R1 defect | R2 fix |
|---|---|
| category intersected unconditionally; target outside the shelves on 5.5% of sessions | cross-branch shelf union (target inside 94.5% → **100.0%**); hard narrowing only when the reading is unambiguous, otherwise a source + ranking weight |
| up to 1274 candidates handed to a ranker whose operating point is 100 | deterministic funnel to **Top-100** on source quotas (primary .70 / expansion .20 / rescue .10) |
| rescue lane unbounded, adding hundreds of candidates for free | rescue kept unconditional but **inside** a funnel quota |

## Withdrawn claim, carried forward

The R1 `negative_preference` gain of +0.0889 is a **whole-pipeline** effect.
It is not attributed to the negative channel and `filter_negative` is not
promoted. R2 does not implement a hard-negative filter.

## The minimal 2×2, and nothing else

One ablation, four cells, on `clean`, to confirm the interaction between the
two defects. **The parameter grid is not extended beyond this.**

| | category **off** | category **corrected** |
|---|---|---|
| depth **shallow** (100) | A | B |
| depth **deep** (1200) | C | D |

`category off` = `category_hard_max_shelves=0`, `funnel_quota_expansion=0`,
neighbour and mixed category budgets 0 — no constraint and no source.
`corrected` = the R2 default. Depth sets all three route depths.

**Predictions, in order of confidence.**

1. **A ≈ baseline (`0.928508`).** Shallow depth with no category is close to
   the legacy path; the funnel is a no-op when the source already fits.
2. **C > R1's `0.883565`.** Deep retrieval without the category constraint,
   but funnelled to 100. R1's uncapped version of roughly this scored
   `0.903699` in an unleased diagnostic; the funnel should recover more.
3. **D is the candidate default and the one I am least sure of.** If the two
   fixes are independent, D ≈ A. If depth still hurts after funnelling, D < B.
4. **B ≥ A.** The corrected category as a source at shallow depth should be
   neutral-to-positive.

**What would falsify the R2 thesis:** D materially below A. That would mean
funnelling does not neutralise depth, and the honest conclusion would be that
deep retrieval cannot pay with this ranker — closing Phase 2 default-OFF
rather than iterating a third time.

## Route topology must survive the funnel

The funnel caps every route at 100 candidates, so R1's pool-size contrast
(462 / 1274 / 909) no longer distinguishes the routes by construction.
Divergence must therefore be demonstrated on what the funnel does *not*
equalise: **candidate origin** (which sources filled the 100, in what
proportion) and **shelf spread** (categories and entropy within the 100).
If those collapse too, the routes are labels again and Phase 2 does not close.

## Acceptance — unchanged from R1

| gate | threshold |
|---|---|
| compat `ask_policy="other"` | bit-exact `0.928708` |
| `dual_plane` off | `0.928508` |
| clean with Phase 2B on | ≥ `0.926508` |
| clean HR@10 | ≥ `0.990` |
| clean MTTC | ≤ `2.09` |
| index memory | < 100 MB (51.7 MB measured) |
| added p95 retrieval | < 10 ms |

**Default ON and Phase 2 closed** requires every gate above AND at least one
gain on an **official** slice — the evaluator's own `buying`, `browsing`,
`intent_override`, `boundary` segmentation, now carried on every row. A gain
on a synthetic capability scenario does not qualify and will not be offered as
one.

## Matrix

Official aggregate and the four official slices; the 2×2; forced-route origin
and topology; and the existing regression guards (`override_category`,
`override_genuine`, `vague_start`, `uncooperative`, `contradiction`,
`negative_preference`).

Selection seeds `(7,8,9,10,11)`. Robustness seeds `(12…16)` remain unspent.
No consumed holdout, no negation v2.
