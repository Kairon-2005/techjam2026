# Phase 3A pre-registration — re-attributing the existing clarification policy

Written before the A/B matrix runs. Recorded at `f6481d0`, 171 tests passing,
default = Phase 2 arm C.

## Phase 3: Candidate-aware Clarification & Over-Generality Guidance

The question Phase 3 exists to answer:

> Can candidate-pool information pick more valuable questions — cutting dry
> turns and MTTC — **without** costing recommendation quality?

The official metric never rewards withholding recommendations, so an
over-generality cutoff must steer **the question and the strategy**, never
truncate or empty the Top-10.

## 3A first: re-attribute what already exists

`_overgeneral()`, `_pool_entropy()`, `_pool_attribute()`, `other_then_pool`,
answerability weighting and structured broad options were all built and
measured under the **pre-Phase-2 retrieval path**. Phase 2 arm C changed what
the pool *is*: retrieval is 12× deeper and funnelled to 100. Whether these
still work is a measurement, not an assumption, and 3A runs before any new
code is written.

| arm | retrieval | clarification |
|---|---|---|
| **A** | Phase 2 arm C | `ask_policy="other"` — control, no pool awareness |
| **B** | Phase 2 arm C | current `other_then_pool` — the shipped default |
| **C** | Phase 2 arm C | candidate-aware utility — **only if B shows a real defect** |

**A and B run first.** If B is structurally sound with no clearly fixable
defect, Phase 3 closes on the existing implementation. Writing C to have
written something would be the failure mode, not the deliverable.

## The seven diagnostic questions

1. Does pool-aware asking move official score / MRR / MTTC at all?
2. How does it differ across buying / browsing / boundary / intent_override?
3. Does it reduce dry turns on `vague_start` and `uncooperative`?
4. On over-general turns: pool size, category count and entropy, chosen
   attribute, estimated information gain, whether the customer supplied a new
   constraint, and next-turn pool reduction.
5. **Is entropy inflated by the missing-value bucket?** `_pool_entropy` scores
   a product that mentions no colour as the empty-string value, so a facet
   most products are silent about reads as "the candidates disagree". Material
   is 57% covered, colour 39%, size 21%. Instrumented by
   `question_facet_coverage` recorded next to `estimated_information_gain`.
6. Is the Top-30 window (`pool_depth`) still representative now that the funnel
   returns 100 from a 1200-deep retrieval?
7. Is answerability weighting still inert? It only applies once
   `dry_streak >= answerability_after`.

Answers 5 and 6 are partly settled by reading the code; the matrix measures
how much they matter.

## Predictions

1. **B ≈ A on official score**, differing mainly in MTTC. That has been the
   shape since Phase 0: pool-aware asking changes which questions are asked,
   not the ranking.
2. **`question_facet_coverage` will be low — I predict under 0.55** — on
   targeted questions, meaning entropy is substantially driven by missingness.
   This is the defect most likely to be real.
3. **Answerability weighting will be near-inert** on `clean`, because
   `dry_streak` rarely reaches its threshold there.
4. Least confident: whether B reduces dry turns on `uncooperative`. Its whole
   purpose is that case, and Phase 2 changed the pool underneath it.

## Phase 3B — one hypothesis, only if earned

If and only if A/B expose a clear defect, exactly one hypothesis is
pre-registered:

> Estimating question utility from facet **coverage**, value distribution and
> answerability together avoids asking about attributes that most candidates
> are silent about or that customers cannot answer, better than Shannon
> entropy alone.

Constraints, fixed now: reuse the existing `FacetIndex`; no model, no external
dependency; missing values handled explicitly and never treated as a product
choice; no use of target, ground truth or future replies; structured options
drawn only from the live pool; no new regex families or scenario-specific
rules. **One hypothesis, one implementation, one formal matrix.** If it fails,
stop — no weight search.

## Acceptance

**Official non-regression vs Phase 2 arm C:** score drop ≤ `0.002` · HR@10 drop
≤ `0.005` · MRR drop ≤ `0.003` · MTTC ≤ `2.09` · no official slice HR or MRR
drop > `0.01` · intent_override HR@10 stays `1.000`.

**Robustness vs arm C:** `vague_start` and `uncooperative` score drop ≤ `0.010`
· `override_genuine`, `override_category`, `contradiction` drop ≤ `0.005`.

**Supplementary veto** (non-official, robustness evidence only — a
supplementary gain can never offset an official regression): overall score drop
> `0.010` VETO · HR@10 drop > `0.010` VETO · any supplementary slice score drop
> `0.020` VETO.

**Product evidence required to claim C beats B:** median pool reduction after an
answered targeted question ≥ `20%` · dry-question rate ≤ B · MTTC ≤ B ·
question-selection p95 < `5 ms` · added resident memory < `10 MB`.

If C cannot beat B on score but B already provides structured over-generality
guidance at near-zero cost, **B is kept** and Phase 3 is recorded as *product
capability established, official simulator reward limited*.

## Sets

`clean` + four official slices · `vague_start`, `uncooperative`,
`override_genuine`, `override_category`, `contradiction` ·
`supplementary_dev` + its four slices. Seeds `(7,8,9,10,11)`.

**The sealed supplementary holdout is not run.** It stays sealed until Phase 4,
Phase 6 and the final defaults are all frozen.
