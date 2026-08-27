# Phase 2B-R2 results

Six leased shards, all `matrix_complete`, all isolated at **`40d0c1f`**,
rendered through `citable()`. Predictions in
[notes/12-phase2b-r2-prereg.md](12-phase2b-r2-prereg.md) were written first.

## Verdict: **default OFF. Phase 2 does not close.**

Every pre-registered gate passes — three of them by hair-thin margins — and
official gains exist. It still should not ship, because R2 introduces a
**−0.158 regression on `uncooperative`** caused by a defect, not a trade-off.

| gate | required | actual | |
|---|---|---|---|
| compat | `0.928708` | `0.928708` | pass |
| off reproduces baseline | `0.928508` | `0.928508` | pass |
| clean on | ≥ `0.926508` | `0.926881` | pass by **0.00037** |
| clean HR@10 | ≥ `0.990` | `0.990` | pass **exactly** |
| clean MTTC | ≤ `2.09` | `2.09` | pass **exactly** |
| index memory | < 100 MB | 51.7 MB | pass |
| added p95 | < 10 ms | +6.7 ms | pass |

Three gates landing exactly on their thresholds is not a comfortable margin;
it is a coin toss dressed as a pass.

## The 2×2 — one prediction wrong, and it matters

| | category off | category corrected |
|---|---|---|
| **shallow** (100) | A `0.924983` | B `0.903003` |
| **deep** (1200) | **C `0.931967`** | D `0.926881` |

Predicted A ≈ baseline (0.924983, close); C > R1's 0.883565 (0.931967, and it
**beats the baseline by +0.0035**); D ≈ A (0.926881, close). **Prediction 4
was wrong**: B ≥ A was predicted, B is 0.022 *below* A.

The interaction is not the one the R1 diagnosis implied. Depth helps at both
category settings (C > A, D > B). The category component **costs at both
depths** (A > B by 0.022, C > D by 0.005). The R1 story — "depth was half the
problem, the category constraint the other half" — was half right: fixing the
category from a hard intersection to a mostly-source still leaves it a net
cost, and the funnel converted depth from a liability into a **gain**.

The best cell is C: **deep retrieval, funnelled, with the category component
switched off entirely.** That is also the cell with the least route
differentiation, which is the whole tension of this phase.

## Official slices — real gains, and one real regression

| slice | off HR / MRR | R2 on HR / MRR | |
|---|---|---|---|
| buying | 0.988 / 0.852 | 0.988 / 0.849 | −0.003 MRR |
| **browsing** | 1.000 / 0.782 | 1.000 / **0.796** | **+0.014 MRR** |
| **intent_override** | **1.000** / 0.922 | **0.967** / 0.917 | **−0.033 HR** |
| **boundary** | 1.000 / 0.950 | 1.000 / **1.000** | **+0.050 MRR** |

Two official gains, on exactly the slices the browsing plane and the funnel
were meant to help. One official regression: `intent_override` loses a target
outright on one session in thirty.

## The defect that decides it

`uncooperative` falls **0.833266 → 0.675231**, HR@10 0.923 → 0.724, MTTC
2.80 → 4.43. The telemetry names the cause in one line:

| | pool size |
|---|---|
| `uncooperative`, Phase 2B off | **400.4** |
| `uncooperative`, R2 on | **100.0** |

**The funnel silently voids starvation-aware widening.** When the customer
stops cooperating, `_starved()` widens retrieval to `starved_candidates=1000`
precisely because the query is too thin to rank on. The funnel then discards
90% of it against a fixed `funnel_top=100`, because it is a constant that
never learned about the starvation signal. `vague_start` shows the same shape
(0.917534 → 0.884045; pool 106.9 → 82.0, *below* the cap, because thin sources
cannot even fill their quotas).

This is the interaction I flagged at the end of R1 — "pool-entropy question
selection, starvation widening and dry-streak recovery all assume a ~100
candidate pool" — arriving from the opposite direction: the funnel does not
break because the pool got bigger, it breaks because it refuses to let the
pool get bigger when a measured capability needs it to.

It is a defect with an obvious shape, not a trade-off. It is **not** fixed
here: a starvation-aware funnel is a new hypothesis and needs its own
pre-registration.

## Route topology inside the funnel

| route | pool | categories | entropy | Recall@pool | exclusion |
|---|---|---|---|---|---|
| browsing | 100.0 | 26.34 | 3.72 | 0.557 | 0.000 |
| buying | 100.0 | 15.35 | 2.23 | 0.712 | 0.313 |
| mixed | 100.0 | 15.54 | 2.31 | 0.719 | 0.000 |

Browsing stays clearly distinct — 26.3 shelves at entropy 3.72 against 15.4
at 2.2. **Buying and Mixed have collapsed toward each other**, separable now
only by candidate origin: buying's 0.313 exclusion rate against mixed's zero.
The pre-registration predicted this risk explicitly and named it as
disqualifying — capping every route at 100 destroys the pool-size contrast by
construction, and two of the three routes no longer differ much in what
survives.

R1's headline recall gain also does not survive: Recall@pool was 1.000 with
the unbounded pool and is 0.56–0.72 here. The funnel bought ranking back by
giving the recall away.

## The withdrawn attribution, confirmed

R1's `negative_preference` +0.0889 is now **−0.0167** under R2. Had that gain
been credited to the negative channel, the channel would not have changed
between R1 and R2 and the reversal would be inexplicable. It was a
whole-pipeline artefact of the unbounded deep pool, exactly as the withdrawal
said. `filter_negative` remains off and unpromoted.

## Guards

| scenario | off | R2 on | Δ |
|---|---|---|---|
| override_category | 0.928308 | 0.927106 | −0.0012 |
| override_genuine | 0.922134 | 0.922281 | +0.0001 |
| contradiction | 0.809352 | 0.805072 | −0.0043 |
| negative_preference | 0.700683 | 0.684023 | −0.0167 |
| vague_start | 0.917534 | 0.884045 | −0.0335 |
| **uncooperative** | 0.833266 | **0.675231** | **−0.1581** |

Override and contradiction are flat — the R1 regressions there are fixed. The
thin-query scenarios are where R2 breaks.

## What R2 established

* Deep retrieval **plus a funnel** is a genuine gain: C beats the baseline.
* Cross-branch shelf union removes the 5.5% target exclusion completely
  (94.5% → 100.0% inside).
* The category component is a net cost at both depths, even corrected.
* A fixed funnel cap is incompatible with starvation-aware widening.
* Browsing is topologically distinct; Buying and Mixed largely are not.

## What it did not

* Close Phase 2. Pillar I is still not claimed.
* Justify default-ON, despite passing every stated gate.
* Test any fix for the starvation interaction.
