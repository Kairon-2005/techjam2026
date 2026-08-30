# 05 — Generalization, comparison with the frontier, remaining headroom

Five questions, all answered from this round of experiments (`lab/stress.py`,
plus roughly 20 new rows in `experiments.jsonl`).

## Q1 What dataset are we running on? Is generalization guaranteed?

**Data:** a frozen subset of Amazon Reviews 2023 (McAuley Lab, UCSD),
`Clothing_Shoes_and_Jewelry`: a 50k-product catalog plus 200 public sessions.
Sessions are sampled deterministically from the official **5-core
leave-last-out** split; the "conversations" are not real conversations but are
generated on the fly by the simulator from the target product's metadata. The
private test set is 800 in-distribution sessions (different users, different
target products, the same 40/40/15/5 scenario mix).

**Three layers of generalization risk, each checked:**

1. **Statistical sampling** (new in-distribution samples): 5-fold stratified CV
   gives a fold standard deviation of 0.0203, extrapolating to 0.928 +/- 0.005
   over 800. Low risk.
2. **The popularity prior failing:** with `w_pop=0` the score is 0.867 (-0.061).
   But the cause of the prior (5-core sampling) holds for the private set too
   (the README states the same sampling pipeline). Low risk. `half_pop` (w=2)
   still gives 0.925, so it is insensitive to the weight itself -- a flat
   optimum.
3. **Template paraphrasing** (the organizer reserves the right to add it): this
   is the only risk confirmed by measurement. Before the fix, the agent dropped
   to 0.776/0.807/0.671 under three paraphrase styles; after adding
   template-independent cue parsing plus noise/override regular expressions it
   recovered to **0.918/0.923/0.856**, with zero regression on the clean
   simulator (the identity style reproduces 0.9280 exactly, and the harness
   itself was bug-fixed and validated first). The verbose style retains a -0.07
   residual caused by category-extraction contamination; known, and convergeable.

## Q2 Compared with the frontier: what was adopted, what was discarded

| frontier technique | decision | basis (measurement / rules) |
|---|---|---|
| multi-route recall (keyword + vector) | **vector route discarded** | recall@200 = 1.000 on the public development set: lexical recall misses nothing, so a vector route could only change ranking -- and ranking is cheaper with features |
| cross-encoder reranking | discarded | roughly 210,000 forward passes is infeasible on CPU; linear feature reranking already reaches 0.93 |
| LLM semantic ranking (the brief's Pillar I) | discarded on the main path | asymmetric risk from the no-network clause; the interface is retained as an optional enhancement |
| RL dialogue policy (CRM/EAR/UNICORN) | discarded | 200 samples cannot train RL, and the optimal question under this simulator is a constant policy |
| information-gain questioning (CPR/SCPR) | partly adopted | retained as a counterfactual experiment still to be run (report value), not a source of score |
| popularity prior (standard in industrial ranking) | **adopted** | +0.114 from a single feature, the largest in the project |
| structured slot state (DST) | **adopted** | `parse_message` plus the override-keep policy |
| hybrid RRF fusion | unused | only one recall route, so nothing to fuse |

In one sentence: **this is a problem where retrieval is already saturated on the
development set, so the whole complexity budget went into ranking features and
state management rather than recall architecture** -- and every discard has a
measurement behind it.

## Q3 How much headroom is left

Score 0.9291 (after route-conditioning). The realistic upper bound is 0.9822.
Of the remaining 0.053:

- MRR 0.841 to 0.995 is worth +0.046 (87% of it): 57 sessions at ranks 2-10
  moving to rank 1.
- The HR tail (1 remaining miss) plus the efficiency tail: about +0.005
  combined.
- **Assessment: diminishing returns are already obvious.** Of 8 new
  features/transforms this round only 2 were positive, the largest at +0.0033.
  Going from 0.93 to 0.95 may cost as much as going from 0.10 to 0.93 did.
  **Recommendation: freeze the main line and spend the remaining time on
  robustness (the verbose residual), the report and the ablation presentation**
  -- 65% of the rubric does not look at this score.

## Q4 Designs that better fit a real e-commerce setting

Implemented and effective: **intent-routed differentiated ranking** -- browsing
raises the popularity weight (an exploring user has no precise constraint, so
popular is relevant), while buying keeps exact constraint matching dominant.
`route_overrides: {browsing: {w_pop: 6}}` moved 0.9280 to 0.9291. The increment is
small, but this is standard practice in real e-commerce retrieval (query intent
to ranking profile) and it is implemented purely as configuration, which makes a
clean section in the report.

Directionally right but not rewarded by this simulator (write-ups for the
discussion): hard price/budget filtering (budget appears 0 times in the
constraints), diversity/MMR (the evaluation recognizes only the unique target, so
diversity purely costs rank), the natural-language quality of clarification
questions (the simulator reads only the `ask_attribute` field), and cross-session
personalization (the profile is a constant plus weak tags). **The mismatch
between the evaluation metric and real e-commerce objectives is itself material
for the Innovation and Problem Insight section.**

## Q5 Performance work that costs no accuracy

Measured latency profile (a cloud container with 2 vCPUs, slower than an
M-series MacBook):

| item | measured |
|---|---|
| catalog index build (one-off) | 15.9s |
| second instantiation in the same process (cache hit) | 0.2ms |
| turn 1 (about 4 terms) | 35ms |
| turns 3-4 (about 30 terms, saturated) | 64ms |
| a whole 4-turn session | about 207ms |
| token consumption | **0** |

That is already two to three orders of magnitude faster than any LLM approach.
Free improvements still available:
1. The FTS query string takes only the first `term_cap` terms -- **latency grows
   linearly with term count**. Under this evaluation term accumulation is bounded
   (4 constraints in total), so it is not a problem; a real deployment would need
   a term-budget policy.
2. On a noise turn the terms do not change, so the previous ranking can be reused
   (a protocol-level, zero-risk cache).
3. Persisting the index (file-backed SQLite) would amortize the 16s build into a
   roughly 1s load -- useful for submission-package startup experience.
4. Reranking does dictionary lookups plus float weighting over 100 candidates,
   about 1ms, and is not the bottleneck.

**Assessment: performance already exceeds the task's requirement by two orders of
magnitude, so no further investment.** The evidence for the 15% Feasibility
weight is sufficient (0 tokens, single CPU core, 207ms per session).
