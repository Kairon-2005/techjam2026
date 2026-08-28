# Phase 6B2-R2 design & pre-registration — staged question context

**Written before any R2 implementation exists.** Recorded alongside the
committed benchmark harness (`lab/benchmark.py`, `lab/benchfixtures.py`,
`lab/benchweights.py`), which is committed *first* so that the numbers this
document gates on can only be produced one way.

Phase 6B2 is **NOT ADOPTED** and stays that way. R2 inherits none of its
evidence: not gate A, not gate B, not gate C, and certainly not gate D. See
`notes/32-phase6b2-results.md` and its two corrections. If R2 succeeds, 6B2 as
recorded still failed.

## What was actually wrong, and what was not

6B2's defect was **eager total-statistics construction**, not purity.
`CandidateStats` is a frozen, fully-populated object: every facet in
`ATTR_VOCAB`, both entropy variants, coverage for all of them, computed before
the decision function is entered and regardless of which branch it will take.
That is 15 passes over `cat.text` on **every** dispatch — including the
first-two-`other` branch, which needs none of them, and which is 45% of live
turns.

**A pure controller does not require every possible statistic to be calculated
in advance.** It requires that the function it is given already holds
everything it will read. Those are different demands, and 6B2 conflated them.
The fix is to compute in stages and to enter each stage only when the previous
one could not decide — with the *host* deciding when to scan, and the pure
functions still receiving nothing but bounded primitive summaries.

**What is explicitly not being tried:** relaxing purity, passing the `Agent`
or the catalog into the decision, or making the decision depend on evaluation
order. Those would trade a measurable cost for an unmeasurable one.

## Staged construction

Six stages. Each stage is a pure function over primitives; the host runs the
scans between them and only the scans the previous stage's outcome requires.

1. **`QuestionSnapshot + QuestionPolicy`.** Session facts and configuration.
   No candidate evidence. Free.
2. **Branches that need no candidate evidence**, resolved here and returned:
   `other`, `probe_cycle`, `other_then_cycle`, first-two-`other`, and the
   `ask_fallback_after` degrade. **Zero candidate scans.**
3. **Category / over-generality summary**, computed once, only if stage 2 did
   not decide. One bounded pass over `cat.cats` across
   `ranked[:max(2, pool_depth)]`.
4. **Uncertain/easier and dry give-up**, resolved from the snapshot plus that
   summary. **No facet scans.**
5. **Sparse facet statistics**, only if pool selection is still required:
   * skip facets already in `state["asked"]`;
   * compute only the entropy variant `question_utility` requires;
   * utility **ON** — entropy and coverage together, **one pass per unasked
     facet**;
   * utility **OFF** — entropy per unasked facet, and coverage for the
     **selected winner only**.
6. **The same immutable `QuestionDecision` and the same partial
   `QuestionPatch`** as 6B2 produced.

### The boundary is explicit, and is not a lazy object

There is **no** object holding an `Agent`, a catalog or a callback and
materialising statistics when an attribute is read. A lazy object would move
the scans out of sight: the cost would still be there, it would fire from
inside a function documented as pure, and no test could say which branch paid
it. Instead the *host* orchestrates:

```
decision = question_without_candidates(snapshot, policy, probe_order=...)   # stage 2
if decision is None:
    category = host.category_summary(pool, cfg)                            # 1 scan
    decision = question_from_category(snapshot, policy, category, ...)      # stage 4
if decision is None:
    plan     = facet_scan_plan(snapshot, policy, vocab=...)                 # pure
    samples  = host.facet_samples(pool, cfg, plan)                          # N scans
    picked   = select_pool_attribute(snapshot, policy, samples)             # pure
    coverage = picked.coverage if picked.coverage_known \
               else host.facet_coverage(window, picked.attribute)           # <=1 scan
    decision = question_from_pool(snapshot, policy, category, picked, coverage)
```

Every argument crossing into a staged function is a primitive, a tuple of
primitives, or a frozen dataclass of them. `facet_scan_plan` names the facets
and the entropy variant; the host performs exactly that and nothing else.

### Pre-registered scan topology

Counted per dispatch. `cat.cats` passes are the category summary; `cat.text`
passes are entropy/coverage over the window and cost ~1.1 ms each at
`pool_depth=30`.

| branch | expected candidate work | `cat.cats` | `cat.text` |
|---|---|---|---|
| non-pool / first-two-`other` | zero candidate scans | 0 | 0 |
| easier | category summary only | 1 | 0 |
| dry give-up | category summary only | 1 | 0 |
| pool, utility **ON** | category summary + one combined pass per unasked facet | 1 | *u* |
| pool, utility **OFF** | category summary + one entropy pass per unasked facet + winner coverage | 1 | *u* + 1 |

*u* = unasked facets, ≤ 5. For comparison, at *u* = 5:

| arm | `cat.text` passes, utility ON | utility OFF |
|---|---|---|
| legacy | 11 | 6 |
| 6B2 eager | 15 | 15 |
| **R2 staged** | **5** | **6** |

R2 is expected to be **cheaper than legacy** with utility ON — one combined
pass replaces legacy's separate entropy and coverage passes over the same
window, reading the same `pattern.search()` result twice instead of computing
it twice — and **equal to legacy** with utility OFF. That is a consequence of
the staging, not a target: no threshold, no ordering and no output changes.

### Frozen invariants

No change to: question thresholds, branch ordering, patch semantics (which
keys are written, and with what values), the rendered message, the score,
retrieval, the profile, or the reranker. The `_compose` config asymmetry
recorded in `notes/31-phase6b2-design.md` stays as it is — it is architecture
debt, and R2 is still a relocation.

## Gates

The 6B2 pair is **not reused**. `median ratio ≤ 1.20` and `absolute overhead ≤
0.10 ms` disagree by 12× against a ~6 ms baseline: an implementation could
satisfy either while failing the other, and which one it hit would be an
accident of the fixture. Gates are per branch class, and live in
`lab/benchreport.py:GATES` rather than only here.

| branch class | gate |
|---|---|
| no-scan (`first_two_other`, `probe_cycle`) | median absolute overhead **≤ 0.10 ms** |
| category-only (`easier`, `give_up`) | median absolute overhead **≤ 0.25 ms** |
| pool selection | median ratio **≤ 1.10** **and** absolute overhead **≤ 0.50 ms** |
| live branch-weighted aggregate | median overhead **≤ 0.50 ms** |
| hard gate | **no lazy index construction** (`_cat_index`, `_facet_index`, `_dense_index` unbuilt by the question path) |
| end-to-end p50/p95 | **diagnostic only** — the 6B1 noise floor was ≈0.452 ms |

No ratio gate on the no-scan and category-only classes: their baselines are
0.7 µs and 21 µs, and a ratio there is arithmetic on noise.

**Overheads are paired medians** — the median of the per-repetition
(pure − legacy) differences, not the difference of the two medians. Pairing is
the entire reason the arm order alternates.

### The weighted aggregate, and why its weights are already frozen

Weights are the observed selection-branch mix of the **completed 6B2 shadow
matrix** (`p6b2b-shadow`, seven scenarios, 8,483 per-seed-normalised turns),
frozen in `lab/benchweights.py` on 2026-08-28, before any R2 code existed:

| branch | share | representative fixture |
|---|---|---|
| `pool_selection` | 0.454393 | `pool_utility_on_none_asked` |
| `first_two_other` | 0.452507 | `first_two_other` |
| `give_up` | 0.082184 | `give_up` |
| `easier` | 0.010915 | `easier` |

`cycle` and `fallback` are absent because the shipped policy is
`other_then_pool` and neither fires live. They are still benchmarked; they
contribute nothing to an aggregate that claims to describe live cost.

**Weights are not recomputed after seeing R2 performance.** A branch-weighted
aggregate whose weights are chosen after the per-branch numbers are known is a
knob, not a measurement: the branch that looks worst can always be weighted
into irrelevance. `benchweights.derive()` recomputes them from the ledger so
the constants can be *checked*, never so the gate can be *re-formed*.

## Benchmark protocol

Committed harness, one CLI line, `lab/benchmarks.jsonl`, same
`lab.provenance.citable()` predicate as the score ledger.

* **1,000 warm-ups, 10,000 measured complete dispatches** per arm per fixture
  per repetition.
* **Seven fresh-process paired repetitions**, arm order alternating with the
  repetition index.
* Every repetition in its **own interpreter**; one JSON row appended and
  fsynced after each.
* **15-minute timeout per repetition.** On timeout the partial record is
  retained and marked non-citable; it is never silently dropped.
* At **2× the child's own projected duration**, the parent captures the child's
  PID state, CPU%, CPU time and RSS. 6B2's four-hour stall produced no such
  record and therefore has, and will always have, no explanation.
* A repetition whose two arms disagree, or whose fixture leaves its registered
  branch, is rejected — a speed comparison between implementations that
  disagree measures nothing.

**Fixtures** (`lab/benchfixtures.py`, hash recorded per row):
`first_two_other`, `probe_cycle`, `easier`, `give_up`,
`pool_utility_on_none_asked`, `pool_utility_off_none_asked`,
`pool_utility_on_three_asked`, `pool_utility_off_three_asked`, `pool_empty`,
`pool_one_item`. That covers the full 30-item window with no facets asked, the
full window with several already asked, both utility settings, and the empty
and one-item pools.

**A median over fewer repetitions than were registered is not the registered
statistic.** `benchreport.aggregate()` returns `sufficient=False` and the
report prints the table under a DIAGNOSTIC heading. Seven means seven.

## Staged acceptance

Ordered to spend the cheap evidence first. Each step's failure stops R2 where
it stands.

1. **Unit tests and the 4,320-cell equivalence grid**, re-run against the
   staged implementation. Not inherited from 6B2 — the grid compares against
   the legacy controller, and the thing being compared has changed.
2. **Three-repetition feasibility screen**, final committed harness, **1,000
   measured dispatches** (a tenth of the gate protocol — enough to see a
   branch miss its gate by 20%, a tenth of the cost). Explicitly a screen:
   it can say "keep going", never "passed".
3. **If any branch exceeds its gate by more than 20% → stop R2 immediately.**
   No scenario matrix, no shadow comparison, no seven repetitions.
4. **If the screen passes → all seven repetitions** at the full protocol.
5. **Then the complete pre-adoption comparison**: seven scenarios, the
   8,483-turn shadow comparison, all four official slices, compat anchor
   `0.928708`.
6. **Zero disagreements and bit-exact score/output.** Any single disagreement
   stops R2.
7. **Adoption compares against the measured R2 control implementation**, not
   only against an older baseline.

## Outcomes

**If R2 passes every gate:** delete the eager duplicate body *and* the legacy
`_pick_attribute` / `_pool_attribute` rule bodies, leave one implementation
behind one adapter, default `question_context_mode="control"`, record adoption.
Post-adoption shadow agreement is tautological and is not evidence.

**If R2 fails any gate:** 6B2 control is **permanently abandoned for this
submission**. The unused pure implementation is *removed* rather than carried
as duplicate dead code — this project has already paid twice for inert code
kept "in case" (the void `suppress_abandoned` switch, the inert
`route_overrides` patch). The design notes, the results notes, the equivalence
grid, the write-tracking oracle, the negative controls, the shadow comparator
and the benchmark harness are all retained: they are useful independent of the
outcome. Then Phase 6C.

## Stop conditions

Stop immediately if the relocation requires changing behaviour or a threshold;
if a staged function acquires a callback into the host; if shadow execution
mutates state; if any comparison gate shows even one disagreement; or if the
feasibility screen blows a gate by more than 20%.

**No threshold tuning and no score optimisation in R2.** If the staged
implementation is faster than legacy, that is reported as a consequence of
doing less work, and nothing downstream is re-tuned to spend it.

## Predictions

1. **Attribute, `selection_mode`, write set and rendered message agree on 100%
   of turns.** R2 is a re-ordering of when work happens, not of what is
   decided.
2. **The pool branches get faster, not merely no slower** — 5 combined text
   passes against legacy's 11 with utility ON. If R2 comes out *slower* than
   legacy on `pool_utility_on_none_asked`, the staging is not doing what this
   document says it does, and that is a defect to find rather than a gate to
   pass on a technicality.
3. **The no-scan branches are where a staging bug would hide.** If stage 2
   ever fails to return early, the branch still produces the right answer and
   only the clock says so — which is exactly why `first_two_other` has its own
   fixture and its own 0.10 ms gate, and why it carries 45% of the weighted
   aggregate.
4. **Utility OFF is the case with no headroom.** R2 does the same 6 passes as
   legacy there, so its overhead is pure orchestration: dataclass construction,
   the scan plan, and one extra function call boundary. If the ≤1.10 ratio gate
   fails anywhere, it fails there first.
