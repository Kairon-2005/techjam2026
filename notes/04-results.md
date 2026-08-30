# 04 — Experimental results and ablations

## Overview

| stage | TechnicalScore | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| official weak baseline | 0.1067 | 0.125 | 0.068 | 9.81 |
| stage 1: dialogue policy (H1+H2+stopwords) | 0.7536 | 0.870 | 0.560 | 3.48 |
| stage 2: reranking layer | 0.9273 | 0.995 | 0.834 | 2.02 |
| stage 3: cross-validated tuning | **0.9280** | 0.995 | 0.837 | 2.03 |
| *(realistic joint upper bound)* | *0.9822* | *1.000* | *1.000* | *1.89* |

**8.70x over the official baseline.** Pure Python standard library, zero network,
zero GPU, no learned or neural model weights.

## Individual contribution of each reranking feature

Measured against "BM25-only reranking" as the baseline (0.7536, identical to no
reranking at all, which serves as a regression check on the refactor):

| feature | meaning | added alone | delta |
|---|---|---|---|
| `w_pop` | normalized `(rating/5) * log1p(rating_number)` | 0.8673 | **+0.114** |
| `w_phrase` | substring hit rate of disclosed constraint strings in the product's full text | 0.8124 | +0.059 |
| `w_idf` | IDF-weighted coverage of query terms | 0.7848 | +0.031 |
| `w_cat` | word overlap between the stated category and the product's categories | 0.7617 | +0.008 |
| `w_exact` | a constraint **exactly equal to** one of the product's feature/detail values | +0.0045 | increment in combination |
| `w_field` | the constraint hits in features/details rather than description | +0.0031 | increment in combination |

### Negative results (equally important)

| attempt | result |
|---|---|
| `pop_mode=pct / pct2 / pct4` (percentile transforms) | **worse** (0.9273 to 0.9155 / 0.9130 / 0.9079). Everything in the candidate set is popular, so a percentile saturates and loses discrimination; `log1p` still separates 6846 from 400 at the top end |
| `w_pos` (position of the constraint within the product's field list) | **worse** (0.9273 to 0.9247 / 0.9223 / 0.9214) |
| `phrase_idf` (weighting constraints by term IDF) | **worse** (0.9273 to 0.9191) |
| `candidates=400` with a high `w_pop` | **much worse** (as low as 0.7189). With too large a pool, popularity pulls in irrelevant bestsellers |

## Key finding: why the popularity prior is so strong

| | median over the whole catalog | median over the 200 targets |
|---|---|---|
| `rating_number` | **12** | **6846** |

- The **median percentile** of target products in the catalog's review-count
  distribution is **0.995**.
- **86%** of targets fall in the most popular 10%; **96%** in the most popular
  25%; only **2%** in the bottom half.

The cause is in the participant kit README: sessions are "sampled
deterministically from the official Clothing **5-core** leave-last-out split".
5-core filtering plus real purchase records implies a strong popularity bias.

**This is a transferable modelling insight** (popularity priors hold in real
recommender systems too), not an exploitation of a simulator mechanism.

## A trade-off that needs a decision: `w_card`

The `w_card` feature reimplements the simulator's `intent_card()` generation
locally (material regex inserted at position 0, color at position 1, price
appended, first 4 taken after cleaning), then checks whether the disclosed
constraints **exactly hit** the four strings that product would generate. This is
thorough reverse-engineering of the simulator.

Measured gain: **0.9273 to 0.9306, only +0.0033.**

The reason is that `w_exact` (a constraint equal to one of the product's own
feature values) already captures most of the same signal.

**Recommendation: do not enable it.** Because:
1. It is worth only 0.3%, which is a poor return.
2. It is the only component in the project that *looks* like cheating, and it
   would contaminate the narrative of the whole approach.
3. It is **fragile**: the official specification notes that "If natural-language
   paraphrasing is added by the organizer, it cannot decide correctness" -- once
   the private set adds paraphrasing, exact string hits stop working, whereas
   `w_phrase` and `w_exact` degrade gracefully.

The switch is retained in the code and defaults to off, and appears in the report
as an ablation we measured and deliberately abandoned.

## Generalization

5-fold stratified cross-validation (stratified by `scenario_type`, preserving the
40/40/15/5 mix). For each fold, the best configuration is selected using only the
other 4 folds and then scored on the held-out fold.

```
fold 0: 0.9360   fold 1: 0.9587   fold 2: 0.8981   fold 3: 0.9157   fold 4: 0.9313
mean 0.9280      sd 0.0203
```

**"Overfitting gap = 0" is a mathematical identity, not proof of no
overfitting.** The same configuration wins on all 5 training splits, and the 5
held-out folds exactly partition the 200, so the mean necessarily equals the
full-set score. What it does show is that **configuration selection is stable
across folds** (weak robustness evidence), not an unbiased estimate of
generalization.

Extrapolating the fold standard deviation to an 800-session private set:
`0.0203 * sqrt(40/800) = 0.0045`, so the private score would be expected around
**0.928 +/- 0.005** -- not counting any other differences between the two splits.
The scenario mix being identical on both sides is favourable here.

## Remaining headroom

```
HR@10 : 0.995 -> 1.000                        at most +0.0025
Eff   : 0.898 -> 0.911 (realistic lower bound) at most +0.0025
MRR   : 0.837 -> 0.995                        at most +0.0529   <- nearly all of it
```

**Minimum MTTC:** an earlier derivation assuming "browsing hits at turn 2 at the
earliest, boundary at turn 3" gave 1.890 and was rejected in review -- the
measured hit-turn distribution has 25 browsing and 3 boundary sessions hitting on
turn 1 (category plus popularity is enough on the first turn). The correct lower
bound is notes/03's 1.390 (the only hard constraint is that an override cannot
convert before it fires), giving an efficiency ceiling of 0.961. The remaining
headroom from 0.898 is therefore +0.0126 of score rather than +0.0025 -- still far
smaller than MRR's, so the conclusion is unchanged.

Per-scenario performance at this point:

| scenario | n | HR | MRR | MTTC | hit-turn distribution |
|---|---|---|---|---|---|
| buying | 80 | 0.988 | 0.819 | 1.56 | `{1:47, 2:29, 3:3}` |
| browsing | 80 | 1.000 | 0.769 | 1.80 | `{1:25, 2:46, 3:9}` |
| intent_override | 30 | 1.000 | 0.921 | 3.60 | `{3:12, 4:18}` |
| boundary | 10 | 1.000 | 0.900 | 2.60 | `{1:3, 3:5, 4:2}` |

**Conclusion: efficiency and recall are both essentially maxed out, and
everything left is "lift ranks 2-5 to rank 1".** Of 199 hits, 142 are already
first, and the remaining 57 are spread over ranks 2-10. Browsing (30 non-rank-1)
and buying (22) are the main sources.
