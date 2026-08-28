# Phase 6B2-R2 results — staged question context

Implementation `027eb6f`, gate revision R2.1 `2908a8b`, adoption `669e303`.
Harness `ba85207`. All comparison rows leased, isolated, `matrix_complete`,
`citable()`.

## Verdict: **ADOPTED.** `question_context_mode` defaults to `"control"`.

| gate | result | evidence |
|---|---|---|
| A — pure-function correctness | **PASS** | 4,320-cell grid, 362 tests |
| B — shadow agreement | **PASS**, 18,597 raw turn-comparisons, zero disagreements | `p6b2r2-shadow`, 7/7 citable |
| C — behaviour preservation | **PASS**, bit-exact | 29 cells, four leases, all valid |
| D — performance (R2.1) | **PASS**, 7/7 repetitions | `p6b2r2-perf-2` |
| hard gate — no lazy index construction | **PASS** | now a test, not an inspection |

## Phase status — three distinct verdicts, not one

These get conflated, so they are stated separately and should be quoted
separately:

| subject | status |
|---|---|
| **Phase 6B2, original eager design** (`CandidateStats`, commit `7e66115`) | **REJECTED / NOT ADOPTED** |
| **Phase 6B2-R2, staged design** (commit `027eb6f`, adopted `669e303`) | **ADOPTED** |
| **Phase 6B2 overall** | **CLOSED THROUGH R2** |

"Closed through R2" means: the phase's objective — relocating the clarification
decision into an explicitly-programmed controller without changing behaviour —
was met, by R2, on R2's own evidence. It does **not** mean the eager design was
retroactively accepted, and it does **not** mean R2 inherited 6B2's gates. R2
re-ran A, B and C from scratch against the staged implementation and measured D
on a committed harness that did not exist when 6B2 ran.

The eager design failed, was recorded as failing in `notes/32`, and that record
stands. Nothing in R2 rehabilitates it — and R2.1's falsification test exists
precisely to keep it failing.

## Read these three corrections before quoting anything here

1. **R2 itself failed its first gate.** The feasibility screen stopped on
   `pool_empty` under the ratio rule pre-registered in `notes/33`. That record
   is in `notes/34` and is not rewritten. R2.1 (`notes/35`) is a **post-result
   specification correction** — written after seeing the number it changes the
   verdict on — and the seven repetitions below were run fresh under it, with
   nothing reused from the stopped screen.
2. **The first seven-repetition run lost six repetitions to host starvation**
   and is recorded, non-citable, at tag `p6b2r2-perf`. The run quoted here is
   `p6b2r2-perf-2`.
3. **Shadow agreement stops being evidence at `669e303`.** After adoption
   `_pick_attribute` is an adapter over the same controller, so the comparison
   compares a value with itself. Only the pre-adoption `p6b2r2-shadow` run
   counts, exactly as `notes/31` said it would.

## What changed, in one table

Counted per dispatch over the bounded window, five facets, none already asked.
`_overgeneral` reads `cat.cats`; everything else reads `cat.text` at ~1.1 ms a
walk when `pool_depth=30`.

| arm | `cat.text` walks, utility ON | utility OFF |
|---|---|---|
| legacy | 11 | 6 |
| 6B2 eager | 15 | 15 |
| **R2 staged** | **5** | **6** |

The eager arm was flat: 15 walks whether five facets were unasked or one, and
15 on the first-two-`other` branch that reads none of them. The staged arm
resolves that branch before touching a candidate at all.

## A — pure-function correctness

The **4,320-cell** equivalence grid, the write-tracking oracle and the three
negative controls were **re-run against the staged implementation**, not
inherited: the grid compares against the legacy controller, and the thing being
compared had changed.

New in R2, and the test 6B2 did not have: **`ScanTopologyTest` asserts the scan
count per branch.** 6B2's defect was invisible at unit level — every branch
returned the right answer, every turn-comparison agreed, and only the clock said the
first-two-`other` path had scanned five facets to decide something that reads
none of them. A correctness suite that cannot see wasted work will pass a
design that does nothing but waste it.

| branch | `cat.cats` | `cat.text` |
|---|---|---|
| first-two-`other`, `probe_cycle`, `other_then_cycle`, dry degrade, fallback | 0 | 0 |
| easier | 1 | 0 |
| dry give-up | 1 | 0 |
| pool, utility ON, *u* unasked | 1 | *u* |
| pool, utility OFF, *u* unasked | 1 | *u* + 1 |

## B — shadow agreement, the only pre-adoption independent evidence

**18,597 raw turn-comparisons / 8,483.4 seed-normalised equivalent turns, zero
disagreements.** Both numbers, because one of them alone misleads.

Five of the seven scenarios are stochastic and run five seeds; `clean` and
`supplementary_dev` are deterministic and run once. **18,597** is what the
comparator actually executed and compared — the honest count of independent
turn-level checks, and the right denominator for "zero disagreements".
**8,483.4** divides each scenario by its own `n_seeds` before summing, so no
scenario is weighted five times as heavily as another purely by the seed
schedule; it is the right basis for a per-turn *rate* or a branch mix, and it
is what `lab/benchweights.py` froze the live weights from.

Writing "8,483 turns" unqualified — as earlier drafts of this phase's notes did
— reads as 8,483 raw comparisons. It was never wrong, but it was imprecise in
the direction that understates the work done. The same relationship holds for
the identically-shaped figure in `notes/30-phase6b1-results.md`; that document
is a closed record and is **not** edited here.

| scenario | seeds | raw comparisons | per-seed | attribute | state | message |
|---|---|---|---|---|---|---|
| clean | 1 | 411 | 411.0 | 0 | 0 | 0 |
| vague_start | 5 | 2,515 | 503.0 | 0 | 0 | 0 |
| uncooperative | 5 | 2,798 | 559.6 | 0 | 0 | 0 |
| override_genuine | 5 | 2,105 | 421.0 | 0 | 0 | 0 |
| override_category | 5 | 2,065 | 413.0 | 0 | 0 | 0 |
| contradiction | 5 | 3,159 | 631.8 | 0 | 0 | 0 |
| supplementary_dev | 1 | 5,544 | 5,544.0 | 0 | 0 | 0 |
| **total** | | **18,597** | **8,483.4** | **0** | **0** | **0** |

Every turn that carried a comparison was compared; none was skipped.

The observed branch mix reproduces the frozen weights turn for turn:
`first_two_other/open` 3,838.8, `pool_selection/structured` 3,596.2,
`give_up/open` 697.2, `pool_selection/open` 258.6, `easier/structured` 49.8,
`easier/open` 42.8 (per-seed normalised). **`easier/structured` still fires** —
the counterexample the design was built around, where selection takes the
easier branch while `_compose` renders the structured message from options the
same call wrote a few lines earlier.

## C — behaviour preservation

Bit-exact on everything.

| | off | shadow | control |
|---|---|---|---|
| `clean` | 0.932067 | 0.932067 | 0.932067 |
| `supplementary_dev` | 0.441608 | 0.441608 | 0.441608 |
| compat anchor | | | **0.928708** |

All four official slices identical between `off` and `control`
(`boundary`, `browsing`, `buying`, `intent_override`). Five robustness
scenarios identical on every metric and every slice:
`contradiction` 0.814187, `override_category` 0.931867, `override_genuine`
0.925980, `uncooperative` 0.831926, `vague_start` 0.919247.

29 cells, four leases, every one valid, every row citable.

## D — performance under R2.1

Full protocol: 1,000 warm-ups, 10,000 measured complete dispatches per arm per
fixture, seven fresh-process paired repetitions, alternating arm order,
15-minute timeout, watchdog at 2× the child's own projection. **7 of 7 citable
and completed.**

| fixture | class | legacy | pure | overhead | ratio | |
|---|---|---|---|---|---|---|
| `first_two_other` | no_scan | 0.0004 | 0.0080 | +0.0076 | *diag* | PASS |
| `probe_cycle` | no_scan | 0.0006 | 0.0082 | +0.0076 | *diag* | PASS |
| `easier` | category_only | 0.0204 | 0.0296 | +0.0093 | *diag* | PASS |
| `give_up` | category_only | 0.0006 | 0.0090 | +0.0083 | *diag* | PASS |
| `pool_empty` | pool | 0.0049 | 0.0207 | +0.0158 | *diag* | PASS |
| `pool_one_item` | pool | 0.5297 | 0.2850 | −0.2441 | 0.538 | PASS |
| `pool_utility_off_none_asked` | pool | 6.4232 | 6.4605 | +0.0223 | 1.003 | PASS |
| `pool_utility_off_three_asked` | pool | 2.8479 | 2.8699 | +0.0185 | 1.006 | PASS |
| **`pool_utility_on_none_asked`** | pool | **11.9710** | **5.6162** | **−6.3304** | **0.470** | PASS |
| `pool_utility_on_three_asked` | pool | 4.8148 | 2.0366 | −2.7782 | 0.423 | PASS |
| **live branch-weighted median** | | | | **−2.8723 ms** | | PASS (≤ +0.50) |

*diag* = the legacy median is below the 0.10 ms floor, so the ratio is
diagnostic and the fixture is held to the 0.10 ms micro-path budget instead.
Those ratios are printed in the report with their baselines, never dropped.

Per-repetition ratio on the shipped pool path: 0.4424, 0.4686, 0.4692, 0.4696,
0.4696, 0.4710, 0.4712 — spread 0.029, and the one outlier is the low end.

**The shipped configuration got 2.13× faster.** Not a target, and nothing
downstream was re-tuned to spend it: prediction 2 of `notes/33` said the pool
branches should get faster because one combined walk replaces legacy's separate
entropy and coverage walks over the same window, and that is what happened.
Prediction 4 also held — utility OFF is the case with no headroom, at ratio
1.003, which is +22 µs of pure orchestration on a 6.4 ms dispatch.

## Adoption comparison, against the measured R2 control

Run with **no `question_context_mode` set at all** (tags `p6b2r2-adoption`,
`p6b2r2-adoption-anchor`, both leases valid, all rows citable). Setting the
mode explicitly would test the flag; the question is whether an unconfigured
agent lands on the implementation that was measured.

| scenario | measured control | shipped default | |
|---|---|---|---|
| clean | 0.932067 | 0.932067 | identical, all slices |
| vague_start | 0.919247 | 0.919247 | identical, all slices |
| uncooperative | 0.831926 | 0.831926 | identical, all slices |
| override_genuine | 0.925980 | 0.925980 | identical, all slices |
| override_category | 0.931867 | 0.931867 | identical, all slices |
| contradiction | 0.814187 | 0.814187 | identical, all slices |
| supplementary_dev | 0.441608 | 0.441608 | identical, all slices |
| compat anchor | 0.928708 | **0.928708** | mode left at its default |

Seven of seven identical on score, HR@10, MRR, MTTC and every official slice.

## What adoption deleted

| | why it could go |
|---|---|
| `_pool_attribute` | the legacy selection rule; there is one copy now |
| `_pick_attribute`'s body | an adapter over `_decide_question`, like `_starved` after 6B1 |
| `_pool_entropy` | only caller was `_pool_attribute`; `_facet_pass` computes the same entropy from the same `_entropy_of` kernel |
| `_easiest_unasked` (Agent) | superseded by `context._easiest_unasked`, which takes its vocabularies as arguments |

The last two were orphaned in production and referenced only by tests. Keeping
a method alive so a test can call it is how dead code survives a cleanup, so
the tests were retargeted at the live path.

`test_the_adapter_holds_no_second_copy_of_the_rule` patches the staged entry
point and requires `_pick_attribute`'s answer to change — which is how a
relocation is stopped from quietly becoming a fork.

## Two things this phase cost, and what they bought

**A gate had to be corrected after it fired.** `notes/33` argued that a ratio
against a sub-microsecond baseline is arithmetic on noise, exempted two branch
classes on that basis, and then applied a ratio gate to a class spanning 4.9 µs
to 11.9 ms. That is a real specification defect and it was caught by the gate
working, not by anyone reading it. R2.1 replaces the hand-assignment with a
measured floor and is disclosed as post-result. Its falsification — that the
eager arm still fails ten gates under it, including the weighted aggregate at
+11.30 ms — is a committed test, so a later edit to the floor that started
admitting the eager arm breaks the build.

**Six repetitions were lost, on evidence consistent with host scheduling
starvation.** Every one left the process facts 6B2's four-hour stall never had:
state `R`, 3–34% of a core, CPU accumulating, and a parent whose own 900 s
deadline fired 400 s late.

Stated precisely, because the previous draft of this paragraph overreached:

> The captured facts are **consistent with host scheduling starvation**. There
> is **no evidence of deadlock** — the children were runnable and accumulating
> CPU throughout, and the parent's own timer was late by the same kind of
> factor, which is not what a blocked measured thread looks like. The **exact
> cause is undiagnosed.**

That is weaker than "rules out a hang", which is what this section said first
and could not support: a process that is runnable and slow is *unlikely* to be
deadlocked, but nothing here excludes livelock, lock convoying, or a stall
inside a dependency that still burns CPU. The honest claim is the absence of
positive evidence for a hang, not its exclusion.

The re-run was held awake with `caffeinate -dimsu`, and all seven landed within
493–507 s of a 493–496 s projection. That mitigation is empirical and its
mechanism is not diagnosed either.

## What must not be quoted

* Post-adoption shadow agreement. Tautological.
* The 11-vs-5 scan comparison as a live test. Both arms report 5 now; its
  evidence is the pre-adoption record.
* `"off"` as an independent legacy implementation. It is the adapter plus no
  orchestration telemetry.
* Anything from `p6b2r2-perf` (1 of 7) or the stopped screen `p6b2r2-screen`.
* Phase 6B2's gate-D benchmark, under any framing. See `notes/32`,
  Correction 1.
