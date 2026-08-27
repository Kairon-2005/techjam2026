# Phase 6B1 design & pre-registration — staged context + retrieval takeover

**For review before implementation. No code written.** Recorded at `e0d4d22`,
237 tests passing, `context_shadow=False`, dense OFF.

## What 6A left, and what 6B1 must not inherit

Phase 6A established reason-code **localization** and nothing about an action
policy (notes/28 §D). Three consequences bind this design:

* **`ContextSnapshot` is post-retrieval and cannot drive retrieval.** It reads
  `pool_size`, `category_count`, `overgeneral` — all produced *by* the
  retrieval it would have to decide. 6B1 therefore introduces a **separate,
  strictly earlier** snapshot rather than reusing it.
* **`ASK_STRUCTURED` must not drive anything.** It stays a historical
  observation code. Clarification is 6B2's problem, not 6B1's.
* **Multi-signal precedence is undefined in 6A.** 6B1 touches only retrieval,
  where the existing rule is already total, so it can define precedence by
  *reproducing* that rule rather than inventing one.

## The staged pipeline

```
state / evidence update            agent.py 458-524
route retarget                     agent.py 525-528
--> PreRetrievalSnapshot           NEW, inserted here
--> RetrievalDecision              NEW, pure
depth / starvation                 agent.py 531-536   (arm C reads the decision)
candidate generation               agent.py 538
rerank / rotate                    agent.py 539-543
ContextSnapshot (6A, shadow)       agent.py 546+
```

The insertion point is **after the retarget block and before
`top_k = min(...)`**. Everything the snapshot reads is already final at that
line: `_rebuild_terms` has run (524), the route has moved (525-528), and
`rotate_pending` is still live — `_rotate` does not consume it until
`retrieval.py:42`, well after.

## `PreRetrievalSnapshot` — frozen, and deliberately thin

Only fields that exist *before* retrieval:

| field | source at the insertion point |
|---|---|
| `route` | `state["route"]`, post-retarget |
| `turn` | argument |
| `query_term_count` | `len(state["terms"])`, post-rebuild |
| `active_slot_count` | `sum(1 for s in slots if s.usable)` |
| `dry_streak` | `state["dry_streak"]` |
| `outcome` | `state["outcome"]` |
| `rotate_pending` | `state["rotate_pending"]`, not yet consumed |
| `candidates` | `cfg["candidates"]` |
| `starved_candidates` | `cfg["starved_candidates"]` |
| `starved_after` | `cfg["starved_after"]` |
| `starved_max_terms` | `cfg["starved_max_terms"]` |
| `starved_max_slots` | `cfg["starved_max_slots"]` |
| `rerank` | `cfg["rerank"]` |

**No post-retrieval field may appear here** — no `pool_size`,
`category_count`, `category_entropy`, `overgeneral`, profile coverage or
candidate list. A test enumerates the dataclass fields against an explicit
allowlist so a later addition cannot smuggle one in.

The config thresholds are carried *in the snapshot* rather than read from
`cfg` inside the function, so the decision is a function of one argument and
its inputs are visible in telemetry.

## `decide_retrieval` — pure

```python
def decide_retrieval(snapshot: PreRetrievalSnapshot,
                     cfg: Mapping) -> RetrievalDecision
```

| output | meaning |
|---|---|
| `starved: bool` | exactly today's `_starved()` verdict |
| `candidate_depth: int` | `candidates`, widened to `starved_candidates` when starved |
| `retrieval_mode: str` | `"standard"` \| `"widened"` |
| `reasons: tuple[RetrievalReason, ...]` | action codes, see below |

`limit = max(top_k, depth) if rerank else top_k` **stays in the host**, because
`top_k` is a caller argument and not session context. Putting it in the
snapshot would widen the type for no gain.

### Reason codes — new, and separate from 6A's

6A's codes are *observations*. These are *actions*, and mixing them would let
an observation code silently acquire control:

| code | condition |
|---|---|
| `WIDEN_THIN_EVIDENCE` | stalled ≥ `starved_after` **and** query thin |
| `WIDEN_REQUEST_MORE` | `rotate_pending` **and** query thin |
| `DEPTH_STANDARD` | not starved |
| `WIDEN_DISABLED` | `starved_candidates == 0` |

`BROADEN_THIN_DRY` (6A) keeps its name and stays observation-only.

### Precedence — reproduced, not invented

Today's rule is total and 6B1 copies it exactly:

```
if starved_candidates == 0        -> WIDEN_DISABLED, standard
elif not (stalled or rotate_pending) -> DEPTH_STANDARD, standard
elif terms <= max_terms or active <= max_slots
                                  -> widened; reason is WIDEN_REQUEST_MORE if
                                     rotate_pending else WIDEN_THIN_EVIDENCE
else                              -> DEPTH_STANDARD, standard
```

**6B1 changes no retrieval behaviour.** It relocates a rule. Any score
movement is a defect, not a result.

## Three arms

| arm | `_starved` / depth | new decision |
|---|---|---|
| **A** | current code | not computed |
| **B** | current code | computed **alongside**, controls nothing |
| **C** | **from `RetrievalDecision`** | controls depth and starvation |

Gated by `context_orchestration`, default **False**. `context_shadow` stays
**False** throughout and is not re-opened.

## Acceptance

**B vs A — agreement must be total, not statistical.** Per turn, across
`clean`, the seven robustness scenarios and `supplementary_dev`:

* `starved` agreement **100%**
* `candidate_depth` agreement **100%**
* plane, starvation-bypass and funnel/bypass choice identical

A single disagreeing turn stops 6B1. This is a relocation; "almost always
agrees" is a failure.

**C vs A — bit-exact.** `recommendations`, `message`, `ask_attribute`; score,
HR@10, MRR, MTTC and all four official slices; seven robustness scenarios,
`supplementary_dev`, compat anchor. Retrieval telemetry — plane, depth, pool
sizes, bypass — exact. Warm p95 delta **≤ 0.2 ms**. No new index built. Sealed
holdout not run.

## Single source of truth

If C passes, `_starved()` is retained as a **thin wrapper** that builds a
`PreRetrievalSnapshot` and returns `decide_retrieval(...).starved`. The rule
exists once. Two copies of a starvation rule is exactly the class of defect
this project has already paid for twice — the void `suppress_abandoned` switch
and the inert `route_overrides` patch.

A test asserts `_starved` and `decide_retrieval` cannot disagree, by driving
both from the same generated snapshots.

## Predictions

1. **B agrees with A on 100% of turns.** If not, the extracted rule is not the
   rule, and that is the finding.
2. **C is bit-exact.** Same rule, same inputs, same outputs.
3. Warm p95 delta **< 0.05 ms** — one frozen dataclass and a handful of integer
   comparisons per turn.
4. Least confident: that `rotate_pending` is genuinely still live at the
   insertion point in **every** path. It is set at 466, cleared at 523 on new
   evidence, and consumed at `retrieval.py:42`. The 100% agreement gate in B
   is what would catch a path I have not traced.

## Not in scope

`_pick_attribute`, clarification, `ASK_STRUCTURED`, question policy — **6B2,
designed only after 6B1 is accepted.** Profile weighting is untouched;
per-tag credibility (`coverage`, `overlaps_session_attribute`,
`overlaps_session_value`, `rejection_reason`) is **6C**, and 6A's single
`profile_credible` boolean is already recorded as insufficient to drive
ranking. No dense, category, reranker or profile-weight change.

**6B1 stops when measured and recorded. 6B2 is not started automatically.**
