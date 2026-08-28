# Phase 6B2 design & pre-registration — clarification decision extraction

**Revision 2. For review before implementation. No code written.** Recorded at
`638e2f3`, 264 tests passing, `retrieval_context_mode="control"`,
`context_shadow=False`, dense OFF.

Revision 2 fixes ten defects found in review. The largest: revision 1's single
`mode` field **cannot describe the customer-visible question at all** in the
case below. Every claim here was re-verified against source, with line numbers.

Objective: extract `_pick_attribute()` and `_pool_attribute()` into an explicit
context-programmed controller **without changing customer-visible behaviour,
score, thresholds or question policy.** This is a relocation. Any movement is a
defect.

## Two permanent reporting constraints

1. **The 6B1 end-to-end p95 gate was inconclusive, not passed.** Only "no
   measurable regression" may be claimed, and the direct microbenchmark is not
   proof the end-to-end gate resolved. 6B2 does not reuse it — see §D.
2. **Post-adoption shadow agreement is tautological**, because the adapter
   delegates to the same function. The valid 6B1 evidence is the pre-adoption
   8,483-turn comparison. **The same applies here: 6B2's shadow evidence counts
   only before adoption.**

## Mutations are asymmetric, and staleness is load-bearing

`_pick_attribute` writes different fields on different branches, and
`_compose` reads two of them, so a value from a previous turn changes the
rendered message. From source:

| branch | `broad_options` | `last_bits` | `last_coverage` | `last_weighed` |
|---|---|---|---|---|
| `other_then_pool`, first-two-`other` (line 284) | — | — | — | — |
| pool path, before any branch (line 290) | **written** | — | — | — |
| uncertain/easier (296–297) | (already written) | **0.0** | — | — |
| dry give-up (305) | (already written) | — | — | — |
| pool selection (307–310) | (already written) | **bits** | **written** | **written** |
| `other` / `probe_cycle` / `other_then_cycle` | — | — | — | — |

`state["asked"].append(attribute)` happens in `respond()` and stays in the host.

## Selection and rendering are different questions

**Canonical counterexample, verified at `dialogue.py:289-298`:**

```
pool is over-general           -> state["broad_options"] = options   (line 290)
uncertain_streak >= threshold  -> easiest attribute exists           (line 294)
                               -> last_bits = 0.0, return easy       (296-297)
```

`broad_options` is written **before** the uncertain branch is tested, so
selection takes the *easier* path while `_compose` — checking
`len(options) >= 2` at line 417 — renders the *structured* message. Both are
true simultaneously and one field cannot hold them.

```python
@dataclass(frozen=True)
class QuestionDecision:
    attribute: str                       # or "other"
    selection_mode: str                  # branch that selected it
    render_mode: str                     # open | structured, AFTER the patch
    effective_options: tuple[str, ...]   # what _compose will actually see
    bits: float
    coverage: float
    weighted_value: float
    primary_reason: QuestionReason
    modifiers: tuple[QuestionModifier, ...]
    patch: QuestionPatch
```

`STRUCTURED_CLARIFICATION_DUE` is a **modifier**, not a competing primary
reason: in the counterexample the primary reason is `EASIER_AFTER_UNCERTAIN`
and structured rendering is a consequence of inherited state, not of the
branch that fired.

### The structured condition is `len(effective_options) >= 2`

Not `overgeneral`. `_compose` needs two options (line 417), so `overgeneral`
with one distinguishing option renders **open**. Test: overgeneral with one
effective option; two or more current options; **stale** two-or-more options
surviving a no-write branch; and a patch that clears stale options.

## Prior render state, and the effective-state operation

```python
@dataclass(frozen=True)
class PriorRenderState:
    broad_options: tuple[str, ...]
    last_bits: float
    last_coverage: float
    last_weighed: bool

def apply_patch(prior: PriorRenderState, patch: QuestionPatch) -> PriorRenderState
```

`render_mode`, `effective_options` and all render telemetry derive from
`apply_patch(prior, patch)` — never from the patch alone, never from the prior
alone.

## Inputs — four, kept apart

```python
@dataclass(frozen=True)
class QuestionSnapshot:      # session facts only
    asked: tuple[str, ...]
    dry_streak: int
    dry_others: int
    uncertain_streak: int
    prior: PriorRenderState

@dataclass(frozen=True)
class QuestionPolicy:        # configuration only
    ask_policy: str
    ask_fallback_after: int
    answerability_after: int
    pool_give_up_after: int
    pool_depth: int
    overgeneral_cats: int
    question_utility: bool
    question_dry_cost: float

@dataclass(frozen=True)
class FacetStat:
    attribute: str
    bits_with_missing: float
    bits_skip_missing: float
    coverage: float
    answerability: float

@dataclass(frozen=True)
class CandidateStats:
    window_size: int
    facets: tuple[FacetStat, ...]        # ATTR_VOCAB order, <= 5
    overgeneral: bool
    options: tuple[str, ...]             # <= 3
```

`other_asked_count` is **removed**: both call sites use
`state["asked"].count("other")` (lines 284, 313) and there is no separate
source of truth, so carrying it would create one.

`decide_question(snapshot, policy, stats) -> QuestionDecision` receives **no
`Agent`, no session dict, no catalog, no lazy index.**

## The patch representation — immutable and presence-marked

`Mapping[str, object]` cannot distinguish *not written* from *written with the
same value*, and the legacy oracle depends on that distinction.

```python
_UNSET = object()

@dataclass(frozen=True)
class QuestionPatch:
    broad_options: tuple[str, ...] | _UNSET
    last_bits: float | _UNSET
    last_coverage: float | _UNSET
    last_weighed: bool | _UNSET

    def writes(self) -> tuple[str, ...]      # fixed order, allowlisted
```

Three states are distinguishable: **not written** (`_UNSET`), **written with a
value**, and **written with the value it already held** — the last being
indistinguishable from the first under a before/after diff.

## The legacy patch oracle

A before/after dict diff **cannot observe a same-value write**, so it is not an
acceptable oracle. The measurement commit instruments the legacy path with a
**write-tracking mapping** that records every `__setitem__` against the
allowlist, capturing the exact keys `_pick_attribute` / `_pool_attribute`
assign, in order, with their values.

Real-turn shadow validation compares **five things separately**:

1. write set (which keys)
2. written values
3. effective post-patch render state
4. selected attribute
5. rendered message

## Candidate window contract

Both `_overgeneral` and `_pool_attribute` use **`ranked[:max(2, int(pool_depth))]`**
— *not* `ranked[:pool_depth]`, which revision 1 wrote. Boundary cases:
`pool_depth ∈ {0, 1, 2, 30}`, each against an empty pool, a one-item pool, and
a full pool.

## Ordering and numerical semantics — pre-registered tests

* `ATTR_VOCAB` **insertion order** is preserved in `CandidateStats.facets`.
* Equal utility keeps the **first** attribute: legacy uses `util > best_util`,
  strictly greater (line 309).
* Equal category counts preserve **first-seen pool order** — `sorted` is stable
  over insertion-ordered `counts`.
* Entropy with the missing bucket: exact reproduction, empty string counted as
  a value.
* `skip_missing=True`: exact reproduction, unmatched products excluded entirely.
* Coverage rounds to **four decimals**, matching `_facet_coverage`.
* When selection yields `"other"`, `_pool_attribute` still runs and writes
  **`last_coverage = 0.0`**, because `ATTR_VOCAB.get("other")` is `None`.

## Config asymmetry — preserved, and recorded as debt

Selection uses route-resolved `turn_cfg` (`dialogue.py:280`), while `_compose`
reads **base** `self.cfg["pool_depth"]` (line 422). **This is not corrected
here.** A test gives a route override a different `pool_depth` from base config
and requires the rendered message to stay bit-exact.

Recorded as **existing architecture debt, outside 6B2 scope.** Fixing it during
a relocation would change behaviour under exactly the configuration a reviewer
would least expect it.

## Precedence truth table

Reproduces the legacy `if` chain exactly. `patch` column lists written keys.

| # | condition | attribute | selection_mode | patch | primary reason |
|---|---|---|---|---|---|
| 1 | pool policy, `other_then_pool`, `asked.count("other") < 2` | `other` | first_two_other | **none** | `FIRST_TWO_OTHER` |
| 2 | pool path, `uncertain_streak >= answerability_after`, easier facet exists | easiest unasked | easier | `broad_options`, `last_bits=0.0` | `EASIER_AFTER_UNCERTAIN` |
| 3 | pool path, **not** overgeneral, `dry_streak >= pool_give_up_after` | `other` | give_up | `broad_options` | `GIVE_UP_AFTER_DRY` |
| 4 | pool path, `bits >= 0.2` | selected | pool_selection | all four | `POOL_ATTRIBUTE_SELECTED` |
| 5 | pool path, `bits < 0.2` | `other` | pool_selection | all four | `NO_DISCRIMINATING_FACET` |
| 6 | `ask_policy=="other"`, `ask_fallback_after` set, `dry_others >= limit` | first unasked in `PROBE_ORDER[:-1]` | cycle | none | `PROBE_CYCLE` |
| 7 | `ask_policy=="probe_cycle"` | first unasked in `PROBE_ORDER` | cycle | none | `PROBE_CYCLE` |
| 8 | `ask_policy=="other_then_cycle"`, `asked.count("other") < 2` | `other` | first_two_other | none | `FIRST_TWO_OTHER` |
| 9 | `ask_policy=="other_then_cycle"`, otherwise | first unasked in `PROBE_ORDER` | cycle | none | `PROBE_CYCLE` |
| 10 | otherwise | `other` | fallback | none | `FALLBACK_OTHER` |

`render_mode` is **not** a column: it is derived per row from
`apply_patch(prior, patch)`, and rows 1 and 6–10 can render structured purely
from inherited state.

Row 2 **beats** over-generality; row 3 is **suppressed** by it. Both are the
legacy order, preserved rather than improved.

Threshold values to test exactly: `asked.count("other") ∈ {1,2}`;
`uncertain_streak ∈ {t-1, t}`; `dry_streak ∈ {t-1, t}`;
`dry_others ∈ {limit-1, limit}`; `bits ∈ {0.199, 0.2, 0.201}`;
`overgeneral_cats` crossing the category count; all five ask policies.

## `question_context_mode = "off" | "shadow" | "control"`

| mode | pure decision | who controls |
|---|---|---|
| `off` | not computed | legacy |
| `shadow` | computed, **no mutation** | legacy |
| `control` | computed | pure decision; host applies the patch |

**`control` must work with `trace=False`.**

## Acceptance gates

**A — pure-function correctness.** Exhaustive boundary grid against legacy
across all five policies, every threshold, and the window cases; no input
mutation; deterministic for identical inputs; **no lazy index construction**
(`_cat_index`, `_facet_index`, `_dense_index` all `None` before and after);
reason priority explicit, with a test that a lower-priority code never appears
when a higher-priority branch fires.

**B — shadow agreement.** Across `clean`, `vague_start`, `uncooperative`,
`override_genuine`, `override_category`, `contradiction`,
`supplementary_dev`: **zero** disagreements on write set, written values,
effective post-patch render state, selected attribute, and rendered message.
**Raw counts and compared turns, never rounded rates.**

**C — behaviour preservation.** Control vs legacy bit-exact on score, HR@10,
MRR, MTTC, all four official slices, compat anchor `0.928708`,
recommendations, `ask_attribute`, rendered message. Any movement is a defect;
**no tuning around it.**

**D — performance, executable and pre-registered now.** The 6B1 end-to-end
noise floor was ≈0.452 ms, so a 0.2 ms end-to-end gate is unmeasurable and is
not reused.

* **≥ 7 paired repetitions**, fixed warm-up and iteration counts, **alternating
  legacy-first / pure-first** order to cancel drift.
* Benchmark the **complete dispatch**: stats + decision + patch application.
* **Exactly one decision execution per control turn**, asserted by call count.
* **Median pure/legacy ratio ≤ 1.20.**
* **Absolute median overhead ≤ 0.10 ms per turn.**
* End-to-end p50/p95 **diagnostic only**.
* **Hard gate:** no new lazy-index construction.

**E — migration.** Two commits: (1) measurement, legacy and pure side by side,
duplication explicitly temporary; (2) adoption, deleting the legacy rule body
and leaving one adapter. The adoption anchor compares against the **measured
control implementation**, not only an older baseline.

Every experiment through the lease and ledger; aborted or defective rows marked
non-citable via the append-only invalidation ledger; **results never
rewritten.**

## Promotion and post-adoption semantics

* **All correctness gates zero-disagreement and performance gates pass** →
  adopt one implementation, default `question_context_mode="control"`.
* **Any gate fails** → default stays `"off"`, no adoption, stop for review.
* **After adoption, `off` means adapter plus no orchestration telemetry.** It
  is no longer an independent legacy implementation, and must not be described
  as one.
* **Post-adoption shadow comparison is tautological** and cannot be quoted as
  evidence.

## Stop conditions

Stop immediately if extraction requires changing behaviour or a threshold; if
shadow execution mutates state; or if any comparison gate shows **even one**
disagreement.

## Predictions

1. Attribute and `selection_mode` agree on 100% of turns.
2. **`render_mode` and the rendered message are where this breaks if it
   breaks** — rows 1 and 6–10 write nothing, and row 2 writes `last_bits` but
   not `last_coverage`. An implementation that tidied those into a uniform
   patch would pass attribute agreement and fail message agreement.
3. Complete-dispatch ratio within 1.20; the same work, rearranged.
4. Least confident: that the write-tracking oracle captures every legacy write
   path, including any same-value write. That is why the oracle instruments
   assignment rather than diffing before and after.

## Not in scope

Personalization, profile weights, dense retrieval, reranking, category logic,
scoring parameters, and the `pool_depth` config asymmetry. **Phase 6C**
(profile credibility shadow evaluation) follows, then the score-oriented
reranker phase.
