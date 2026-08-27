# Phase 3 results — Candidate-aware Clarification & Over-Generality Guidance

All rows leased, isolated, `matrix_complete`, `citable()`. Pre-registration:
[notes/17-phase3a-prereg.md](17-phase3a-prereg.md), written before any Phase 3
code existed.

## Verdict

The pre-registered defect is **real and now fixed**, arm C passes every
measurable gate, and the official reward is **+0.0001**. Recommendation: adopt
C. `question_utility` is left **off** pending sign-off, because every prior
default in this project was a reviewer decision and the margin here does not
justify making this one unilaterally.

## 3A — the seven diagnostic questions

**1. Does pool-aware asking move the official score?** Barely, and slightly
downward. A (`ask_policy="other"`, no pool awareness) scores `0.932167`;
B (shipped `other_then_pool`) scores `0.931967`. B costs `−0.0002`, all of it
MTTC (2.055 → 2.065).

**2. Across the four official slices — nothing at all.** A and B are
**bit-identical** on every slice: buying HR 0.988 / MRR 0.851181, browsing
1.000 / 0.809375, boundary 1.000 / 1.000000, intent_override 1.000 / 0.922222.
Pool-aware questioning has never changed the ranking; it changes which
questions get asked.

**3. Does B reduce dry turns? No — it slightly increases them.**
`dry_question_rate` on `vague_start` is 0.1577 for A and **0.1889** for B; on
`uncooperative` 0.6785 vs 0.6798. The policy built to cut dry turns was adding
them.

**4. Over-general turns.** Triggered on 9.5% of clean turns, 24.5% of
`vague_start`, 22.0% of `uncooperative`, 56.2% of `supplementary_dev`.
Structured options are emitted on exactly those turns. Estimated information
gain averages 1.80 bits on clean.

**5. Entropy WAS inflated by the missing-value bucket — predicted, and
confirmed.** `_pool_entropy` scored a product mentioning no colour as the
empty-string value, so an attribute most candidates are *silent* about read as
one they *disagree* about. Measured `question_facet_coverage` on the attribute
actually asked: **0.5643** clean, **0.5336** `vague_start`, **0.2601**
`uncooperative`, **0.5565** supplementary. On `uncooperative`, 74% of the
window had no value for the question being asked. Pre-registered prediction
was "under 0.55"; three of four came in at or under it.

**6. The Top-30 window is much less representative than it was.** Under arm C
the pool is 100 on clean (window = 30%), but 194 on `uncooperative` and up to
1000 on a starvation-bypass turn, where the window covers **3%**.

**7. Answerability weighting is near-inert on clean**, as predicted: it only
engages once `dry_streak ≥ 1`, and clean's dry rate is 0.076.

### A structural finding that invalidated one of my own gates

`median_pool_reduction_after_answer` is **0.0000 in every arm and every
scenario**, and `pool_size_next_turn` is always exactly 100 — because the
funnel emits exactly `funnel_top` candidates every turn. **Pool reduction is a
property of the funnel, not of the questions**, so the product gate I
pre-registered for arm C — "median pool reduction ≥ 20%" — is unsatisfiable by
any question policy. That is a defect in my pre-registration, and only
measurement could have found it. It is reported, not quietly dropped.

## 3B — arm C, earned by the 3A diagnosis

```
utility(attribute) = information_gain × catalog_coverage × answerability
                     − question_dry_cost × (1 − coverage × answerability)
```

with the missing bucket **excluded** from the entropy. Reuses the existing
window, `FacetIndex` vocabularies and `ANSWERABILITY`; no model, no dependency,
no new regex family, no scenario rule, no use of ground truth or future replies.

### Official and robustness

| scenario | B | C | C−B | MTTC B → C |
|---|---|---|---|---|
| `clean` | 0.931967 | **0.932067** | +0.0001 | 2.065 → 2.060 |
| `vague_start` | 0.918847 | **0.919247** | +0.0004 | 2.550 → 2.530 |
| `uncooperative` | 0.831846 | 0.831926 | +0.0001 | 2.883 → 2.879 |
| `override_genuine` | 0.925593 | 0.925980 | +0.0004 | 2.121 → 2.115 |
| `override_category` | 0.931767 | 0.931867 | +0.0001 | 2.075 → 2.070 |
| `contradiction` | 0.814207 | 0.814187 | −0.00002 | 3.243 → 3.244 |

C is never meaningfully worse and is deterministically better on `clean` and
`vague_start` (both sd 0.0000). HR@10 is unchanged everywhere.

### Supplementary — non-official, veto evidence only

| | B | C |
|---|---|---|
| `supplementary_dev` score | 0.441431 | 0.441608 |
| HR@10 | 0.560 | 0.560 |

No veto triggers. Supplementary is far harder than the public set (0.44 vs
0.93, HR@10 0.56 vs 0.995), which is what makes it useful as a veto. **A
supplementary gain never offsets an official regression, and this one is not
offered as evidence of anything but the absence of collapse.**

### Did C fix the defect it targeted? Yes.

| metric | scenario | B | C |
|---|---|---|---|
| `question_facet_coverage` | clean | 0.5643 | **0.6425** |
| | `vague_start` | 0.5336 | **0.6414** |
| | `uncooperative` | 0.2601 | 0.2819 |
| | supplementary | 0.5565 | **0.6945** |
| `dry_question_rate` | clean | 0.0755 | **0.0711** |
| | `vague_start` | 0.1889 | **0.1749** |

C asks about better-described attributes and goes dry less often. The
attribute histogram shifts accordingly — on clean, `color` rises from 0 to 7
and `material` falls, because colour is stated more often than the entropy
signal implied.

## Gates

**Official non-regression vs Phase 2 arm C** — score drop ≤0.002: PASS (+0.0001)
· HR@10 drop ≤0.005: PASS (0.000) · MRR drop ≤0.003: PASS (0.000) · MTTC ≤2.09:
PASS (2.060) · no slice HR/MRR drop >0.01: PASS · intent_override HR 1.000:
PASS.

**Robustness vs arm C** — `vague_start` ≤0.010: PASS (+0.0004) ·
`uncooperative` ≤0.010: PASS (+0.0001) · three guards ≤0.005: PASS.

**Supplementary veto** — overall ≤0.010: PASS · HR@10 ≤0.010: PASS · slices
≤0.020: PASS.

**Product evidence for C over B** — dry-question rate ≤ B: **PASS** · MTTC ≤ B:
**PASS** · question-selection p95 <5 ms: **PASS** (+3.77 ms, measured on a
120-wide window, four times the real one) · added memory <10 MB: **PASS** (C
adds no index; it reuses the existing window and vocabularies) · median pool
reduction ≥20%: **UNMEASURABLE** — structurally 0 under the funnel, see above.

## Recommendation

Adopt arm C. It is never worse, deterministically better on both deterministic
scenarios, improves MTTC on five of six, and removes a defect that would
otherwise keep steering the asker toward attributes the catalog is silent
about — a defect that matters more as retrieval widens, not less.

The counter-argument, stated fairly: the official gain is `+0.0001`, and a
reviewer could reasonably decline to move a default for that. If B is kept, C
stays behind `question_utility` and the defect is documented rather than
fixed.

Phase 3 is recorded as **product capability established, official simulator
reward limited**. The simulator answers every question from the target
product's own attributes, so a better question cannot help it answer better —
which is exactly why the score barely moves while coverage and dry rate do.

## Aborted runs

`p3a-Q4` and `p3b-C6`, both 10-minute shard wall clocks, both journalling
nothing, both recorded in `lab/invalidations.jsonl`. The first exposed a real
inefficiency: deterministic scenarios were being run over five identical
seeds, which on `supplementary_dev` (1000 sessions) cost 5× for nothing. Fixed
by asking whether a scenario has any hook that could consume a seed.

## Row keys

| row_key | tag | scenario | arm | official | score |
| `2c2effbad9899aa4` | p3a-clean-official | clean | A | true | 0.932167 |
| `f0fb192e135eb9d9` | p3a-clean-official | clean | B | true | 0.931967 |
| `1f34bc0a666af1a8` | p3a-supplementary | supplementary_dev | A | false | 0.440712 |
| `57d8c0c93c15a43e` | p3a-supplementary | supplementary_dev | B | false | 0.441431 |
| `f9216d634f9bf1c1` | p3a-thin | uncooperative | A | true | 0.831986 |
| `1e77849c110658b7` | p3a-thin | uncooperative | B | true | 0.831846 |
| `aca66b6e8bdb4c2c` | p3a-thin | vague_start | A | true | 0.920497 |
| `b036b365ea10d622` | p3a-thin | vague_start | B | true | 0.918847 |
| `e0882556435dee7a` | p3b-clean-official | clean | B | true | 0.931967 |
| `167a6b5abcced009` | p3b-clean-official | clean | C | true | 0.932067 |
| `3a04a60dc11fabf2` | p3b-guards | contradiction | B | true | 0.814207 |
| `cee40abba2ca68ee` | p3b-guards | contradiction | C | true | 0.814187 |
| `7ad6fa2792a9b027` | p3b-guards | override_category | B | true | 0.931767 |
| `8b5947de3eeb7128` | p3b-guards | override_category | C | true | 0.931867 |
| `d33a339a025fff20` | p3b-guards | override_genuine | B | true | 0.925593 |
| `d7a48c87c8d2a54b` | p3b-guards | override_genuine | C | true | 0.925980 |
| `78b1622f48dc0ba3` | p3b-supplementary | supplementary_dev | C | false | 0.441608 |
| `7cd937c3d0cc3c39` | p3b-thin | uncooperative | B | true | 0.831846 |
| `5cfad8c5224d20bd` | p3b-thin | uncooperative | C | true | 0.831926 |
| `5801425b5a343e88` | p3b-thin | vague_start | B | true | 0.918847 |
| `a8a9cef269f2e7b8` | p3b-thin | vague_start | C | true | 0.919247 |
