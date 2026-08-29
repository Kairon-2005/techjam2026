# Phase 6 — closed

Every claim below is backed by a leased, isolated, `matrix_complete` row that
passes `lab.provenance.citable()`. Where a phase failed, it is recorded as
failed and the failure is not paraphrased into something softer.

## Status

| sub-phase | status | evidence |
|---|---|---|
| **6A** — shadow context vocabulary | **COMPLETE** | `notes/27`, `notes/28` |
| **6B1** — retrieval controller | **ADOPTED** | `notes/29`, `notes/30` |
| **6B2** — question controller, eager design | **REJECTED / NOT ADOPTED** | `notes/31`, `notes/32` |
| **6B2-R2** — question controller, staged design | **ADOPTED** | `notes/33`–`notes/36` |
| **6C1** — profile credibility | **EVALUATED** | `notes/37`, `notes/38` |
| **6C2** — controlled personalization | **NOT DESIGNED** | gated out by 6C1 |

**Profile weights remain zero.** `w_profile = 0.0`,
`w_profile_adaptive = 0.0`, `profile_context_mode = "off"`, and there is no
profile control mode in the codebase to enable.

## What each sub-phase established

**6A — shadow vocabulary complete.** A bounded, immutable `ContextSnapshot`
(≤ 64 entries, ≤ 4096 bytes, asserted by building the maximal instance) and a
pure policy over it, running in shadow and reaching nothing. It is the
vocabulary every later sub-phase was written in.

**6B1 — retrieval controller adopted.** `_starved()` became an adapter over
`decide_retrieval()`; one rule, one copy. Zero disagreements pre-adoption.

**6B2 — eager design rejected.** `CandidateStats` precomputed every facet,
both entropy variants and every coverage before the decision was entered — 15
passes over `cat.text` per dispatch, including on the 45% of turns that read
none of them. Correctness passed; the performance gate failed; there was no
adoption commit. That record stands and R2 does not rehabilitate it.

**6B2-R2 — staged design adopted.** Six stages, the host scanning only between
them. **18,597 raw turn-comparisons / 8,483.4 seed-normalised, zero
disagreements.** Bit-exact everywhere, compat anchor `0.928708`. The shipped
pool path got **2.13× faster** (11.9710 → 5.6162 ms) by walking `cat.text`
five times where the legacy path walked it eleven. Adopted at `669e303`,
default `question_context_mode="control"`.

R2 also failed its own first gate — the screen stopped on `pool_empty` under
`notes/33`'s ratio rule — and R2.1 (`notes/35`) is disclosed as a post-result
specification correction, with a committed falsification test proving it still
rejects the eager arm.

**6C1 — profile credibility evaluated.** Five categories with a fixed
precedence, one shared word-boundary match kernel, a call site pinned
pre-rerank so the evidence cannot be circular, and shadow proven inert.

Arm A (official profiles) passed D1 (0.925) and D3 (0 violations of 477
credible tags), and failed D2 (0.40 against ≤ 0.30) and D5. D4 held: the
instrument scored 61/61 on the oracle arm over the same 61 eligible sessions.

> **Target alignment was not demonstrated on the public clean set.**

The observed win rate was 27/61, below 50%. Positive target alignment was not
demonstrated. **The experiment does not distinguish a neutral effect from a
negative effect** — the reverse one-sided test gives `p = 0.221` and the 95%
interval [0.325, 0.567] contains 0.5.

**6C2 — not designed.** The pre-registered gate did not open. Its adoption
gates were written in `notes/37` and never evaluated, because there is nothing
to evaluate.

## Verification at closure

| | |
|---|---|
| tests | **484 pass**, 1 skipped |
| `clean` composite | **0.932067** |
| `boundary` MRR | **1.000000** |
| `browsing` MRR | **0.809375** |
| `buying` MRR | **0.851181** |
| `intent_override` MRR | **0.922222** |
| compat anchor | **0.928708** |
| working tree | clean |
| experiment lease | none held |
| writers | one |

## Hygiene cleared before Phase 7

**A — non-string `preference_tags` crashed the agent.** Fixed at `58c8f8b`.
The reranker now shares the profile classifier's normalizer, and — the part
that mattered — **no longer normalizes at all while both profile weights are
zero**, so neither the cost nor the crash is paid for a feature that is not
running.

**B — `config_label` never reached recorded rows.** Fixed at `b1bf288`. All 20
Phase 6C1 rows carry their arm label, so Phase 7's multi-arm runs can identify
an arm by name instead of reverse-mapping config dicts. No historical row was
back-filled; the ledger stays append-only and `report.py`'s fallback still
renders the older label-less rows.

## Carried forward as constraints, not as work

* **The 6C1 profile decision costs +1.475 ms** (official shape) and +3.438 ms
  at the 8 × 40-character maximum, against the ≤ 0.25 ms gate `notes/37`
  pre-registered for 6C2. **Deliberately not optimised**: it is not on the live
  control path, the alignment gate failed, and optimising it would be work with
  no product benefit. It is recorded as a constraint on any future attempt to
  reopen personalization.
* **The lesson underneath it:** a bound on the *number* of operations is not a
  bound on cost. 240 bounded checks cost 1.475 ms because each scanned ~1.1 kB.
* **Host scheduling starvation recurs even under `caffeinate -dimsu`**, and
  cost one repetition of the 6C1 latency run. Consistent with scheduling
  starvation; no evidence of deadlock; exact cause undiagnosed.

## Not done in Phase 6

The sealed holdout was not run. The reranker was not touched except for the
crash guard above. No downstream weight was tuned. Cross-session memory was
never built, and could not be: the evaluator mints a fresh `uuid4` per sample.
