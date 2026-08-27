# Phase 2B-R3 pre-registration — starvation-aware deep funnel

Written **before** the implementation exists. Recorded at `41f05a7`, 148 tests
passing, everything default-off.

## The single hypothesis

> Ordinary turns use configuration C's deterministic Top-100 funnel. When
> `_starved()` is true, the fixed funnel is bypassed and the Phase 1 widened
> candidate pool is restored. This keeps C's official-evaluator gains while
> recovering `uncooperative` and `vague_start` robustness.

This hypothesis is fixed. It will not be revised, extended or supplemented
after seeing results, and if it is falsified there is no second round of
patch-tuning: the result is reported and Phase 2B stays off.

## Settled, and not reopened this round

R2 is accepted. Category corrected is a net cost at **both** depths (A 0.924983
vs B 0.903003; C 0.931967 vs D 0.926881), so CategoryIndex, FacetIndex and
category ranking weights are **closed for tuning**. Configuration C is the
candidate carried forward:

| | C, recorded at `40d0c1f`, row `c0deaeb522baa964` |
|---|---|
| clean score | `0.931967` |
| clean HR@10 / MRR / MTTC | `0.995` / `0.852556` / `2.065` |
| buying MRR | `0.851181` |
| browsing MRR | `0.809375` |
| boundary MRR | `1.000000` |
| intent_override HR@10 | `1.000` |

C cannot be the default yet because a constant `funnel_top` discards the
widened pool that `_starved()` requests — measured on `uncooperative` as pool
400.4 (off) against 100.0 (on), with score −0.158.

## Naming, so nothing is over-claimed

`dual_plane` conflated three independent things. They are separated into:

| flag | meaning |
|---|---|
| `deep_funnel` | deep retrieval + deterministic Top-100 preselection |
| `category_plane` | category constraint and category candidate source |
| `starvation_bypass` | on a starved turn, bypass the funnel |

`dual_plane` is retained only so already-recorded R1/R2 rows remain
reproducible; it means `deep_funnel and category_plane`.

**The candidate default this round is `deep_funnel` ON, `category_plane` OFF,
`starvation_bypass` ON.** None of this is a completed dual-track and it will
not be described as one.

## Implementation, deliberately minimal

On a starved turn with `starvation_bypass` on, retrieval uses **the Phase 1
legacy path verbatim** — `_retrieve()` at the already-widened `limit`, no
funnel, no quotas. Reusing the verified path is the point; no reservoir,
rotation or quota scheme is designed. Starvation is evaluated per turn from
existing state, so a bypass cannot leak into a later well-evidenced turn.

## Arms

| arm | deep_funnel | category_plane | starvation_bypass |
|---|---|---|---|
| **A** | off | off | Phase 1 existing behaviour |
| **B** | on | off | off |
| **C** | on | off | **on** |

Same seeds `(7,8,9,10,11)`, same evaluator, same catalog, one commit, all
leased and isolated.

**Stop condition:** if **B does not reproduce ≈`0.931967`**, the experiment
halts and the discrepancy is investigated before anything else is run. B is
supposed to be R2's C under a new flag name; if it is not, the flag split is
wrong and no comparison downstream of it means anything.

## Predictions

1. **B reproduces `0.931967`** — it is the same configuration renamed.
2. **C ≈ B on clean and on all four official slices.** The bypass only fires
   on starved turns, which are rare in the public set.
3. **C recovers `uncooperative` and `vague_start`** to within 0.010 of the
   feature-off baseline. This is the hypothesis.
4. **C's starved-turn Recall@pool is clearly above B's**, because B truncates
   the widened pool to 100 and C does not.

Least confident in 3: the bypass restores the pool, but the reranker still
differs from Phase 1 in what reaches it on non-starved turns, and MTTC may
carry regression across turns even when a single starved turn is repaired.

## Acceptance gates

**Official / clean:** score ≥ `0.928508` · HR@10 ≥ `0.990` · MTTC ≤ `2.09` ·
browsing MRR ≥ `0.792` · buying MRR ≥ `0.849` · boundary MRR = `1.000` ·
intent_override HR@10 = `1.000`.

**Starvation robustness**, paired against each scenario's own feature-off
baseline: `uncooperative` score drop ≤ `0.010` and HR@10 drop ≤ `0.010` ·
`vague_start` score drop ≤ `0.010` · starved Recall@pool clearly above B ·
C's starved pool not truncated to 100.

**Guards:** `override_genuine`, `override_category`, `contradiction` each
regress ≤ `0.005` · `ask_policy="other"` bit-exact `0.928708` · full suite
passes.

**Efficiency:** no measurable rise in non-starved latency · starved p95 within
`+10 ms` of the feature-off baseline · memory reported, no unbounded session
state or candidate cache.

## Decision rule

**All gates pass** → candidate default, recorded explicitly as a
*score-oriented deep lexical retrieval improvement*, **not** a completed
dual-track. Category plane stays feature-off/diagnostic. Genuine
Buying/Browsing route separation moves to Phase 4.

**Any gate fails** → Phase 2B stays OFF, no second tuning round; the failing
gate and the B→C causal evidence are written up as they stand.

## Sets

Only existing sets: `clean`, the four official slices, `uncooperative`,
`vague_start`, `override_genuine`, `override_category`, `contradiction`, and
the `ask_policy="other"` compatibility check. No new scenario is added.

Selection seeds `(7,8,9,10,11)`. Robustness seeds `(12…16)` remain unspent.
