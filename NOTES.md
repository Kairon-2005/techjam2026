# Decision log

Reverse chronological. Each entry records what was decided, on what evidence,
and what evidence would overturn it.

## 2026-08-26 (response to external review, see notes/08)

- **The code is frozen into git (`2f85538`).** This was the single largest
  outstanding risk: 490 lines of uncommitted changes, with all 66 experiment
  records pointing at a starter commit that did not contain that code.
  `sweep.py` now flags a dirty working tree.
- **Experiment rejected "route-conditioned weights".** The +0.0013 from
  `browsing w_pop=6` decomposes into 2 sessions better / 7 worse, only 2 of 5
  folds improved, and the neighbourhood does not support it. It is a noise
  spike and is not adopted. What was adopted instead: `route_overrides` now
  apply for the whole turn (previously patching `ask_policy` / `on_override`
  failed silently), plus a warning on unknown configuration keys.
- **Experiment rejected "slot erasure as the default".** A new
  `lab/override_stress.py` replaces an obsolete preference with a constraint
  from a different product (a direct contradiction such as silk to leather).
  Averaged over 5 seeds, `keep` still wins: **0.9233 vs slot 0.9140 vs erase
  0.8458**. **The reason is that this system scores and does not filter:** an
  obsolete constraint can only contribute a little wrong credit and cannot
  exclude the correct product, whereas forgetting genuinely destroys evidence.
  This is a transferable conclusion. `slot` is retained as a measured
  capability, and the report presents it as a design decision rather than a
  claim that erasure was performed.
- **Adopted "pool-aware questioning"** (`other_then_pool` plus dry-streak
  protection) at a cost of **-0.0002** (0.9285), with 17% of turns asking a
  real information-gain question derived from candidate-pool entropy. Paired
  analysis: 0 sessions ranked better, 0 worse; the whole cost is in MTTC. **Now
  the default:** 0.928708 to 0.928508, HR@10 and MRR identical digit for digit,
  only MTTC moving 2.03 to 2.04. Pure `other` is reproducible with one
  configuration key, and both configurations' scores are locked by
  `tests/test_score_regression.py`.
- **Adopted "build indexes on demand".** The `order` and `card` weights default
  to 0 yet roughly 80 MB of index was built unconditionally. After making them
  lazy: 7.70s/430MB becomes **5.07s/393MB**, with the score identical digit for
  digit.
- **Fixed two real bugs.** `decay` retained the *oldest* evidence (`[:8]`)
  rather than the newest; after the fix that strategy gains +0.030.
  `lab/stress.py` had two live `__main__` guards, so `stress v2` ran the v1
  suite first.
- **Wording corrected.** `tune.py` no longer claims that fold scores estimate
  private-set performance; externally the number is stated as a public
  development score of 0.928708.
- **Tests grew from 3 cases (evaluator only) to 30**, including a score lock and
  agent-level regressions.

## 2026-08-25

- **Track 4 (Shopping Copilot) selected.** Rationale: solo entry with no GPU.
  The rules cap compute (no base-model fine-tuning, no heavy vector database,
  everything in memory), which flattens the resource gap, and there is a
  deterministic local evaluator giving a roughly 25-second iteration loop.
  Ruled out: Track 2 (scored on data volume and GPU-hours), Track 3 (not taking
  an infrastructure route, and the brief itself concedes the kernel can be
  AI-generated), Track 1 (heavy overlap with an existing my-coding-agent
  project), Track 5 (feasible, but the data download is the bottleneck and CV
  is not the main strength here).
- **The lab runs in a cloud container; deliverables land in the local
  repository.** Rationale: the local sandbox suspends processes between calls,
  background tasks cannot survive, and a single call is capped at 45 seconds.
  The cloud container has neither limit.
- **Design principle established: the main path must make zero network calls.**
  Rationale: `docs/submission_rules.md` states explicitly that "For official
  final scoring, organizer policy may disable network access."
- **Ablation confirmed H1 (ask `other`) plus H2 (do not clear on override):**
  0.1067 to 0.7536 (**7.06x**). The two are super-additive because a hit before
  an override does not score.
- **Reranking layer complete: 0.7536 to 0.9280 (8.70x over the baseline).**
  Recall@200 = 1.000 on the public development set, which shows retrieval is
  not the bottleneck there; the remaining headroom is entirely in ranking.
- **The popularity prior is the single strongest feature (+0.114).** Target
  products have a median `rating_number` of 6846 against 12 for the whole
  catalog (the 99.5th percentile), because sessions are sampled from the Amazon
  5-core split. This is a transferable modelling insight about *this*
  evaluation.
- **`w_card` (reverse-engineering the simulator) deliberately abandoned.** It is
  worth only +0.0033, is fragile against any rewording the organizer may
  introduce, and would contaminate the narrative of the whole approach. The
  switch is retained, defaults to off, and appears in the report as an
  ablation.
- **Negative results:** popularity percentile transforms, constraint-position
  features, constraint IDF weighting, and widening the candidate pool to 400 --
  all worse.
- **Next:** MRR (0.053 remaining, 91% of all remaining headroom). Of 199 hits,
  142 are already rank-1.

## 2026-08-25 (afternoon)

- **Paraphrase robustness is the only generalization risk confirmed by
  measurement.** Three paraphrase styles pushed the older agent to
  0.776/0.807/0.671; after adding template-independent cue parsing plus
  noise/override regular expressions it recovered to 0.918/0.923/0.856, with
  zero regression on the clean simulator. The stress harness itself first had to
  be fixed for a bug where `disclosed` was contaminated early (the identity
  style now reproduces 0.9280 exactly, which serves as the harness's own
  regression check).
- **Popularity dependence quantified:** `w_pop=0` gives 0.867 (-0.061);
  `w_pop=2` gives 0.925. The optimum is flat, and the cause of the prior
  (5-core sampling) holds for the private set as well.
- **Route-conditioned reranking is implemented but not merged into the
  default:** raising `w_pop` for browsing gives 0.9291 (a single run; +0.0011 is
  within fold noise and was not cross-validated). The submitted version is
  frozen at the default configuration, 0.9280; `route_overrides` is written up
  as an ablated extension.
- **Performance profile:** index build 15.9s (one-off), 35-64ms per turn,
  roughly 207ms for a 4-turn session, 0 tokens.
- **Decision: freeze the main line at 0.9280 (the default configuration).**
  Marginal returns have fallen below 0.003 per feature, and 65% of the rubric
  does not look at the score. Remaining time goes to verbose residuals, the
  information-gain counterfactual experiment, and the report and demo.

## 2026-08-25 (evening, after a three-way adversarial review)

- Three independent review agents (code / methodology / compliance) cross-checked
  the work; all findings are archived in notes/06.
- Fixes: `w_pos` branch variable shadowing (which contaminated the tie-break);
  `__init__` hardening (working-directory-independent paths, tolerant
  `TJ_CONFIG`); recommendation count clamped to 100 per the contract;
  non-string messages coerced; a `term_cap` guard; and **a runtime fallback for
  the `ask="other"` disaster branch** (after 2 consecutive dry replies it
  degrades to cycling concrete attributes, at zero cost on the clean simulator).
  After the fixes, 0.9280 reproduces exactly.
- The methodology review's central rulings were adopted: the cross-validation
  numbers are "a restatement of the training split", and the paraphrase stress
  test is "a regression suite, not a robustness estimate" (the cue regular
  expressions were written against those three styles). The report's wording was
  weakened accordingly: 0.90-0.93 expected on an in-distribution private set;
  an honest estimate of 0.67-0.86 under unseen paraphrase styles (pre-fix
  numbers).
- The compliance review confirmed: `evaluator/` byte-for-byte unchanged, no
  secrets, test suite passing, 0.9280 reproducible with one command. The
  Devpost-layer deliverables (report, public repository, video) were at 0%,
  which was the largest gap at the time.

## 2026-08-26

- **Slot-level graceful degradation shipped and merged into the default**
  (`slot_soft=4`): each constraint is probed independently for liveness, and a
  dead slot falls back to IDF-weighted soft matching (excluding high-frequency
  terms with IDF < 1.5). Clean set 0.9287 (no insurance premium; +0.0007 the
  other way); the payload-rewording set improves from 0.854-0.874 to
  0.876-0.898. Stress set v2 (payload rewording) was synthesized for validation
  and its transformations were never fed back into the regular expressions.
  Discarded: static `w_soft` (a -0.003 insurance premium) and session-level
  gating (weak recovery).
- **New honest worst-case interval: 0.85-0.92** (previously 0.67-0.86).
- The four-pillar gap review is complete (notes/07). The largest literal
  deviation is Pillar I's "LLM Semantic Ranking", which needs to be written up
  as an evidenced design decision; Pillar II's selective erasure, over-generality
  guidance and slot decay are scheduled as the next day's experiments.
