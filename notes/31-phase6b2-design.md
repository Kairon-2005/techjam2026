# Phase 6B2 design & pre-registration — clarification decision extraction

**For review before implementation. No code written.** Recorded at `3a6b09d`,
264 tests passing, `retrieval_context_mode="control"`, `context_shadow=False`,
dense OFF.

Objective: extract `_pick_attribute()` and `_pool_attribute()` into an explicit
context-programmed controller **without changing customer-visible behaviour,
score, thresholds or question policy.** This is a relocation. Any movement is a
defect.

## Two reporting constraints carried forward, permanently

1. **The 6B1 end-to-end p95 gate was inconclusive, not passed.** Only "no
   measurable regression" may be claimed, and the direct microbenchmark is not
   proof that the end-to-end gate resolved. 6B2 does not reuse that gate — see
   §D.
2. **Post-adoption shadow agreement is tautological**, because the adapter
   delegates to the same function. The valid 6B1 relocation evidence is the
   **pre-adoption 8,483-turn comparison**. The same trap applies here: 6B2's
   shadow evidence is only meaningful **before** adoption.

## The hard part: mutations are asymmetric, and staleness is load-bearing

`_pick_attribute` does not write the same fields on every branch, and
`_compose` reads two of those fields. A value left over from a previous turn
therefore **changes the rendered message**. Enumerated from source:

| branch | `broad_options` | `last_bits` | `last_coverage` | `last_weighed` |
|---|---|---|---|---|
| `other_then_pool`, first-two-`other` gate | — | — | — | — |
| pool path, before any branch | **set** | — | — | — |
| uncertain/easier branch | (already set) | **0.0** | — | — |
| dry give-up branch | (already set) | — | — | — |
| pool selection | (already set) | **bits** | **set** | **set** |
| `other` / `probe_cycle` / `other_then_cycle` | — | — | — | — |

`_compose` reads `broad_options` and `last_bits`. So on a turn where legacy
writes neither, the message is rendered from the **previous** turn's values.

**Consequence for the design: `QuestionDecision.state_patch` must be a
PARTIAL patch containing only the keys the matching legacy branch writes.** A
patch that always wrote all four would be "obviously equivalent" and would
change the rendered message on later turns. This is the single likeliest way
6B2 breaks, and the shadow gate compares patches key-by-key for exactly that
reason.

Also mutated, outside these functions: `state["asked"].append(attribute)` in
`respond()`, which stays in the host.

## Inputs — three, kept apart

```python
@dataclass(frozen=True)
class QuestionSnapshot:      # session facts only
    asked: tuple[str, ...]
    other_asked_count: int
    dry_streak: int
    dry_others: int
    uncertain_streak: int

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
class FacetStat:             # one attribute, over the ranked window
    attribute: str
    bits_with_missing: float
    bits_skip_missing: float
    coverage: float
    answerability: float

@dataclass(frozen=True)
class CandidateStats:
    window_size: int
    facets: tuple[FacetStat, ...]          # <= len(ATTR_VOCAB) = 5
    overgeneral: bool
    options: tuple[str, ...]               # <= 3
```

`CandidateStats` is computed by a bounded helper over `ranked[:pool_depth]`
and the already-resident `cat.text` / `cat.cats` mappings — **the same
constraint as 6A's `summarize_categories`: no `CategoryIndex`, no
`FacetIndex`, no `DenseIndex`, no catalog-wide scan.** Cost is bounded at
5 attributes × 30 candidates for entropy and coverage, plus one pass for the
category leaves.

`decide_question(snapshot, policy, stats) -> QuestionDecision` receives **no
`Agent`, no session dict, no catalog, no lazy index.**

## Output

```python
@dataclass(frozen=True)
class QuestionDecision:
    attribute: str                      # or "other"
    mode: str                           # open | structured | easier | cycle | give_up
    options: tuple[str, ...]
    bits: float
    coverage: float
    weighted_value: float
    reasons: tuple[QuestionReason, ...]
    state_patch: Mapping[str, object]   # PARTIAL, see above
```

### Action reason codes — explicit priority, not source order

6A's `decide()` appended codes in source order and let later branches
overwrite the mode; notes/28 records that as inadequate for control. Here each
branch emits exactly one **primary** code, and the branch that fires is
determined by the precedence table below:

`FIRST_TWO_OTHER` · `EASIER_AFTER_UNCERTAIN` · `GIVE_UP_AFTER_DRY` ·
`STRUCTURED_CLARIFICATION_DUE` · `POOL_ATTRIBUTE_SELECTED` ·
`NO_DISCRIMINATING_FACET` · `PROBE_CYCLE` · `FALLBACK_OTHER`

`STRUCTURED_CLARIFICATION_DUE` is **new and distinct** from 6A's
`ASK_STRUCTURED`, which stays a historical observation code and is not reused
to drive behaviour. It is emitted only when over-generality is detected **and**
the branch that fires actually renders a structured question — eligibility and
final mode both, which is the gap notes/28 identified.

## Precedence truth table

Order is fixed and reproduces the legacy `if` chain exactly.

| # | condition | attribute | mode | patch keys | reason |
|---|---|---|---|---|---|
| 1 | `ask_policy` ∈ {`pool`,`other_then_pool`}, `other_then_pool` and `other_asked_count < 2` | `other` | open | **none** | `FIRST_TWO_OTHER` |
| 2 | pool path, `uncertain_streak >= answerability_after`, an easier facet exists | easiest unasked | easier | `broad_options`, `last_bits=0.0` | `EASIER_AFTER_UNCERTAIN` |
| 3 | pool path, **not** overgeneral, `dry_streak >= pool_give_up_after` | `other` | give_up | `broad_options` | `GIVE_UP_AFTER_DRY` |
| 4 | pool path, selection yields `bits >= 0.2` | selected | structured if overgeneral else open | `broad_options`, `last_bits`, `last_coverage`, `last_weighed` | `POOL_ATTRIBUTE_SELECTED` (+ `STRUCTURED_CLARIFICATION_DUE` if overgeneral) |
| 5 | pool path, `bits < 0.2` | `other` | open | as row 4 | `NO_DISCRIMINATING_FACET` |
| 6 | `ask_policy == "other"`, `ask_fallback_after` set, `dry_others >= limit` | first unasked in `PROBE_ORDER[:-1]` | cycle | none | `PROBE_CYCLE` |
| 7 | `ask_policy == "probe_cycle"` | first unasked in `PROBE_ORDER` | cycle | none | `PROBE_CYCLE` |
| 8 | `ask_policy == "other_then_cycle"`, `other_asked_count < 2` | `other` | open | none | `FIRST_TWO_OTHER` |
| 9 | `ask_policy == "other_then_cycle"`, otherwise | first unasked in `PROBE_ORDER` | cycle | none | `PROBE_CYCLE` |
| 10 | otherwise | `other` | open | none | `FALLBACK_OTHER` |

**Row 2 beats over-generality.** The uncertain/easier branch is checked before
the give-up guard and before selection, so an over-general pool does not
override it — that is the legacy order and it is preserved, not improved.

**Row 3 is suppressed by over-generality**, because legacy guards it with
`if not broad`. An over-general pool is exactly when a targeted question pays.

**Rows 1, 6–10 write nothing at all.** Their `state_patch` is empty, and the
message is rendered from whatever previous turn left behind.

Boundary values to be tested at exactly the threshold: `other_asked_count` ∈
{1, 2}; `uncertain_streak` ∈ {`answerability_after` − 1, `answerability_after`};
`dry_streak` ∈ {`pool_give_up_after` − 1, `pool_give_up_after`}; `dry_others` ∈
{`limit` − 1, `limit`}; `bits` ∈ {0.199, 0.2, 0.201}; `overgeneral_cats`
crossing the category count.

## `question_context_mode = "off" | "shadow" | "control"`

| mode | pure decision | who controls |
|---|---|---|
| `off` | not computed | legacy |
| `shadow` | computed, **no mutation** | legacy |
| `control` | computed | pure decision; host applies `state_patch` |

**`control` must work with `trace=False`.** Telemetry may depend on `trace`;
orchestration may not.

In `shadow`, the pure path must not write to the session. The stop condition is
absolute: if shadow execution mutates state, stop.

## Acceptance gates

**A — pure-function correctness.** Exhaustive boundary grid against the legacy
implementation across all five ask policies and every threshold value above;
no input mutation (inputs canonicalised either side of a call); deterministic
for identical snapshot/policy/stats; **no lazy index construction** — a test
starts with `_cat_index`, `_facet_index` and `_dense_index` all `None` and
requires them still `None`; reason priority explicit, asserted by a test that
a lower-priority code never appears when a higher-priority branch fires.

**B — shadow agreement.** Across `clean`, `vague_start`, `uncooperative`,
`override_genuine`, `override_category`, `contradiction`,
`supplementary_dev`: **zero** disagreements on selected attribute, mode,
structured options, **state patch (key-by-key, including absent keys)**, and
rendered message. Reported as **raw counts and compared turns**, never rounded
rates.

**C — behaviour preservation.** Control vs legacy bit-exact on aggregate
score, HR@10, MRR, MTTC, all four official slices, compat anchor `0.928708`,
recommendations, `ask_attribute`, and rendered message. Any movement is a
defect; **no tuning around it.**

**D — performance.** The 6B1 end-to-end noise floor was ≈0.452 ms, so a
0.2 ms end-to-end gate is unmeasurable and **is not reused**.

* **Primary gate:** a repeated, paired direct benchmark of the *complete*
  question dispatch — stats computation plus decision plus patch application —
  legacy against pure, reported as median and range over repetitions. 6B1
  showed a single microbenchmark can silently describe half the path, so the
  benchmark must exercise the whole dispatch and the call count must be
  asserted.
* **Diagnostic only:** end-to-end p50/p95, explicitly not a gate.
* **Hard gate:** no lazy index construction, and no cold-start regression.

**E — migration.** Two commits. (1) Measurement: legacy and pure side by side,
duplication explicitly temporary. (2) Adoption: delete the legacy rule body,
leave one adapter. The adoption anchor compares against the **measured control
implementation**, not only an older baseline.

Every experiment through the lease and the results ledger; aborted or
defective rows marked non-citable through the append-only invalidation ledger;
**results are never rewritten.**

## Stop conditions

Stop immediately if extraction requires changing behaviour or a threshold; if
shadow execution mutates state; or if any comparison gate shows **even one**
disagreement.

## Predictions

1. Shadow agrees on 100% of turns for attribute and mode.
2. **The state patch is where this breaks if it breaks** — specifically rows
   1 and 6–10, which write nothing, and row 2, which writes `last_bits` but
   not `last_coverage`. An implementation that "tidies" those into a uniform
   patch would pass attribute and mode agreement and fail message agreement.
3. Complete-dispatch cost within ±20% of legacy; the same work, differently
   arranged.
4. Least confident: that `_compose`'s dependence on stale `broad_options` and
   `last_bits` is fully captured by the patch model. The rendered-message
   comparison in gate B is the instrument for that doubt.

## Not in scope

Personalization, profile weights, dense retrieval, reranking, category logic,
scoring parameters. **Phase 6C** (profile credibility shadow evaluation)
follows 6B2, then the score-oriented reranker phase.
