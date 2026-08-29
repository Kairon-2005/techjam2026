# Phase 6C1 results — profile credibility, shadow evaluation

Implementation `2cfa043`…`4dbbaeb`, telemetry fix `c904c64`. Design
`notes/37` revision 3 (`d03d45e`). All scenario rows leased, isolated,
`matrix_complete`, `citable()`.

## Verdict: **6C2 is NOT designed.**

> **Target alignment was not demonstrated on the public clean set.**

That is the whole claim, and its wording is deliberate. It is **not** "the
profile signal does not exist": absence of demonstrated alignment on 200
samples of one public corpus is not proof of absence, and the stronger sentence
is not one this evidence can carry.

| gate | Arm A (official) | Arm B2 (oracle) |
|---|---|---|
| **D1** sessions with ≥1 credible tag | **PASS** — 185/200 = 0.925 | PASS — 192/200 = 0.960 |
| **D2** median pairwise Jaccard ≤ 0.30 | **FAIL** — **0.40** | PASS — **0.00** |
| **D3** credible tags have support, ≤ ceiling | **PASS** — 0 violations / 477 checked | PASS — 0 / 490 |
| **D5** target alignment | **FAIL** — see below | PASS — see below |
| **D4** instrument check (B1 ∧ B2) | **HOLDS** | |

`profile_context_mode` stays `"off"`. `w_profile` and `w_profile_adaptive`
remain `0.0`. There is no profile control mode in the codebase to enable.

## The result, in one paired comparison

Arm A and Arm B2 ran over the **same 61 eligible sessions**. The eligibility
constraint is set by retrieval, which the profile cannot influence, so the two
arms share their population exactly — same classifier, same kernel, same
window, same candidates. **Only the origin of the tags differs.**

| | Arm A — real profiles | Arm B2 — tags read off the target |
|---|---|---|
| D5 wins | **27 / 61 (44.3%)** | **61 / 61 (100%)** |
| median margin | **0.00** | **1.00** |
| one-sided exact binomial *p* | **0.847** | **~0** |
| D2 median Jaccard | 0.40 | 0.00 |

Arm A is **below chance**. Not "weakly positive", not "inconclusive": 27 of 61,
with a median margin of exactly zero, on a test whose null is a coin flip.

Arm B2 is the same machinery scoring **61 of 61**. So the instrument is not
blind, the kernel matches, the window is right, and the join works. **The
official `preference_tags` carry no target-discriminating signal on this set.**

This is exactly the separation D4 exists to provide, and exactly why D5 was
added in revision 2: **Arm A passes D1 and D3.** A phase that stopped at D1–D3
would have reported "credible tags exist in 92.5% of sessions" and moved on to
design personalization on top of a signal that ranks the right product worse
than a coin flip.

## D1 passed, and my pre-registered response was to distrust it

`notes/37` prediction 1 said Arm A would **fail** D1, and added: *"If Arm A
passes D1 comfortably, I have probably mis-implemented the kernel or the
ceiling, and that is the first thing to check rather than a result to
celebrate."* It passed at 0.925. So I checked before reporting.

Median per-tag support over 40 clean sessions, real catalog:

| tag | sessions | median `match_count` | median coverage | classified |
|---|---|---|---|---|
| `material` | 32 | 5 / 30 | 0.1667 | `specific_informative` |
| `fit` | 31 | 13 / 30 | 0.4333 | `specific_informative` |
| `comfort` | 26 | 1 / 30 | 0.0333 | `specific_informative` |
| `style` | 21 | 3 / 30 | 0.1000 | `specific_informative` |
| `durability` | 8 | 1 / 30 | 0.0333 | `specific_informative` |
| `performance` | 5 | 0 / 30 | 0.0000 | `unsupported` |
| `warmth` | 3 | 0 / 30 | 0.0000 | `unsupported` |
| `weather` | 2 | 0 / 30 | 0.0000 | `unsupported` |

**The kernel is correct.** Prediction 5 is confirmed exactly: `performance`,
`warmth` and `weather` are words a product description rarely uses about
itself, and under word-boundary matching they match nothing and land in
`unsupported`. Prediction 1's other half — that the remainder would be
`generic` — is **wrong**, and wrong for an informative reason: word-boundary
matching drops `fit` from near-ubiquitous to **13/30 = 0.433**, just under the
0.50 ceiling. Under the old substring matcher `fit` would have matched *outfit*
and *fitted* and been rejected as `generic` — the right verdict for the wrong
reason, which is precisely the artefact revision 2 changed the kernel to avoid.

So D1 passes honestly, and the finding is sharper than the prediction was:
**coverage-based credibility is necessary and nowhere near sufficient.** A tag
in 13 of 30 candidates is "credible" by the rule while carrying no information
about *which* of them this customer wants — because that same tag is in 81.5%
of all customers' profiles.

## Category distribution, first recommendation turn, raw counts

| category | Arm A | Arm B2 |
|---|---|---|
| `specific_informative` | 477 | 490 |
| `unsupported` | 171 | 207 |
| `generic` | 18 | 294 |
| `duplicated_session_evidence` | 0 | 9 |
| `conflicting` | 0 | 0 |

`conflicting` never fires, and `duplicated_session_evidence` fires 0 times on
Arm A — prediction 4, confirmed. The official tags name *dimensions* and the
session states *values*, so they do not collide as strings. That is itself
evidence that the profile and the session are describing different things.

## Eligibility is limited by retrieval, not by profiles

**138 of 200 sessions were excluded because the ground-truth target was not in
the pre-rerank top-30 window**; only 1 was excluded for having no supported
tag. The two reasons are counted separately precisely so this is visible: a
target missing from the window says something about **retrieval ordering**, not
about profiles, and pooling them would let a retrieval failure read as a
profile failure.

This is a real limit on D5's power. `clean` has HR@10 = 0.995 **after**
reranking, but the profile window is the first 30 candidates **before**
reranking — which is the pinned, non-circular call site, and the price of not
being circular is a thinner eligible population. n = 61 clears the
pre-registered minimum of 30, and it is thin; a future phase wanting more power
would have to widen the window, which is a design change and a new
pre-registration.

## Bit-exactness — shadow moved nothing

Every shard ran both modes. `off` and `shadow` are identical on score, HR@10,
MRR, MTTC and all four official slices, everywhere:

| shard | scenarios | result |
|---|---|---|
| `p6c1-arm-a` | `clean` | 0.932067 = 0.932067, slices identical |
| `p6c1-arm-b2` | `profile_informative` | identical |
| `p6c1-robustness` | 5 scenarios | identical on all five, all metrics and slices |
| `p6c1-supplementary` | `supplementary_dev` | 0.441608 = 0.441608, slices identical |

Robustness scores under both modes: `contradiction` 0.814187,
`override_category` 0.931867, `override_genuine` 0.925980, `uncooperative`
0.831926, `vague_start` 0.919247. **These are reported separately and were
never pooled into a gate denominator** — all seven scenarios derive from the
same 200 samples, so pooling would have counted each user up to 35 times.

`supplementary_dev`: **0 of 1,000 sessions carry a credible tag.** Its
synthetic profiles have no `preference_tags` the classifier can use at all.

## Component latency and memory

`p6c1-latency-2`, lease valid, **7 of 7 citable and completed**, full protocol:
1,000 warm-ups, 10,000 measured dispatches per arm per fixture, fresh process
per repetition, alternating arm order. The control arm does **nothing** —
profile shadow is purely additive — so the overhead below is the decision's
whole cost, not a difference between two implementations of it.

| fixture | tags | control | shadow | overhead | |
|---|---|---|---|---|---|
| `profile_no_tags` | 0 | 0.0000 | 0.0031 | **+0.0030 ms** | PASS |
| `profile_generic` | 3 | 0.0000 | 1.1453 | **+1.1453 ms** | **FAIL** |
| `profile_unsupported` | 3 | 0.0000 | 1.3219 | **+1.3219 ms** | **FAIL** |
| `profile_official_shape` | 4 | 0.0000 | 1.4750 | **+1.4750 ms** | **FAIL** |
| `profile_maximal` | 8 × 40 chars | 0.0000 | 3.4378 | **+3.4378 ms** | **FAIL** |

Peak RSS median **452.9 MB** (438.2–459.4), indistinguishable from the
question-only runs: the decision allocates a handful of frozen dataclasses and
at most eight compiled patterns.

**This is a 6C2 constraint, not a 6C1 failure.** 6C1 has no control mode, so
nothing on the live path pays this: `profile_context_mode` defaults to `"off"`
and the decision never runs outside a shadow measurement. But the 6C2 latency
gate pre-registered in `notes/37` is **≤ 0.25 ms**, and the
official-shape fixture is **1.475 ms — 5.9× over it**. Recorded now, before
6C2 exists, so a future phase cannot discover it after building on the
assumption that a "bounded 240-check decision" is cheap.

**Why it costs this much, counted rather than guessed.** The bound in
`notes/37` — 8 tags × 30 candidates = 240 shared-kernel match checks — is
correct, and 240 sounded small. Each check is a compiled regex search over a
product blob averaging ~1.1 kB, so the real quantity is ~264 kB of regex
scanning per dispatch. At four tags that is 4 × 30 × 1.1 kB ≈ 132 kB and
1.475 ms, which is the same ~11 µs per candidate-scan the question path pays.
The bound was on the number of checks; the cost is in the size of what each
one scans, and the design stated the first while implying the second.

The question-component fixtures in the same run are **tautological now** and
are not re-interpreted here: `_pick_attribute` has been an adapter over the
staged controller since `669e303`, so both arms execute the same code
(`notes/36`). Absolute question timings also differ from the 6B2-R2 run under
different host conditions, which is why only within-run paired deltas are
quoted anywhere in this project.

### The first attempt lost a repetition

`p6c1-latency` completed **6 of 7** and is retained, non-citable, in
`lab/benchmarks.jsonl`. Its aggregate is refused by `benchreport.aggregate()`
rather than reported over six. The abort carries the process facts the harness
exists to capture: at 570.9 s, state `R`, 100% CPU, but only 152 s of CPU time
consumed — 27% of wall — and at the 900 s kill, 193 s of CPU in 1,215 s.

**Consistent with host scheduling starvation; no evidence of deadlock; exact
cause undiagnosed** — the same wording, and the same limits, as
`notes/36`. It recurred *despite* `caffeinate -dimsu`, so that mitigation is
not sufficient and is not claimed to be. `p6c1-latency-2` is the run quoted
above and reproduced the first run's figures closely (official-shape 1.4418 →
1.4750 ms), which is corroboration and not a substitute for the missing
repetition.

## What must not be quoted

* **Any Arm B2 number as an achieved gain.** Its tags are read off the answer.
  It is an instrument check and an upper bound; it can never justify adoption.
* **"The profile signal does not exist."** The supported claim is that target
  alignment was not demonstrated on the public clean set.
* **D1's 0.925 as evidence that personalization is viable.** It is evidence
  that the coverage rule is permissive, which D5 then contradicts.
* **Robustness or supplementary numbers as gate evidence.** They are not, by
  construction.
* **The 6C2 gates.** They were pre-registered and not evaluated, because 6C2
  is not designed. The latency figure above is measured against one of them for
  information; it is not a 6C2 verdict, because there is no 6C2 to judge.
* **Anything from `p6c1-latency`** (6 of 7). The citable run is
  `p6c1-latency-2`.
* **Absolute question-component timings across runs.** Only within-run paired
  deltas are comparable.

## Out of scope, found and not fixed

The hostile-profile test found that **any non-string in `preference_tags`
crashes the agent** at `starter/retrieval.py:454`, on a path that runs every
turn regardless of `profile_context_mode` or `w_profile`. It is unreachable on
the official data, whose tags are all strings. It is reported rather than
patched because `notes/37` puts the reranker out of scope for Phase 6C.
