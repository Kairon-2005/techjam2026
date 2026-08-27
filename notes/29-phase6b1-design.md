# Phase 6B1 design & pre-registration — staged context + retrieval takeover

**Revision 2. For review before implementation. No code written.** Recorded at
`c3150d4`, 237 tests passing, `context_shadow=False`, dense OFF.

Revision 2 fixes eight contract defects found in review: a boolean where three
states were needed; observed context conflated with policy configuration;
unlocked depth semantics; undefined simultaneous-reason attribution; a
migration that would have left duplicated rules as the final state; a missing
boundary grid; vague scenario counts; and an unstated promotion rule.

## What 6A left, and what 6B1 must not inherit

Phase 6A established reason-code **localization** and nothing about an action
policy (notes/28 §D). Three consequences bind this design:

* **`ContextSnapshot` is post-retrieval and cannot drive retrieval.** It reads
  `pool_size`, `category_count`, `overgeneral` — all produced *by* the
  retrieval it would have to decide. 6B1 introduces a **separate, strictly
  earlier** snapshot rather than reusing it.
* **`ASK_STRUCTURED` must not drive anything.** It stays a historical
  observation code; clarification is 6B2's problem.
* **Multi-signal precedence is undefined in 6A.** 6B1 touches only retrieval,
  where the existing rule is already total, so it defines precedence by
  *reproducing* that rule rather than inventing one.

## The staged pipeline

```
state / evidence update            agent.py 458-524
route retarget                     agent.py 525-528
--> PreRetrievalSnapshot           NEW, inserted here
--> RetrievalDecision              NEW, pure
depth / starvation                 agent.py 531-536   (control mode reads it)
candidate generation               agent.py 538
rerank / rotate                    agent.py 539-543
ContextSnapshot (6A, shadow)       agent.py 546+
```

Insertion is **after the retarget block, before `top_k = min(...)`**.
Everything read there is final: `_rebuild_terms` has run (524), the route has
moved (525-528), and `rotate_pending` is still live — `_rotate` does not
consume it until `retrieval.py:42`.

## 1. Three modes, not a boolean

```
retrieval_context_mode = "off" | "shadow" | "control"
```

| mode | new decision | who controls |
|---|---|---|
| `off` | not computed | old path |
| `shadow` | computed | old path |
| `control` | computed | **new decision** |

**`control` must work with `trace=False`.** Telemetry may depend on `trace`;
orchestration may not. A boolean would have forced those two concerns to share
a switch — and 6A already needs `trace` for its own shadow, so the coupling
would have been invisible until someone ran with tracing off.

`context_shadow` (6A) stays **False** throughout and is not reopened.

## 2. Observed context, separated from policy configuration

```python
@dataclass(frozen=True)
class PreRetrievalSnapshot:      # what the session IS
    route: str
    query_term_count: int
    active_slot_count: int
    dry_streak: int
    rotate_pending: bool

@dataclass(frozen=True)
class RetrievalPolicy:           # what the config SAYS
    candidates: int
    starved_candidates: int
    starved_after: int
    starved_max_terms: int
    starved_max_slots: int

def decide_retrieval(snapshot: PreRetrievalSnapshot,
                     policy: RetrievalPolicy) -> RetrievalDecision
```

Revision 1 carried thresholds inside the snapshot, which made "what was
observed" and "how it was judged" indistinguishable in telemetry — two
different runs could show identical snapshots and different decisions with
nothing to explain the gap.

`turn`, `outcome` and `rerank` are **removed**: none is read by the starvation
rule. `top_k`, `rerank` and the final `limit` stay in the **host**.

An allowlist test enumerates the snapshot's fields, so no post-retrieval
quantity — `pool_size`, `category_count`, `overgeneral`, profile coverage —
can be added later.

## 3. Candidate depth, locked

```python
candidate_depth = (max(policy.candidates, policy.starved_candidates)
                   if starved else policy.candidates)
```

This is today's rule written as one expression. **Tests cover
`starved_candidates` below, equal to, and above `candidates`** — the `max` is
only observable in the "below" case, and an implementation that simply
assigned `starved_candidates` would pass every test that omitted it.

## 4. Reason codes, and simultaneous attribution

Action codes, kept separate from 6A's observation codes so an observation code
cannot silently acquire control:

| code | condition |
|---|---|
| `WIDEN_THIN_EVIDENCE` | stalled ≥ `starved_after` **and** query thin |
| `WIDEN_REQUEST_MORE` | `rotate_pending` **and** query thin |
| `DEPTH_STANDARD` | not starved |
| `WIDEN_DISABLED` | `starved_candidates == 0` |

**When `stalled` and `rotate_pending` are both true, `WIDEN_REQUEST_MORE` wins
for attribution.** This is **attribution only**: the starvation verdict and
`candidate_depth` are identical either way, and a test asserts that swapping
the attribution does not move the depth. Reporting an explicit customer
request as though it were a stall would misdescribe the turn, which matters
for telemetry and not at all for behaviour.

`BROADEN_THIN_DRY` (6A) keeps its name and stays observation-only.

### Precedence — reproduced, not invented

```
if starved_candidates == 0            -> WIDEN_DISABLED,  standard
elif not (stalled or rotate_pending)  -> DEPTH_STANDARD,  standard
elif terms <= max_terms or active <= max_slots
                                      -> widened; WIDEN_REQUEST_MORE if
                                         rotate_pending else WIDEN_THIN_EVIDENCE
else                                  -> DEPTH_STANDARD,  standard
```

**6B1 changes no retrieval behaviour.** It relocates a rule. Any score
movement is a defect, not a result.

## 5. Migration — two commits, and the first is explicitly temporary

**Commit 1 — measurement.** Old `_starved()` remains; all three modes
available; arm B compares predicted against actual per turn; arm C is driven
by the new decision.

> The duplicated rule in this commit is **experimental and must not be the
> final state.** Two copies of a starvation rule is the defect class this
> project has already paid for twice — the void `suppress_abandoned` switch
> and the inert `route_overrides` patch — and it is tolerated here only long
> enough to measure the replacement against the original.

**Commit 2 — adoption**, only after every C gate passes. `_starved()` becomes
a thin wrapper building snapshot + policy and returning
`decide_retrieval(...).starved`; **the old rule body is deleted**; and a final
isolated bit-exact anchor runs against the measured C agent, so adoption is
verified against what was measured rather than assumed identical to it.

## 6. Boundary grid — every cell compares legacy against new

A unit grid over the full cross product:

| axis | values |
|---|---|
| `dry_streak` | below threshold, at threshold |
| `query_term_count` | below, at, above `starved_max_terms` |
| `active_slot_count` | below, at, above `starved_max_slots` |
| `rotate_pending` | `False`, `True` |
| `starved_candidates` | `0`, below, equal to, above `candidates` |

**Every cell asserts legacy `_starved()` and `decide_retrieval().starved`
agree**, and that `candidate_depth` matches the host's computed depth. The
`at`-threshold cells matter most: `>=` versus `>` in a relocated comparison is
the single likeliest way this goes wrong silently.

## 7. Evaluation matrix — explicit

`clean` · `vague_start` · `uncooperative` · `override_genuine` ·
`override_category` · `contradiction` · `supplementary_dev` · compat anchor ·
and all four official slices (`buying`, `browsing`, `boundary`,
`intent_override`).

**B vs A — total agreement, not statistical.** Per turn, on every set above:
`starved` 100%, `candidate_depth` 100%, plane / starvation-bypass /
funnel-versus-bypass identical. **One disagreeing turn stops 6B1.**

**C vs A — bit-exact.** `recommendations`, `message`, `ask_attribute`; score,
HR@10, MRR, MTTC; all four official slices; every scenario above; compat
anchor. Retrieval telemetry — plane, depth, pool sizes, bypass — exact. Warm
p95 delta **≤ 0.2 ms**. No new index built. **Sealed holdout not run.**

## 8. Promotion rule — pre-registered

* **All agreement, bit-exactness and performance gates pass** → the final
  default becomes **`retrieval_context_mode="control"`**. The point of 6B1 is
  real orchestration; shipping a verified controller in `off` would be
  building the thing and declining to use it.
* **Any gate fails** → default stays **`"off"`** and the phase stops. No
  tuning, no partial adoption.
* `context_shadow` stays **False** either way.

## Predictions

1. **B agrees with A on 100% of turns.** If not, the extracted rule is not the
   rule, and that is the finding.
2. **C is bit-exact.** Same rule, same inputs, same outputs.
3. Warm p95 delta **< 0.05 ms**: two frozen dataclasses and a few integer
   comparisons per turn.
4. Least confident: that `rotate_pending` is genuinely live at the insertion
   point on **every** path. It is set at 466, cleared at 523 on new evidence,
   consumed at `retrieval.py:42`. The 100% agreement gate is what would catch
   a path I have not traced.

## Not in scope

`_pick_attribute`, clarification, `ASK_STRUCTURED`, question policy — **6B2,
designed only after 6B1 is accepted.** Profile weighting untouched; per-tag
credibility (`coverage`, `overlaps_session_attribute`,
`overlaps_session_value`, `rejection_reason`) is **6C**, and 6A's single
`profile_credible` boolean is already recorded as insufficient to drive
ranking. No dense, category, reranker or profile-weight change.

**6B1 stops when measured, recorded and either promoted or halted. 6B2 does
not start automatically.**
