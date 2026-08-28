# Phase 6B2-R2 results — staged question context

Implementation `027eb6f`, gate revision R2.1 `2908a8b`, adoption `669e303`.
Harness `ba85207`. All comparison rows leased, isolated, `matrix_complete`,
`citable()`.

## Verdict: **ADOPTED.** `question_context_mode` defaults to `"control"`.

| gate | result | evidence |
|---|---|---|
| A — pure-function correctness | **PASS** | 4,320-cell grid, 362 tests |
| B — shadow agreement | **PASS**, 8,483 turns, zero disagreements | `p6b2r2-shadow`, 7/7 citable |
| C — behaviour preservation | **PASS**, bit-exact | 29 cells, four leases, all valid |
| D — performance (R2.1) | **PASS**, 7/7 repetitions | `p6b2r2-perf-2` |
| hard gate — no lazy index construction | **PASS** | now a test, not an inspection |

**Phase 6B2 is still NOT ADOPTED.** R2 inherited none of its evidence and does
not rehabilitate it. 6B2's eager design failed, was recorded as failing, and
this is a different design measured from scratch.

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
returned the right answer, all 8,483 turns agreed, and only the clock said the
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

| scenario | turns | compared | attribute | state | message |
|---|---|---|---|---|---|
| clean | 411 | 411 | 0 | 0 | 0 |
| vague_start | 503 | 503 | 0 | 0 | 0 |
| uncooperative | 560 | 560 | 0 | 0 | 0 |
| override_genuine | 421 | 421 | 0 | 0 | 0 |
| override_category | 413 | 413 | 0 | 0 | 0 |
| contradiction | 632 | 632 | 0 | 0 | 0 |
| supplementary_dev | 5,544 | 5,544 | 0 | 0 | 0 |
| **total** | **8,483** | **8,483** | **0** | **0** | **0** |

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

**Six repetitions were lost to host starvation.** Every one left the process
facts 6B2's four-hour stall never had: state `R`, 3–34% of a core, CPU
accumulating, and a parent whose own 900 s deadline fired 400 s late. That is
not a diagnosis of what throttled them and none is claimed — but it is enough
to rule out a hang in the measured code, which is exactly the question 6B2
could not answer. The re-run was held awake with `caffeinate`, and all seven
landed within 493–507 s of a 493–496 s projection.

## What must not be quoted

* Post-adoption shadow agreement. Tautological.
* The 11-vs-5 scan comparison as a live test. Both arms report 5 now; its
  evidence is the pre-adoption record.
* `"off"` as an independent legacy implementation. It is the adapter plus no
  orchestration telemetry.
* Anything from `p6b2r2-perf` (1 of 7) or the stopped screen `p6b2r2-screen`.
* Phase 6B2's gate-D benchmark, under any framing. See `notes/32`,
  Correction 1.
