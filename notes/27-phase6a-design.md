# Phase 6A design & pre-registration — Context Programming foundation

**For review before implementation. No code written.** Recorded at `bde4d17`,
202 tests passing, `score_default` = deep funnel + starvation bypass +
question utility, dense OFF.

## What Phase 6A is

Make explicit the context that is currently implicit in a 22-key session dict,
the slot list, route history and per-turn trace. **Shadow mode only**:
`ContextPolicy` computes and records a decision and controls nothing.

The session dict remains the source of truth. `ContextSnapshot` is a *view*
built from it, never a replacement — a second source of truth would be a
correctness hazard, not a foundation.

## What the session already holds

Twenty-two keys, written across three modules:

```
terms asked phrases category route profile dry_others provenance overrides
dry_streak broad_options slots outcome wants_more shown uncertain_streak
last_override_turn rotate_pending route_history starved
last_bits last_coverage last_weighed
```

Plus a per-turn trace of ~30 fields already carrying the candidate signals the
snapshot needs: `fused_unique`, `category_coverage`, `pool_before_filter`,
`pool_after_filter`, `retrieval_depth`, `plane`, `starved`,
`starvation_bypass`, `overgeneral`, `structured_options`, `question_bits`,
`question_coverage`.

**Nothing new needs to be computed.** Phase 6A names and bounds what exists.

## `ContextSnapshot` — frozen dataclass, bounded by construction

Every field is a scalar, a small int, or a tuple capped at a stated length.
`slots=True` and `frozen=True`; tuples not lists, so it cannot be mutated in
place.

| group | fields | bound |
|---|---|---|
| route | `route`, `previous_route`, `turns_since_override`, `override_count` | scalars |
| evidence | `active_slots`, `negative_slots`, `abandoned_slots` | ≤ 12 entries each, each a frozen `SlotView(attribute, value_hash, polarity, hardness, confidence, source_turn)` |
| counts | `active_constraint_count`, `query_term_count` | ints |
| engagement | `dry_streak`, `uncertain_streak`, `wants_more`, `starved`, `asked_facets` (≤ 10) | scalars / small tuple |
| candidates | `pool_size`, `category_count`, `category_entropy`, `pool_before_filter`, `pool_after_filter`, `overgeneral` | scalars |
| profile | `profile_source`, `profile_tags` (≤ 8), `profile_coverage`, `profile_credible` | scalars / small tuple |

**Text handling.** No raw customer text. Slot *values* are already distilled
evidence (`"genuine leather"`), not free text, and are kept because reason
codes are unreadable without them — but each is truncated to 40 characters and
the snapshot stores no message, no history and no candidate list.

**Stated size bound: ≤ 64 fields/entries total, ≤ 4 KB serialised.** Asserted
by test, recorded in telemetry per turn.

## `ContextPolicy` — pure function

```python
def decide(snapshot: ContextSnapshot, cfg: Mapping) -> ContextDecision
```

No `self`, no catalog, no session. It cannot touch state because it is never
handed any. `ContextDecision` is frozen:

| field | example |
|---|---|
| `route` | `"browsing"` |
| `retrieval_mode`, `retrieval_depth` | `"broaden"`, `1000` |
| `clarification_mode`, `clarification_attribute` | `"structured"`, `"material"` |
| `relaxation` | `("teal",)` — proposal only |
| `profile_credible` | `False` |
| `reasons` | `("thin_query+dry_streak→broaden", "recent_override→suppress_abandoned")` |

**In Phase 6A the decision is recorded and discarded.** It does not reach
retrieval, ranking, question selection or slot mutation. Shadow mode is
enforced structurally: `decide()` returns a value, and the only caller writes
it to the trace.

## Personalization boundary

Three tiers, precedence fixed and test-locked:

1. **Session facts** — stated this session. Highest. Explicit constraints,
   negations and overrides always win.
2. **Profile priors** — from `user_profile`. Weak, bounded, rejectable.
3. **Inferred preferences** — derived by the system. Lowest.

**Why profile priors must be rejectable, measured on the public set:** there
are only **9 distinct `preference_tags` across all 200 sessions**, and the top
three appear in 82%, 77% and 72% of them — `fit`, `material`, `comfort`.
`purchase_frequency` has **one distinct value across all 200 sessions**, i.e.
zero information. A prior this generic cannot discriminate and will mostly
duplicate or contradict session evidence.

`ContextPolicy` therefore marks a profile tag **not credible** when it overlaps
current session evidence or is too generic to split the pool. This is
*labelling only* in 6A — `w_profile` stays `0.0`, `w_profile_adaptive` stays
`0.0`, and neither is touched. **The previously failed adaptive profile
weighting is not reopened.**

**No cross-session memory.** The official evaluator opens every session with a
fresh `uuid4`, so there is no reliable cross-session identity and any
long-term profile would be fiction built on a random key.

## Telemetry

Added to the existing per-turn trace: `shadow_route`, `shadow_retrieval_mode`,
`shadow_clarification_mode`, `shadow_profile_credible`, `shadow_reasons`
(≤ 6 codes), `snapshot_fields`, `snapshot_bytes`, `profile_rejected_reason`.

No raw text, no unbounded history. Aggregates: reason-code histogram,
shadow-vs-actual route agreement rate, profile-rejection rate, snapshot size
p50/p95.

## Architecture debt — explicitly not touched

* No dependency inversion. The five bidirectional mixin calls stay.
* The Phase 5B domain-edge tests are migration protection, not permanent
  requirements — untouched here, because nothing replaces those dependencies.
* `_clean` living in `catalog` while `dialogue` uses it: **recorded as debt,
  not moved.**
* One observation offered, not acted on: `_uncredible` is snapshot-shaped, so
  a later phase *could* let `ContextSnapshot` carry it and remove one
  `retrieval → dialogue` edge. Out of scope for 6A.

## Acceptance

**A — behaviour.** Shadow OFF identical to `bde4d17`. **Shadow ON bit-exact
too**, since it has no control. Verified on clean + four official slices,
`vague_start`, `uncooperative`, `override_genuine`, `override_category`,
`contradiction`, `supplementary_dev`, compat anchor. Evaluator response schema
unchanged outside `trace`.

**B — correctness.** Snapshot deterministic, immutable (`frozen=True`,
mutation raises), bounded (≤ 64 entries, ≤ 4 KB). Same state ⇒ same snapshot
and decision. Policy provably cannot mutate state, slots, config or catalog —
it receives none of them. Precedence locked: explicit constraint > negative /
override > profile prior. Reason-code tests for `uncooperative`,
`contradiction`, `override_category`, `vague_start`.

**C — performance.** Shadow warm p95 delta **≤ 1 ms**. No `FacetIndex`, no
`DenseIndex`, no new catalog-wide scan — asserted by a test that builds a
snapshot and checks `cat._facet_index is None` and `cat._dense_index is None`.
Snapshot size recorded. **If the budget is missed, 6A ships default-off and the
cost is attributed, not averaged away.**

**D — discipline.** This document reviewed before implementation. All numbers
via `lab/record.py` under isolated lease; only citable rows quoted. Sealed
supplementary holdout **not run**. No change to dense, RRF, category, reranker
or profile weights.

## Predictions

1. Bit-exact everywhere — shadow mode has no control path.
2. Warm p95 delta under 0.3 ms: the snapshot is field copying, and every
   candidate signal is already in the trace.
3. Shadow route will agree with the actual route on **> 95%** of clean turns;
   disagreement concentrates on `vague_start` and `uncooperative`.
4. Least confident: that reason codes are legible enough to be useful. That is
   a judgement, not a measurement, and 6A should be judged on it honestly
   rather than on the fact that a policy exists.

## Stop

Phase 6A ends when this is implemented, measured and recorded. **Phase 6B
(giving the policy control) is not started.**
