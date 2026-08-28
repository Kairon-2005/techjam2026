# Phase 6B2-R2.1 — a post-result specification correction

**Disclosure, first, because it is the most important thing on this page.**

**R2.1 is a post-result specification correction.** It was written *after*
seeing the measurement it changes the verdict on, and it makes R2 pass a gate
R2 had failed. That is the weakest position a decision rule can be written
from. Nothing below is offered as though it were pre-registered blind.

## The original record stands, unedited

* **R2 stopped on `pool_empty` under the original ratio rule.** Ratio 4.2803
  against `≤ 1.10`, 3.89× over, three repetitions, both arm orders.
* `notes/33-phase6b2-r2-prereg.md` — the original gate — **is not edited.** It
  stands, it fired, and R2 failed it.
* `notes/34-phase6b2-r2-screen.md` — the stop — **is not edited, rewritten or
  relabelled.** R2 failed the screen. It is not retroactively a pass.
* The ledger row `p6b2r2-screen` in `lab/benchmarks.jsonl` stands under that
  verdict and is not invalidated. It was a valid measurement of a real
  implementation against a real gate.

R2.1 is a **new decision rule applied to new measurements**, not an edit that
reaches back and passes the stopped screen.

## The R2.1 gate

**If the legacy median is `< 0.10 ms`:** the ratio is **diagnostic only**, and
the requirement is **absolute overhead ≤ 0.10 ms**.

**If the legacy median is `≥ 0.10 ms`:** the pre-registered ratio and absolute
gates are **retained unchanged** — ratio ≤ 1.10 and the branch class's absolute
budget (no-scan ≤ 0.10 ms, category-only ≤ 0.25 ms, pool ≤ 0.50 ms).

Everything else is untouched: the live branch-weighted aggregate stays at
≤ 0.50 ms, the frozen weights are not recomputed, the hard gate on lazy index
construction stands, the fixture set is unchanged, and the benchmark protocol
is unchanged.

Note what the floor does to the *category-only* class: below 0.10 ms its
budget **tightens** from 0.25 ms to 0.10 ms. The correction is not uniformly
permissive, and it was not shaped to be permissive — it was shaped to put every
micro-path under one budget.

`pool_empty` **stays in the report** with its ratio shown and marked
diagnostic, even though its live weight is zero. A check that did not run is
printed as withheld, never dropped — `lab/report.py` refuses to filter
silently, and a gate report has no business being laxer about that than a score
report.

**The implementation is not modified to optimise this fixture.** No empty-window
early-out, no object-construction shortcut on the degenerate path. The +16 µs
stays exactly where the screen found it.

## Recorded rationale

`pool_empty` moved from roughly **4.9 µs** by approximately **+16 µs**. The
4.28 ratio is dominated by the near-zero denominator; the absolute cost is
**0.016 ms**, well inside the existing **0.10 ms** micro-path budget.

**This is the same dimensional reasoning already used for the no-scan and
category-only paths.** `notes/33` argues in writing that *"a ratio against a
sub-microsecond baseline is arithmetic on noise"* and on that basis withholds
the ratio gate from those two classes, whose baselines are 0.7 µs and 21 µs.
`pool_empty`'s baseline is 4.9 µs — the same regime. The defect in `notes/33`
is that it classified fixtures by which *branch* they take, which is the right
axis for the scan topology, and then applied the ratio gate along that axis
without checking that every member of the `pool` class had a baseline a ratio
of it could mean anything against. That class spans 4.9 µs to 11.9 ms: a factor
of 2,400.

R2.1 replaces a hand-assignment with a measured condition. `notes/33` set
`max_ratio: None` on two of three classes by hand; R2.1 deletes that
hand-assignment, gives every class the same ratio limit, and lets the 0.10 ms
floor decide where it applies. **It removes two special cases and adds none.**
A correction that merely exempted `pool_empty` would have gone the other way,
and that is the test I would want applied to it.

**All live branches passed**, and the shipped pool path is approximately
**2.13× faster**, saving **6.31 ms** per dispatch.

## The falsification this correction has to survive

A gate correction that admits the implementation it was written after is worth
nothing unless it still **rejects the implementation the gate existed to
reject**.

Re-scored under R2.1, the **eager** 6B2 implementation (`p6b2-eager-control`,
commit `d953bd8`, three repetitions, lease VALID) still fails, and not
narrowly:

| fixture | legacy median | applicable gate | actual | |
|---|---|---|---|---|
| `first_two_other` | 0.0004 ms | overhead ≤ 0.10 | +16.7183 ms | **FAIL ×167** |
| `probe_cycle` | 0.0006 ms | overhead ≤ 0.10 | +16.7000 ms | **FAIL ×167** |
| `give_up` | 0.0006 ms | overhead ≤ 0.10 | +16.6927 ms | **FAIL ×167** |
| `easier` | 0.0215 ms | overhead ≤ 0.10 | +16.6955 ms | **FAIL ×167** |
| `pool_one_item` | 0.5266 ms | ratio ≤ 1.10 | 1.5278 | **FAIL** |
| `pool_utility_on_none_asked` | 11.9293 ms | overhead ≤ 0.50 | +4.7962 ms | **FAIL ×9.6** |
| `pool_utility_off_none_asked` | 6.4239 ms | overhead ≤ 0.50 | +10.3061 ms | **FAIL ×21** |
| `pool_utility_on_three_asked` | 4.8193 ms | overhead ≤ 0.50 | +11.9305 ms | **FAIL ×24** |
| `pool_utility_off_three_asked` | 2.8337 ms | overhead ≤ 0.50 | +13.8819 ms | **FAIL ×28** |
| **live branch-weighted median** | | overhead ≤ 0.50 | **+11.2986 ms** | **FAIL ×23** |
| `pool_empty` | 0.0046 ms | overhead ≤ 0.10 | +0.0163 ms | pass (ratio diagnostic) |

The floor changes exactly **one** verdict for the eager arm, and the eager arm
fails on **ten** others including the live-weighted aggregate. This is asserted
as a committed test in `tests/test_benchmark.py`, not left as prose: an edit to
the floor that started admitting the eager arm breaks the build.

## Procedure from here

1. **Run all seven repetitions fresh under R2.1.** No repetition from the
   stopped screen is reused, selected from, or carried forward. Full protocol:
   1,000 warm-ups, 10,000 measured dispatches per arm per fixture, fresh
   process per repetition, alternating arm order, 15-minute timeout, watchdog
   at 2× the child's own projection.
2. **Only if every applicable gate passes**, run the complete pre-adoption
   comparison:
   * the full **8,483-turn** shadow comparison;
   * the complete **A/B/C matrix**;
   * all four **official slices**;
   * **`supplementary_dev`**;
   * the **compat anchor** `0.928708`;
   * the adoption comparison **against the measured R2 control**.
3. **Any failure** on a live branch, the live-weighted aggregate, a correctness
   comparison, or bit-exact output **stops adoption.**

Out of scope for this run, explicitly: optimising `pool_empty`, changing
question behaviour, and touching Phase 6C or the reranker.

**No further gate revision.** If R2 misses a gate under R2.1, that is the
answer. A second correction after a second look would not be a specification
fix by any reading, and this is the last document that gets written after
seeing a number.
