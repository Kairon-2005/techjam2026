# Phase 6A results — Context Programming foundation (shadow)

Leased, isolated, `matrix_complete`, `citable()` at **`250ddda`**. 237 tests.

## Verdict: implemented, all gates pass, **`context_shadow` stays default OFF**

Every acceptance gate is met. The recommendation is still to ship it off — see
*Default decision* below.

## A — behaviour: bit-exact

| scenario | shadow OFF | shadow ON | Δ |
|---|---|---|---|
| clean | 0.932067 | 0.932067 | `+0.000000000` |
| vague_start | 0.919247 | 0.919247 | `+0.000000000` |
| uncooperative | 0.831926 | 0.831926 | `+0.000000000` |
| override_genuine | 0.925980 | 0.925980 | `+0.000000000` |
| override_category | 0.931867 | 0.931867 | `+0.000000000` |
| contradiction | 0.814187 | 0.814187 | `+0.000000000` |
| supplementary_dev | 0.441608 | 0.441608 | `+0.000000000` |
| compat anchor | — | 0.928708 | exact |

All four official slices identical. `recommendations`, `ask_attribute` and
`message` identical, asserted by unit test as well as by the matrix.

## B — correctness

Snapshot frozen (mutation raises), deterministic under reversed input, and
bounded — **observed max 40 fields against a 64 bound; bytes p50 777, p95 1526
against a 4096 bound**. The maximal construction test asserts the bounds
directly rather than trusting these observations.

The leak test is a sequence, not an absence: enter with `asked=["material"]`,
snapshot contains `material`, `_pick_attribute` picks this turn's question,
snapshot does **not** contain it, state holds both afterwards. A call-order
test records both calls and requires shadow first.

`decide()` is handed no state, catalog or session, so it cannot mutate one;
canonicalising snapshot and config either side of a call confirms it.

## C — performance

`trace=True` on both sides, so only shadow differs. 240 turns:

| | p50 | p95 | mean |
|---|---|---|---|
| shadow OFF | 18.307 ms | 35.520 ms | 33.107 ms |
| shadow ON | 18.202 ms | 35.068 ms | 32.932 ms |
| **delta** | **−0.105 ms** | **−0.452 ms** | −0.175 ms |

Shadow measures marginally *faster*, which is run-to-run noise. The honest
statement is **no measurable cost**, comfortably inside the ≤1 ms gate — not
that it is free, which a negative delta cannot establish.

No `CategoryIndex`, `FacetIndex` or `DenseIndex` is built by the shadow path.
The integration test is differential, because retrieval builds `CategoryIndex`
under `deep_funnel` regardless; the direct claim — that the summary builds
nothing at all — is proven at unit level.

## D — reason-code legibility, reported separately

**Machine codes, across every scenario:**

| code | total | where it concentrates |
|---|---|---|
| `ASK_STRUCTURED` | 12,303 | everywhere; pools span many shelves |
| `BROADEN_THIN_DRY` | 2,530 | `uncooperative` 816, `supplementary_dev` 1,482 |
| `REJECT_PROFILE_PRIOR` | 817 | `vague_start` 545 |
| `ROTATE_OR_BROADEN` | 316 | **`uncooperative` only** |
| `SUPPRESS_ABANDONED` | 155 | **`override_category` only** |
| `PROPOSE_RELAX_LOW_CONFIDENCE` | **0** | never observed |

**Human explanations** for the six canonical cases:

| code | rendered |
|---|---|
| `BROADEN_THIN_DRY` | thin query and the customer has gone quiet → recommend broadening |
| `ROTATE_OR_BROADEN` | customer asked for more options → rotate or broaden |
| `SUPPRESS_ABANDONED` | recent override → suppress the abandoned preference |
| `PROPOSE_RELAX_LOW_CONFIDENCE` | a hard constraint is below the confidence threshold → propose relaxing it |
| `ASK_STRUCTURED` | candidate pool spans too many shelves → ask a structured question |
| `REJECT_PROFILE_PRIOR` | profile tags are generic or already covered by session evidence → reject the profile prior |

### What this does and does not establish

**Established: reason-code LOCALIZATION.** `SUPPRESS_ABANDONED` fires *only*
on the category-pivot scenario; `ROTATE_OR_BROADEN` *only* where customers ask
for more; `BROADEN_THIN_DRY` concentrates where queries are thinnest. Codes
land where their names say they should, and they are computed from declared
snapshot fields rather than copied from the agent's own choice.

**Not established: the action policy.** Nothing here shows that acting on
these codes would help. They were never allowed to act, so the histogram is
evidence that the *labels* are well-placed, not that the *decisions* attached
to them are right. Phase 6A validated a vocabulary, not a controller.

**`ASK_STRUCTURED` at 77% is not merely a threshold problem.** It exposes a
missing concept: **clarification eligibility**. The code fires whenever the
pool spans enough shelves, with no notion of whether asking a structured
question is *appropriate* on this turn — the real `_pick_attribute` gates
that behind a first-two-`other` rule, an uncertain/easier branch and a dry
give-up guard, none of which the snapshot models. Retuning
`overgeneral_cats` would move the rate without supplying the missing gate.

**Multi-signal precedence is undefined.** `decide()` appends reason codes in
source order and lets later branches overwrite `clarification_mode`. That is
adequate for observation and inadequate for control: when request-more,
thin-and-dry, uncertain and overgeneral fire together, nothing states which
should win. A precedence table has to precede any takeover.

**Most codes explain existing mechanisms rather than adding product
behaviour.** `BROADEN_THIN_DRY` narrates `_starved()`; `ASK_STRUCTURED`
narrates `_overgeneral()`; `SUPPRESS_ABANDONED` narrates the abandoned-span
logic. That is exactly what a foundation phase should produce — a legible
account of what already happens — but it should not be read as new capability.

**Two honest qualifications.**

1. `PROPOSE_RELAX_LOW_CONFIDENCE` **never fires**, and is the one code with no
   existing mechanism behind it. Every hard slot on these
   sets arrives through the template parser at confidence 1.0. It is unit-
   tested with a hand-built slot and has never been observed in the wild. It
   is a code the system can emit, not a capability demonstrated on data.
2. `ASK_STRUCTURED` accounts for **77%** of all codes — see the eligibility
   gap above. Phase 6B must not let it drive behaviour; it stays a historical
   observation code, and any action needs its own code gated on eligibility.

**Route agreement is 1.000 and proves nothing.** The default row copies
`snapshot.route`, so this partly measures itself. Reported as the diagnostic
it was pre-registered as, never as capability. Profile rejection: 1.46% of
clean turns.

## Default decision: `context_shadow = False`

All gates pass, so turning it on is permitted. **The recommendation is to
leave it off**, because it is pure diagnostics: it changes no
customer-visible field by construction, so on the scoring path it is work with
no product benefit. It is enabled per-run by config for analysis and demos.

That is a judgement about where diagnostic work belongs, not a gate failure,
and it is reversible with one config key.

## Not done, deliberately

The policy controls nothing: not route, retrieval, clarification, relaxation
or personalization. `w_profile` and `w_profile_adaptive` remain `0.0`. No
dense, RRF, category or reranker change. The sealed supplementary holdout was
not run. **Phase 6B is not started.**
