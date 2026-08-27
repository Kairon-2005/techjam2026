# Phase 6A design & pre-registration — Context Programming foundation

**Revision 3. For review before implementation. No code written.** Recorded at
`b2c1b6d`, 202 tests passing, `score_default` = deep funnel + starvation
bypass + question utility, dense OFF.

Revision 3 fixes five further defects found in review; revision 2 fixed five
before them. All are recorded rather than silently corrected, because they are
the kind of error this project keeps catching late.

**Revision 3:**

| # | defect in revision 2 |
|---|---|
| 1 | claimed this turn's `asked`/`last_*` "do not exist yet". They exist and hold PRIOR-turn history; only this turn's append has not happened |
| 2 | `overgeneral` had no current-turn source — `_overgeneral()` runs inside `_pick_attribute`, so `state["broad_options"]` is last turn's |
| 3 | used cumulative `wants_more` as a current-turn trigger, and `rotate_pending` is already consumed by `_rotate` before the snapshot point |
| 4 | implied `PROPOSE_RELAX_LOW_CONFIDENCE` detects the contradiction scenario. It cannot — that scenario's false constraint arrives via template at confidence 1.0 |
| 5 | determinism contract omitted the ranked window and retrieval trace, which the snapshot reads |

**One correction to the review itself.** `trace["category_coverage"]` *does*
exist under `score_default` (`retrieval.py:398`, `deep_funnel=True`) and
carries `categories`, `entropy` and `top_share`. But it describes
`deduped[:100]` — the **pre-rerank funnel output** — not the ranked window the
question is about. So the fields are not missing; they describe the wrong
population, which is a better reason not to reuse them. They are recomputed
over the ranked window instead (§2 below).

**Revision 2 fixed:**

| # | defect in revision 1 |
|---|---|
| 1 | snapshot timing unspecified — it could have read this turn's own question outputs |
| 2 | `clarification_attribute` was not derivable from the declared fields |
| 3 | the declared ≤64-entry bound was violated by the design itself: **73** |
| 4 | `profile_coverage` was presented as an existing trace field. **It does not exist** — I invented it, and the claim "nothing new needs computing" was false |
| 5 | shadow/actual route agreement was proposed as a success gate, which is circular |

## What Phase 6A is

Make explicit the context currently implicit in a 22-key session dict, the slot
list, route history and per-turn trace. **Shadow mode only**: `ContextPolicy`
computes and records a decision and controls nothing.

The session dict remains the source of truth. `ContextSnapshot` is a *view*
built from it, never a replacement — a second source of truth would be a
correctness hazard, not a foundation.

## 1. Snapshot timing — fixed

`respond()` today, by line:

```
503  cands, trace = self._candidates(...)      retrieval
505  ranked      = self._rerank(...)           ranking
508  ranked      = self._rotate(...)
514  trace.update({turn, starved, ...})
     <-- SNAPSHOT AND SHADOW DECISION ARE BUILT HERE
524  attribute   = self._pick_attribute(state, ranked)
525  state["asked"].append(attribute)
529  trace.update({asked, question_bits, question_coverage, ...})
```

The snapshot is built **after retrieval and reranking, before
`_pick_attribute()`**.

At that point `state["asked"]` and `last_bits` / `last_coverage` **do exist and
hold prior-turn history** — revision 2 wrongly said they did not exist. What
has not happened is *this turn's* append at line 525 and overwrite at 529. The
snapshot may therefore read them as history, and cannot see this turn's own
question.

Historical question signals are named for what they are:
`previous_question_mode`, `previous_question_bits`,
`previous_question_coverage` — carried from the prior turn's trace, never this
turn's.

**Test**, stated as the sequence it must produce:

1. enter the turn with `state["asked"] == ["material"]`;
2. the snapshot **contains** `material` — prior history is visible;
3. `_pick_attribute` returns `color` for this turn;
4. the snapshot **does not contain** `color` — this turn's question is not
   visible to it;
5. after `respond()` returns, `state["asked"] == ["material", "color"]`.

Plus an ordering assertion that the snapshot call site precedes
`_pick_attribute`.

## 2. Clarification output — only what is derivable

The snapshot carries **no per-facet entropy, coverage or utility**, so a pure
policy cannot make a candidate-aware attribute choice from it. Revision 1
implied it could.

**Phase 6A therefore emits `clarification_mode` only, with
`clarification_attribute = None`.** Modes are derivable from declared fields:
`open` · `structured` (from `overgeneral`) · `easier` (from
`uncertain_streak`) · `none`.

**Copying the real `_pick_attribute()` result into the shadow decision is
forbidden.** A prediction that reads the answer is not a prediction. Candidate-
aware attribute selection requires refactoring `_pool_attribute` into a pure
function over an explicit facet-statistics input — **Phase 6B**, not here.

## 3. Snapshot bound — fixed, and now provable

Revision 1 allowed 12 slot views *per category*, so its true maximum was 73
against a declared 64.

**`MAX_SLOT_VIEWS = 12` is now global across all three slot tuples combined.**
Retention is deterministic, by this priority, ties broken by descending
`source_turn` then `attribute` ascending:

1. recent explicit negative or override-superseded slots
2. active **hard** slots, by confidence descending, then recency
3. active **soft** slots, same order
4. abandoned slots

| group | entries |
|---|---|
| route (`route`, `previous_route`, `turns_since_override`, `override_count`) | 4 |
| slot views (all three tuples, shared cap) | ≤ 12 |
| counts (`active_constraint_count`, `query_term_count`) | 2 |
| engagement scalars (`dry_streak`, `uncertain_streak`, `starved`, `current_request_more`) | 4 |
| `asked_facets` | ≤ 10 |
| `previous_question_*` | 3 |
| candidates — see §2b (`pool_size`, `pool_before_filter`, `pool_after_filter`, `category_count`, `category_entropy`, `overgeneral`, `overgeneral_option_count`) | 7 |
| profile scalars (`profile_source`, `profile_credible`, `profile_tag_count`) | 3 |
| `profile_tags` | ≤ 8 |
| **maximum total** | **53** |

**Canonical serialization**, defined so `snapshot_bytes` is reproducible:
`json.dumps(asdict(snapshot), sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`, UTF-8 encoded. Slot values truncated to 40 characters.

**Test:** construct the maximal snapshot — 12 slot views at 40-char values, 10
asked facets, 8 profile tags, every scalar at its widest — assert ≤ 64 entries
and canonical bytes ≤ 4096. Estimated maximum ≈ 2.4 KB; the test asserts the
bound rather than the estimate.

## 2b. Current over-generality and category spread — bounded, and new work

`_overgeneral()` runs **inside** `_pick_attribute` (`dialogue.py:283`), so at
the snapshot point `state["broad_options"]` holds **last turn's** signal.
Revision 2 treated it as current.

The snapshot instead builds a **bounded candidate summary** over
`ranked[:pool_depth]` — 30 items already in hand:

* `overgeneral`, `overgeneral_option_count` — by calling the existing
  `_overgeneral(ranked, turn_cfg)`. It is a pure function returning a tuple;
  the write to `state["broad_options"]` happens in its *caller*, so calling it
  from the snapshot builder mutates nothing.
* `category_count`, `category_entropy` — `category_index.coverage()` over the
  same 30 ids.

**This is new computation and is acknowledged as such:** ~30 category lookups,
one `_overgeneral` pass over 30 blobs, and `_overgeneral` then runs a second
time inside `_pick_attribute`. Both are bounded by `pool_depth` and both are
**counted in the latency budget**.

`trace["category_coverage"]` is deliberately **not** reused. It exists under
`score_default` (`retrieval.py:398`), but describes `deduped[:100]` from the
funnel *before reranking* — a different population from the window the question
is about.

## 3b. Request-more — current turn, not cumulative

`state["wants_more"]` is a **cumulative counter** (`+= 1`, `agent.py:430`), and
`state["rotate_pending"]` has already been **consumed** by `_rotate` at line
508 before the snapshot point. Neither can express "this turn".

The snapshot uses:

```python
current_request_more = state["outcome"] == Outcome.REQUEST_MORE
```

`state["outcome"]` is set at line 428, before the snapshot point, and refers to
this turn. Cumulative `wants_more` stays in **telemetry only** — not in the
snapshot, and not in any truth-table condition.

## 4. Personalization — corrected data source and framing

**`profile_coverage` is not an existing trace field. Revision 1 said otherwise
and was wrong, and its claim that "nothing new needs computing" was false.**

If coverage is kept, it is **computed**, over a bounded window only:

* the **Top-30 ranked candidates** already in hand at the snapshot point;
* at most **8 profile tags**;
* therefore **≤ 240 substring checks** against text already in memory;
* **no catalog-wide scan**, no `FacetIndex`, no `DenseIndex`;
* **counted in the latency budget** below, not excluded from it.

**Test:** a counting stub asserts ≤ 8 tags × ≤ 30 candidates, and that
`cat._facet_index is None` and `cat._dense_index is None` afterwards.

**Framing, corrected.** The system must not invent cross-session memory keyed
on a random session id — the evaluator opens each session with a fresh
`uuid4`, so anything learned across sessions would be fiction. But the
evaluator-supplied `user_profile` **is legitimate external long-term context**,
and may be consumed, down-weighted or rejected. Revision 1 conflated the two.

Precedence, test-locked: **session facts > negative/override > profile priors >
inferred preferences.**

Why priors are weak here, measured: **9 distinct `preference_tags` across all
200 public sessions**, the top three (`fit`, `material`, `comfort`) appearing
in 82% / 77% / 72%; `purchase_frequency` has **one distinct value across all
200**, i.e. zero information.

6A **labels** credibility only. `w_profile` and `w_profile_adaptive` both stay
`0.0`; the previously failed adaptive weighting is not reopened.

## 5. Reason codes — stable enum, separate renderer, truth table

Codes are a **stable enum**, stored and aggregated as symbols. Human-readable
text comes from a **separate renderer**, tested independently, so wording can
change without invalidating recorded telemetry.

Pre-registered canonical mappings:

| condition | code |
|---|---|
| thin query + dry streak | `BROADEN_THIN_DRY` |
| explicit request for more | `ROTATE_OR_BROADEN` |
| recent override + abandoned slot | `SUPPRESS_ABANDONED` |
| hard slot with `confidence < 0.9` | `PROPOSE_RELAX_LOW_CONFIDENCE` |
| over-general pool | `ASK_STRUCTURED` |
| generic or conflicting profile | `REJECT_PROFILE_PRIOR` |

**`PROPOSE_RELAX_LOW_CONFIDENCE` is not contradiction detection.** It fires on
a hard slot whose confidence is below 0.9, and nothing more. The
`contradiction` scenario injects its false constraint through the **template**
parser, which yields `confidence = 1.0`, and the official target is hidden —
so **Phase 6A cannot detect a contradiction and does not claim to.**

`contradiction` therefore serves **only as a bit-exact robustness gate**, and
this code's unit test uses a hand-constructed low-confidence hard slot.

### Truth table — decisions derived, not copied

| snapshot condition | route | retrieval | clarification | reason |
|---|---|---|---|---|
| `active_constraint_count == 0`, category known | `browsing` | `standard` | `open` | — |
| `query_term_count ≤ 8` **and** `dry_streak ≥ 2` | unchanged | `broaden` | `easier` | `BROADEN_THIN_DRY` |
| `current_request_more` | unchanged | `broaden` | `none` | `ROTATE_OR_BROADEN` |
| `turns_since_override ≤ 1` **and** abandoned slot present | unchanged | `standard` | `open` | `SUPPRESS_ABANDONED` |
| any active hard slot with `confidence < 0.9` | unchanged | `standard` | `open` | `PROPOSE_RELAX_LOW_CONFIDENCE` |
| `overgeneral` true | unchanged | `standard` | `structured` | `ASK_STRUCTURED` |
| profile tag ∈ session evidence, or coverage > `profile_max_coverage` | — | — | — | `REJECT_PROFILE_PRIOR` |
| otherwise | `snapshot.route` | `standard` | `open` | — |

`route` is copied from the snapshot **only in the default row**, where there is
no signal to justify anything else. Every other row derives its output from
declared fields. Agreement with the actual route is therefore **diagnostic
only, never a success gate** — revision 1 proposed it as a gate, which is
circular when the default row copies the route.

## Architecture debt — explicitly not touched

No dependency inversion. The five bidirectional mixin calls stay. The Phase 5B
domain-edge tests are migration protection and are untouched, because nothing
replaces those dependencies. `_clean` living in `catalog` while `dialogue` uses
it is **recorded as debt, not moved**.

Offered, not acted on: `_uncredible` is snapshot-shaped, so a later phase could
carry it in `ContextSnapshot` and remove one `retrieval → dialogue` edge.

## Acceptance

**A — behaviour.** Shadow OFF identical to `ffaba38`. **Shadow ON bit-exact
too**, since it has no control. Verified on clean + four official slices,
`vague_start`, `uncooperative`, `override_genuine`, `override_category`,
`contradiction`, `supplementary_dev`, compat anchor. `recommendations`,
`ask_attribute`, `message` and scores unchanged; evaluator response schema
unchanged outside `trace`.

**B — correctness.** Snapshot deterministic, immutable (`frozen=True`,
mutation raises), bounded (≤ 64 entries, ≤ 4096 canonical bytes, proven on a
maximal construction).

**Determinism contract:** *same session state + retrieval trace + ranked
window + turn index ⇒ same snapshot and same decision.* Revision 2 said "same
state", which was incomplete: the snapshot also reads the retrieval trace and
the ranked window, so both belong in the contract. Policy cannot
mutate state, slots, config or catalog — it is handed none of them. Snapshot
built before `_pick_attribute`, with no current-question outcome in its input.
Precedence locked. Reason-code tests for `uncooperative`,
`override_category` and `vague_start` from scenarios; the
`PROPOSE_RELAX_LOW_CONFIDENCE` test uses a hand-built low-confidence hard
slot. `contradiction` appears ONLY as a bit-exact gate.
`override_category`, `vague_start`. Enum and renderer tested separately.

**C — performance.** Shadow warm p95 delta **≤ 1 ms**, including profile
coverage. No `FacetIndex`, no `DenseIndex`, no catalog-wide scan. Snapshot size
recorded p50/p95. **If the budget is missed, 6A ships default-off and the cost
is attributed, not averaged away.**

**D — discipline.** This document reviewed before implementation. All numbers
via `lab/record.py` under isolated lease; only citable rows quoted. Sealed
supplementary holdout **not run**. No change to dense, RRF, category, reranker
or profile weights.

## Predictions

1. Bit-exact everywhere — shadow mode has no control path.
2. Warm p95 delta under 0.8 ms. Raised again from revision 2's 0.5 ms: the
   bounded candidate summary in §2b is also real work — ~30 category lookups,
   one extra `_overgeneral` pass over 30 blobs, plus ≤ 240 substring checks
   for profile coverage.
   profile coverage is now acknowledged as real work: ≤ 240 substring checks.
3. Maximal canonical snapshot ≈ 2.4 KB at 53 entries, inside 4096 bytes.
4. Shadow/actual route agreement high on clean, lower on `vague_start` and
   `uncooperative` — **reported as a diagnostic, not scored.**
5. Least confident, and the one that matters: whether the reason codes are
   legible enough to be useful. That is a judgement, not a measurement, and 6A
   should be judged on it honestly rather than on the existence of a policy.

## Stop

Phase 6A ends when this is implemented, measured and recorded. **Phase 6B —
giving the policy control, and the pure-function refactor of `_pool_attribute`
that candidate-aware attribute selection needs — is not started.**
