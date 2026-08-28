# Phase 6B2-R2 — feasibility screen: **STOP**

Harness `ba85207`, pre-registration `d953bd8`, implementation `027eb6f`.
Screen tag `p6b2r2-screen`, lease **VALID**, 3/3 repetitions, `citable()`.

## Verdict: the pre-registered stop condition fired. The scenario matrix was NOT run.

`notes/33-phase6b2-r2-prereg.md` step 3: *if any branch exceeds its gate by
more than 20%, stop R2 immediately; do not run the expensive scenario matrix.*
One branch did. Nothing further was run: no seven-repetition performance
matrix, no 8,483-turn shadow comparison, no A/B/C matrix.

**What fired**

| fixture | class | legacy | pure | overhead | ratio | gate | |
|---|---|---|---|---|---|---|---|
| `pool_empty` | pool | 0.0049 ms | 0.0209 ms | **+0.0160 ms** | **4.2803** | ratio ≤ 1.10 | **FAIL, ×3.89 over** |

Stable across all three repetitions and across both arm orders: ratios 4.2304
(legacy-first), 4.9418 (pure-first), 4.2803 (legacy-first); overheads +15.7,
+19.5, +16.0 **microseconds**. This is not noise and is not an artefact of arm
order. It is a real, reproducible +16 µs.

## Everything else passed, most of it by a wide margin

| fixture | class | legacy | pure | overhead | ratio | |
|---|---|---|---|---|---|---|
| `first_two_other` | no_scan | 0.0004 | 0.0078 | +0.0074 | — | PASS (≤ 0.10) |
| `probe_cycle` | no_scan | 0.0006 | 0.0080 | +0.0074 | — | PASS (≤ 0.10) |
| `easier` | category_only | 0.0220 | 0.0300 | +0.0079 | — | PASS (≤ 0.25) |
| `give_up` | category_only | 0.0006 | 0.0089 | +0.0083 | — | PASS (≤ 0.25) |
| `pool_one_item` | pool | 0.5269 | 0.2835 | **−0.2429** | 0.539 | PASS |
| `pool_utility_off_none_asked` | pool | 6.4100 | 6.4422 | +0.0232 | 1.004 | PASS |
| `pool_utility_off_three_asked` | pool | 2.8415 | 2.8450 | +0.0035 | 1.001 | PASS |
| `pool_utility_on_none_asked` | pool | 11.8985 | 5.5933 | **−6.3052** | **0.470** | PASS |
| `pool_utility_on_three_asked` | pool | 4.8045 | 2.0309 | **−2.7754** | 0.423 | PASS |
| **live branch-weighted median** | | | | **−2.8609 ms** | | PASS (≤ +0.50) |

The staged controller is **2.13× faster than legacy** on the shipped
configuration (`question_utility=True`, `pool_depth=30`, nothing asked), and
the live branch-weighted overhead is −2.86 ms against a +0.50 ms gate. The
ratio 0.4701 is identical to four decimal places in all three repetitions.

Predictions 1–4 of the pre-registration are borne out by the screen so far as
it goes: the pool branches got faster rather than merely no slower (prediction
2), and utility OFF is indeed the case with no headroom, at ratio 1.004 —
pure orchestration overhead of +23 µs on a 6.4 ms dispatch (prediction 4).
Prediction 1 (100% agreement) is **not** tested by a screen; the equivalence
grid and unit tests pass, but the 8,483-turn comparison was not run.

## What the miss actually is

`pool_empty` hands the controller an **empty candidate pool**. Legacy's five
`_pool_entropy` calls return `0.0` from an empty loop and `_facet_coverage`
returns `0.0` without looking at anything, so legacy's whole dispatch is 4.9 µs
of doing nothing. R2 builds a `FacetScanPlan`, five frozen `FacetSample`
dataclasses, a `PoolPick` and a `QuestionPatch` — roughly seven small objects,
~16 µs — and then also does nothing. **R2 is slower here because there is no
work to skip, only objects to build.**

The gate it failed is `max_ratio ≤ 1.10`, assigned to the whole `pool` branch
class in `lab/benchfixtures.py`.

**This is a defect in the gate specification, and I am recording that I
noticed it only after seeing the number.** The pre-registration itself argues,
in writing, that *"a ratio against a sub-microsecond baseline is arithmetic on
noise"* — and on that basis withholds the ratio gate from the no-scan and
category-only classes, whose baselines are 0.7 µs and 21 µs. `pool_empty`'s
baseline is **4.9 µs**: the same regime, in a class whose other members are
0.5–11.9 ms. The reasoning that exempted the no-scan class applies to it
exactly and was not applied, because I classified fixtures by which *branch*
they take and gated by class, without checking that every member of the class
had a baseline the ratio gate made sense against.

`pool_empty` is also a **boundary fixture, not a live-traffic one**. It carries
no weight in the branch-weighted aggregate — `benchweights.REPRESENTATIVE` maps
`pool_selection` to `pool_utility_on_none_asked` — and it exists because the
pre-registration asked for empty and one-item pools as robustness cases. It
received a gate shaped for live cost.

## What is NOT being claimed

* **Not** that the gate should be changed. It was pre-registered, it is
  committed in `lab/benchreport.py:GATES`, and it fired. Rewriting a threshold
  after seeing the number it rejected is threshold tuning, which R2's
  pre-registration forbids in terms.
* **Not** that R2 "really passed". Under the gates as registered, R2 failed the
  screen. The screen is also, by construction, not a gate — it can say "keep
  going", never "passed" — so nothing here says R2 would have passed the full
  protocol either.
* **Not** that +16 µs on an empty pool is unimportant *because* it is small.
  It is small; whether that matters is a question about the gate, and the gate
  is not mine to re-decide after the fact.

## Correctness evidence that does stand

Independent of the screen, and re-run against the staged implementation rather
than inherited from 6B2:

* the **4,320-cell equivalence grid** against the legacy controller, with the
  write-tracking oracle and all three negative controls;
* **345 tests** passing, including a new `ScanTopologyTest` that asserts the
  pre-registered scan topology per branch — the test 6B2 did not have, and the
  reason its defect was invisible at unit level;
* counted from source and asserted in tests: on the shipped configuration
  legacy walks `cat.text` **11** times per dispatch and staged walks it **5**;
  with utility OFF both walk it **6**.

The 8,483-turn shadow comparison and the A/B/C matrix were **not** run, so
there is no pre-adoption behavioural evidence for R2. Under no circumstances
may the staged controller be adopted on what is in this document.

## State

`question_context_mode` remains `"off"`. The legacy controller is the single
live implementation. The staged implementation sits behind the flag at
`027eb6f`, described in its own commit as temporary.

Awaiting a decision on the gate-specification defect before either abandoning
6B2 control (deleting the staged implementation, per the pre-registration's
failure path) or issuing a corrected, separately dated pre-registration that
acknowledges it was written with knowledge of this result.
