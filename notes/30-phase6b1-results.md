# Phase 6B1 results — staged context + retrieval takeover

Two commits, as contracted. Measurement at `36cc7fd`/`af2cc44`, adoption at
`89e6f12`. All rows leased, isolated, `matrix_complete`, `citable()`.

## Verdict: **adopted.** `retrieval_context_mode = "control"` is the default.

Every agreement, bit-exactness and performance gate passed, so the
pre-registered promotion rule applies: shipping a verified controller in
`"off"` would be building the thing and declining to use it.

`context_shadow` stays `False`.

## B vs A — total per-turn agreement

The gate was zero disagreements, not a rate.

| scenario | turns | starved disagreements | depth disagreements |
|---|---|---|---|
| clean | 411 | 0 | 0 |
| vague_start | 503 | 0 | 0 |
| uncooperative | 560 | 0 | 0 |
| override_genuine | 421 | 0 | 0 |
| override_category | 413 | 0 | 0 |
| contradiction | 632 | 0 | 0 |
| supplementary_dev | 5,544 | 0 | 0 |
| **total** | **8,483** | **0** | **0** |

Agreement is reported as counts rather than a rate on purpose: `0.9997` reads
as a pass while naming three broken turns; `0` does not.

## C vs A, and adoption vs measured C — bit-exact

| scenario | A off | C control | adopted default | adopted row key |
|---|---|---|---|---|
| clean | 0.932067 | 0.932067 | 0.932067 | `3b6e0d8d4a05541c` |
| vague_start | 0.919247 | 0.919247 | 0.919247 | `2a1eb3d7205c18cd` |
| uncooperative | 0.831926 | 0.831926 | 0.831926 | `47547fb3269df17e` |
| override_genuine | 0.925980 | 0.925980 | 0.925980 | `abc816062d61fbe1` |
| override_category | 0.931867 | 0.931867 | 0.931867 | `4de4c0bb985ebae1` |
| contradiction | 0.814187 | 0.814187 | 0.814187 | `e939b7bb1220f4e6` |
| supplementary_dev | 0.441608 | 0.441608 | 0.441608 | `cacc773b518981e8` |
| compat anchor | — | 0.928708 | 0.928708 | `eaf0b6023e93dd1a` |

All four official slices identical throughout. The adoption commit was
anchored against **measured C**, not only against the historical baseline, so
the wrapper is verified against what was actually measured.

## Performance — the gate needed a different instrument

End-to-end p95, three repetitions:

| rep | off p95 | control p95 | delta |
|---|---|---|---|
| 1 | 35.150 ms | 34.632 ms | −0.518 |
| 2 | 34.698 ms | 35.229 ms | +0.531 |
| 3 | 34.815 ms | 35.141 ms | +0.326 |

**Identical configurations varied by 0.452 ms run to run.** The noise floor is
larger than the 0.2 ms gate, so end-to-end p95 cannot decide it in either
direction — and a single sample could have been quoted as a comfortable pass
or a clear failure depending on which one was taken.

Timing the added work directly instead — snapshot construction, `policy_from`
and `decide_retrieval`, 200,000 iterations:

**1.852 µs per turn = 0.00185 ms, 108× inside the 0.2 ms gate.**

That is the figure the decision rests on. It measures the quantity the gate is
about, at a resolution appropriate to it.

## One rule, not two

The measurement commit deliberately held both rules so shadow mode could
compare them across 8,483 turns. The adoption commit **deletes the legacy
body**: `_starved()` keeps its name and becomes an adapter that builds a
`PreRetrievalSnapshot` and returns `decide_retrieval(...).starved`.

A test patches `decide_retrieval` and asserts `_starved`'s answer changes with
it, so the body cannot quietly reacquire the rule. Two copies of a starvation
rule is the defect class this project has already paid for twice — the void
`suppress_abandoned` switch and the inert `route_overrides` patch.

## Boundary grid

144 cells crossing `dry_streak` (below/at threshold), query terms
(below/at/above), active slots (below/at/above), `rotate_pending`, and
`starved_candidates` (0 / below / equal / above base). Every cell compares
legacy against new on both verdict and depth. The at-threshold cells carry the
weight: `>=` versus `>` in a relocated comparison is the likeliest silent
failure, and only those cells would catch it.

## Action codes in the wild

| code | total | where |
|---|---|---|
| `DEPTH_STANDARD` | 16,322 | everywhere |
| `WIDEN_THIN_EVIDENCE` | 2,364 | `uncooperative` 650, `supplementary_dev` 1,482, `contradiction` 182, `vague_start` 50 |
| `WIDEN_REQUEST_MORE` | 316 | **`uncooperative` only** |
| `WIDEN_DISABLED` | 0 | `starved_candidates` is never 0 in these configs |

`WIDEN_REQUEST_MORE` firing only where customers ask for more confirms the
attribution split is doing what it was added for — and it changed no depth,
which a test asserts directly.

## Prediction 4 resolved

The least-confident prediction was that `rotate_pending` might not be live at
the insertion point on every path. It is: 316 `WIDEN_REQUEST_MORE` firings
and zero disagreements across 8,483 turns. The 100% gate was the right
instrument for that doubt.

## Not done

`_pick_attribute`, clarification, `ASK_STRUCTURED`, question policy — **6B2,
not started.** Profile weighting untouched; per-tag credibility is 6C. No
dense, category, reranker or profile-weight change. Sealed holdout not run.
