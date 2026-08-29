# Phase 6C design — profile credibility, and personalization only if it is earned

**Revision 2. Design and pre-registration. No implementation.** Phase 6B2 is
closed through R2 (`notes/36`); this is the next phase's contract, written
before any 6C code exists and before any 6C measurement has been taken.

Revision 2 closes nine gaps found in review of revision 1, every one of which
would have produced a measurement that looked clean and meant something other
than it appeared to:

1. a tag with **zero** candidate support was being classified `specific_informative`;
2. the snapshot was to be taken **after** reranking, making its evidence circular;
3. tag matching would have been implemented twice, in credibility and in ranking;
4. D3's lower bound rejected the informative singleton case;
5. the Arm B instrument check is **not** demonstrably distinctive;
6. session aggregation was undefined, so per-turn and per-session numbers were interchangeable;
7. D1–D3 can be passed by rare random noise with no relation to the target;
8. 6C2 could have been adopted on "clean did not regress" plus an oracle gain;
9. `notes/30` and several live comments still carry the unqualified turn count.

Phase 6C is split, and the split is the point:

| | | |
|---|---|---|
| **6C1** | profile credibility **shadow evaluation** | fully pre-registered below |
| **6C2** | **controlled** personalization | **designed only if 6C1 proves the profile signal is discriminative** |

6C2's *control-phase gates* are pre-registered here, now, so they cannot be
chosen after seeing 6C1's numbers. Its *design* is deliberately not written:
writing it now would create a sunk cost pushing 6C1 towards the answer that
justifies it, which is the failure 6B2's stop condition existed to prevent.

## What the data actually contains

Read this first, because it is the reason 6C1 exists as a separate phase rather
than as a preamble to building personalization.

Dataset characterization over `data/public_set.jsonl`, 200 samples. This is a
property of the corpus, not an agent measurement — no agent runs, nothing is
recorded to `lab/results.jsonl`, and it is reproducible in one command:

```bash
python3 -c "import json,collections,itertools,statistics; rows=[json.loads(l) for l in open('data/public_set.jsonl')]; sets=[frozenset(str(t).lower() for t in (r['user_profile'].get('preference_tags') or [])) for r in rows]; c=collections.Counter(sets); j=[len(a&b)/len(a|b) for a,b in itertools.combinations(sets,2) if (a|b)]; print(len(sets), len(c), c.most_common(1), round(statistics.median(j),4))"
```

| | |
|---|---|
| samples | 200 |
| distinct `preference_tags` **values** | **9** |
| distinct tag **sets** | **43** |
| most common set (`comfort, fit, material, style`) | **47 samples** |
| `fit` appears in | **163 / 200 (81.5%)** |
| P(two random users have an identical tag set) | **0.1024** |
| median pairwise Jaccard between users | **0.50** |

The nine tags are `fit`, `material`, `comfort`, `style`, `durability`,
`performance`, `warmth`, `weather`, `general shopping`. **Every one names a
preference *dimension*, not a *value*.** "fit" is not a constraint a retrieval
system can filter or rank on the way "cotton" or "blue" is; it is the name of
an axis along which a value might have been given, and was not.

**This is a strong prior that the official profile carries little usable
per-user signal — and it is a prior, not a result.** 6C1's job is to measure it
properly and be willing to conclude exactly that. Nothing in 6C1's classifier
is allowed to encode this expectation; see "the generic rule is local, not
corpus-derived" below.

## Constraints, all of them binding

* **Only the evaluator-provided `user_profile`.** No other source of user
  information, invented or derived.
* **No cross-session memory keyed on the session id.** The evaluator mints
  `session_id = f"public_{uuid.uuid4().hex}"` per sample
  (`evaluator/local_evaluator.py:227`), so a cross-session store would be
  either permanently empty or keyed on something that is not a user — and any
  scheme that *did* accumulate across samples would be reading the evaluation
  set, not modelling a customer.
* **No model fine-tuning.** Nothing is trained.
* **`w_profile` and `w_profile_adaptive` stay `0.0`** for the whole of 6C1 and
  for 6C2's shadow stage. They may only move inside 6C2's control stage, and
  only after its gates are met.
* **The profile decision is bounded, deterministic and pure**, and **builds no
  lazy catalog index** — `_cat_index`, `_facet_index`, `_dense_index` unchanged
  before and after, asserted the way `notes/36`'s hard gate is: the set of
  built indexes must be *identical* across modes, not merely `None`.
* **Shadow mode is bit-exact** on score, rendered message, recommendations and
  `ask_attribute`. Any movement is a defect, not a result.
* **Official generic profiles and synthesized informative profiles are reported
  separately, never averaged into one number.**
* **Out of scope entirely:** the sealed holdout, the reranker, and any
  downstream weight tuning.

## 6C1 — profile credibility shadow evaluation

### What already exists

`starter/context.py` already computes a rudimentary credibility flag inside the
Phase 6A shadow snapshot: a tag is credible if it was not already stated as an
active positive slot value and its coverage in the ranked window is at or below
`profile_max_coverage` (0.5). 6C1 **replaces that binary with a classified,
reported decision**; it does not inherit its evidence.

### The one shared match kernel

**Tag matching is defined once and used everywhere.** Credibility coverage,
the target-alignment diagnostic, and any future 6C2 reranking all call the same
pure function. Two implementations of "does this tag match this product" would
mean the thing measured in 6C1 is not the thing acted on in 6C2 — a divergence
that would show up as an unexplained gap between shadow and control, exactly
the class of defect the 6B2 write-tracking oracle existed to catch.

```
profile_match(tag: str, text: str) -> bool        # pure, no state, no catalog
match_count(tag, window_texts) -> int             # 0..len(window)
coverage(tag, window_texts) -> float              # match_count / len(window), 4dp
```

`match_count` and `coverage` are two views of one pass; `coverage` is
`match_count / |window|` and never computed separately.

**Matching is word-boundary, not substring.** The existing
`profile_coverage()` uses `tag in blob`, so `fit` matches *outfit*, *fitted*
and *benefit*. That inflates coverage for exactly the tags most in question and
would reject them as `generic` **for the wrong reason** — a rejection that
looks like the predicted answer while being an artefact of the matcher.

Because this changes an existing function's semantics, 6C1's first deliverable
reports **both** matchers side by side — per-tag `match_count` and coverage
under substring and under word-boundary — so the choice is evidenced rather
than assumed. `profile_coverage()` currently feeds shadow telemetry only and
`w_profile` is `0.0`, so the change cannot move a score; that must be
demonstrated bit-exact, not asserted.

### The five categories

Per **tag**, mutually exclusive, first match wins. The precedence is fixed here
so the classification cannot depend on evaluation order:

| # | category | definition |
|---|---|---|
| 1 | **`conflicting`** | the tag matches a value the customer explicitly negated this session (`polarity < 0`), or one superseded by an override (`_uncredible()`'s blocked set). Acting on it would contradict a stated preference. |
| 2 | **`duplicated_session_evidence`** | the tag equals an active positive slot value already stated this session. The session already has it, at higher confidence and with provenance. A prior that repeats stated evidence adds nothing and double-counts. |
| 3 | **`unsupported`** / `no_candidate_support` | **`match_count == 0`.** Nothing in the bounded window matches the tag at all. |
| 4 | **`generic`** | `coverage > profile_max_coverage`. A tag that most surviving candidates already satisfy cannot reorder them. |
| 5 | **`specific_informative`** | **`match_count >= 1`** and coverage at or below the ceiling, not already stated, not contradicted. |

**`unsupported` must precede `generic`, and both must precede
`specific_informative`.** Revision 1 had no `unsupported` category, so a tag
matching *nothing* in the window fell through `generic` (coverage `0.0` is not
`> 0.5`) and was classified `specific_informative`. That is the worst available
error: a tag with no candidate support would have been counted as the evidence
that personalization is viable, and D1 — "sessions with at least one credible
tag" — would have been measured largely on tags that match nothing. The
distinction is *zero support* versus *too much support*, and they fail for
opposite reasons.

Per **session**, one verdict:

| category | definition |
|---|---|
| **`no_signal`** | `preference_tags` is absent, empty, or every entry is empty after normalization — there is nothing to classify. Distinct from "every tag was classified `unsupported` or `generic`", which is a *finding* about the tags, not their absence. |

`no_signal` is deliberately **not** the union of the failure categories.
Collapsing "the user told us nothing" into "what the user told us was useless"
would make the two indistinguishable in the telemetry, and they call for
different conclusions — the first is a data gap, the second is a finding.

### The generic rule is local, not corpus-derived

`generic` is decided **only** by coverage in this turn's bounded window. It is
tempting to add a frozen "abstract dimension" vocabulary — `fit`, `comfort`,
`style` and the rest — and classify those generic by name. **That is
prohibited in 6C1**, because the nine official tags *are* that vocabulary: a
classifier told in advance that the official tags are generic would return the
conclusion it was given, and 6C1's finding would be an artefact of its own
constant table.

The corpus-level statistics above are reported as **dataset characterization
alongside** the classification, never as an input to it.

### The call site is pinned, and it is pre-rerank

**After `_candidates()`, before `_rerank()`** — `starter/agent.py:648-650`:

```python
cands, trace = self._candidates(state, turn_cfg, limit)   # <- snapshot HERE
if turn_cfg["rerank"] and cands:
    ranked = self._rerank(cands, state)
```

The window is the first `pool_depth` entries of `cands`: the candidate
population **as retrieval produced it**, before any ranking that a future
`w_profile` would participate in.

**A post-rerank snapshot is not acceptable, because it creates circular
evidence.** If 6C2 ever lets the profile influence `_rerank()`, then a
credibility measurement taken *after* reranking is measuring a window the
profile helped select. Coverage would be computed over candidates that were
promoted partly *because* they matched the tags, so a tag would raise its own
support and "the profile is informative" would be true by construction. The
6C1 measurement and any 6C2 control must consume **the same pre-profile
candidate population**, or the shadow evidence does not describe the thing the
control would do.

**The existing Phase 6A snapshot is therefore not reusable as the 6C1 basis.**
`_shadow_context(state, trace, ranked, turn, turn_cfg)` is called with
`ranked` — post-rerank — so its `profile_coverage` is already the circular
quantity. That is harmless today because `w_profile` is `0.0` and the snapshot
is telemetry, and it would stop being harmless the moment 6C2 existed. 6C1
takes its own snapshot at the pinned site and does not inherit 6A's numbers.

This also fixes the window's identity: `_candidates()` returns
`list[tuple[str, float]]` of length `limit = max(top_k, depth)`, and the
profile window is its first `pool_depth` asins, in retrieval order.

### The decision contract

A pure function over bounded primitive summaries, in the shape R2 settled on —
the host performs the scans, the decision receives the results:

```
ProfileSnapshot   tags (<=8, normalised), stated values, negated values,
                  blocked values, turn index
ProfilePolicy     profile_max_coverage, and nothing else in 6C1
ProfileCoverage   tag -> coverage in the bounded window   (host-scanned)
      -> ProfileDecision(per_tag_category, session_verdict, credible_tags,
                         reasons)
```

Bounded by construction: at most `MAX_PROFILE_TAGS` (8) tags against at most
`pool_depth` (30) candidates = **240 substring checks against text already in
memory**, no catalog-wide scan, no index. Deterministic: no dict-ordering
dependence, ties broken explicitly, same inputs → same output.

### Mode

`profile_context_mode = "off" | "shadow"`. **There is no `"control"` in 6C1.**
Adding one would let a reviewer — or me — turn it on before the evidence
exists. `"control"` is introduced by 6C2 or not at all.

### Session aggregation, defined exactly

Per-turn and per-session numbers are not interchangeable, and revision 1 left
which one a gate used undefined — so a gate could have been met by one verbose
session contributing twenty turns.

* **Gates D1, D2, D3 and D5 are evaluated on the FIRST RECOMMENDATION TURN of
  each session**, one observation per session. That is the turn where a
  personalization prior would first act, before the session has accumulated
  stated evidence that would make the profile redundant anyway.
* **Later turns are evolution telemetry only.** They are reported — how
  classification drifts as slots accumulate is exactly the interesting
  secondary question — and they are **never** an input to a gate.
* **D2 uses only sessions whose first-turn credible set is NON-EMPTY.** Jaccard
  is undefined on two empty sets and `0/0` would have to be imputed; imputing
  `1.0` (identical) or `0.0` (maximally separated) each rig the gate in
  opposite directions. Sessions with an empty credible set are already counted
  by D1, and counting them again in D2 would double-penalise the same fact.
* **Minimum sample counts.** D2 requires **≥ 30** sessions with a non-empty
  first-turn credible set; D5 requires **≥ 30** eligible sessions. Below the
  minimum the gate's verdict is **`insufficient_data`**, which is neither pass
  nor fail and **may not be reported as either**. A gate evaluated on eight
  sessions is not a gate.
* All category counts are reported **raw**, per turn and per session
  separately, never as a rate alone.

### Measurements, reported in two arms that are never averaged

**Arm A — official profiles**, seven scenarios, the real
`user_profile` from the public set.

**Arm B — synthesized informative profiles.** Three components, because the
existing scenario alone is **not a sufficient instrument check**.

> **Every part of Arm B is an oracle construction and an upper bound, not an
> achievable gain.** Its tags are derived from the answer. It exists to
> separate *"the data has no signal"* from *"we cannot see the signal that is
> there"* — the job the negative controls did for 6B2's comparator. **No number
> from Arm B may ever be quoted as a result the agent achieved, and Arm B can
> never justify adoption.**

**B0 — the existing `profile_informative` scenario, retained but demoted.**
`lab/scenarios.py:713` takes the **first five tokens** of the target's
title/features/details, minus a four-word stoplist. First five, not
*distinctive* five: it will happily emit `womens`, `cotton`, `shirt` — tokens
that may match most of the window and be classified `generic`, or brand and
size fragments that match nothing and be classified `unsupported`. **A failure
of B0 is therefore ambiguous** between a broken classifier and a badly
constructed control, which is precisely what an instrument check must not be.
B0 is reported, and it cannot on its own satisfy D4.

**B1 — constructed fixtures with exact known match counts.** Unit-level, no
scenario run, built from a synthetic window whose texts are written by the
test. For each category, at least one fixture whose expected classification is
determined by construction rather than by inspection of real data:

| fixture | window | expected |
|---|---|---|
| exact singleton | 1 of 30 candidates contains the tag | `specific_informative`, `match_count == 1` |
| mid support | 10 of 30 | `specific_informative`, `coverage == 0.3333` |
| at the ceiling | 15 of 30 | `specific_informative` (coverage `0.5` is **not** `> 0.5`) |
| over the ceiling | 16 of 30 | `generic` |
| zero support | 0 of 30 | `unsupported` |
| substring trap | window contains *outfit*, tag is `fit` | `unsupported` under word-boundary; `generic` under substring — the two matchers **must** disagree here, and the fixture exists to prove the kernel is the one claimed |
| stated | tag equals an active positive slot value | `duplicated_session_evidence` |
| negated | tag equals a negated value | `conflicting` |
| precedence | tag is negated **and** has zero support | `conflicting` — the higher rule wins |

The boundary fixtures pin `>` against `>=` at the ceiling, which is the
likeliest silent inversion, and the precedence fixture pins the order rather
than assuming it.

**B2 — an end-to-end oracle diagnostic on eligible sessions only.** The same
synthesized-profile construction, restricted to sessions where **both**:

1. the ground-truth target is present in the pre-rerank candidate window, and
2. at least one synthesized tag has `match_count >= 1` in that window.

Sessions failing either condition are excluded and **the exclusion rate is
reported**, because a diagnostic that silently drops most of its population is
reporting on a subset it has not described. B2 is what D4 is evaluated on.

**If Arm B fails, B0/B1/B2 separate the causes.** B1 failing means the
classifier is wrong — a unit-level defect, fixable without any scenario run.
B1 passing while B2 fails means the classifier is right and the *scenario
construction* is weak, i.e. first-five-tokens does not reliably produce
supported, distinctive tags — a control-construction defect, not a data
finding. Only B1 and B2 both passing licenses any statement about Arm A.

Reported per arm, never pooled:

* the five-category distribution, as **raw counts** over turns and sessions;
* sessions with ≥ 1 `specific_informative` tag;
* median coverage of credible tags;
* median pairwise Jaccard between sessions' **credible** tag sets;
* the dataset characterization above.

### The gate that decides whether 6C2 is designed at all

Pre-registered now. All evaluated on the **first recommendation turn** of each
session, on **Arm A** except D4:

| | criterion | threshold |
|---|---|---|
| **D1** | sessions with ≥ 1 `specific_informative` tag | **≥ 20%** |
| **D2** | median pairwise Jaccard between non-empty credible tag sets | **≤ 0.30**, n ≥ 30 |
| **D3** | credible tags have support and are not ubiquitous | **`match_count >= 1` and `coverage <= 0.50`** |
| **D4** | *instrument check:* **B1 and B2 both pass** | **required** |
| **D5** | *target alignment*, lab-only | see below, n ≥ 30 |

D1 asks whether personalization could fire often enough to matter. D2 asks
whether it would say anything *different* per user — the raw profiles are
already at 0.50, so this tests whether credibility filtering *creates*
separation rather than inheriting it.

**D3 no longer has a lower coverage bound.** Revision 1 required coverage in
`[0.05, 0.50]`, which at `pool_depth = 30` rejects any tag matching fewer than
1.5 candidates — i.e. it rejects the **singleton hit**. A tag matching exactly
one candidate in the Top-30 is the *most* informative case available, not the
least: it identifies a single candidate uniquely. The floor would have
discarded the best signal the profile could carry. D3 is now exactly the
`specific_informative` condition — `match_count >= 1` and `coverage <= 0.50` —
with the zero-support case handled by the `unsupported` category rather than by
a coverage threshold that cannot distinguish "matches nothing" from "matches
one thing".

**D4 is the negative control on the measurement itself**, and it now requires
**B1** (constructed fixtures) and **B2** (eligible-session oracle diagnostic),
not the existing scenario alone. See Arm B above for why B0 cannot carry it.

### D5 — target alignment, evaluated only in the lab

**D1–D3 are not sufficient.** They ask whether credible tags *exist*, are
*distinct between users*, and have *usable support*. A set of rare tags that
match arbitrary candidates for reasons unrelated to what the customer wants
passes all three. Rare random noise is, by construction, well-supported and
well-separated. D5 asks the question the other three do not: **do the credible
tags point at the right product?**

**The target identity never enters the Agent or the ProfileDecision.** The
decision function's inputs are unchanged — tags, session evidence, and the
match kernel's output over the candidate window. D5 is computed in `lab/`,
afterwards, by joining the recorded `ProfileDecision` against the sample's
ground truth. If the target could reach the decision, the whole phase would be
measuring a leak. This is asserted the way the lazy-index gate is: a test that
the decision's inputs contain no ground-truth field, not a promise.

**Eligible sessions**, and the rate is reported separately: the ground-truth
target is in the pre-rerank window **and** the session has ≥ 1 credible tag.
Sessions ineligible for the first reason say something about retrieval, not
about profiles, and pooling the two would let a retrieval failure read as a
profile failure.

For each eligible session, using the **shared kernel**:

```
align(candidate) = |{t in credible_tags : profile_match(t, text[candidate])}| / |credible_tags|
margin           = align(target) - median{ align(c) : c in window, c != target }
win              = margin > 0          # exact ties count as NOT a win
```

| D5 requirement | threshold |
|---|---|
| win rate over eligible sessions | significantly **> 0.5**, one-sided exact binomial, **α = 0.01** |
| median margin | **≥ 0.10** |
| eligible sessions | **≥ 30**, else `insufficient_data` |

The binomial test is against the null that credible tags rank the target no
better than a coin flip — which is exactly what rare noise does. The median
margin is required *as well*, because with enough sessions a trivial effect
becomes significant, and a significant-but-trivial alignment is not a reason to
build personalization. Ties counting as non-wins is the conservative choice and
matters here: with few credible tags, `align` is coarse and ties are common.

**D5 is a gate on 6C1's conclusion, not on 6C2's adoption.** It decides whether
"the profile signal is discriminative" may be claimed at all.

**Outcomes:**

* **Arm A passes D1, D2, D3 and D5, and D4 holds** → 6C2 is designed, in its
  own pre-registration.
* **Arm A passes D1–D3 but fails D5** → recorded conclusion: *credible tags
  exist and are separable between users, but do not point at the target.* **6C2
  is not designed.** This is the outcome D5 was added to catch, and it is
  indistinguishable from success without it.
* **Arm A fails D1–D3 while D4 holds** → recorded conclusion: *the mechanism
  works; the official profile carries no usable per-user signal.* **6C2 is not
  designed for this submission.** A real finding about the data, and the one
  the dataset characterization predicts.
* **B1 fails** → the classifier is wrong. Unit-level defect, fixable with no
  scenario run. No conclusion about Arm A.
* **B1 passes, B2 fails** → the classifier is right and the oracle scenario is
  weakly constructed. Fix the construction; **do not** record this as a data
  finding, and do not proceed to 6C2.
* **Any gate returns `insufficient_data`** → that gate is neither passed nor
  failed, and 6C2 does not proceed on the strength of the others.

### Stop conditions

Stop immediately if: shadow mode moves score, message, recommendations or
`ask_attribute` by any amount; the profile decision acquires a callback into
the host, a catalog reference or an index build; any classification depends on
a corpus-derived constant; `w_profile` / `w_profile_adaptive` move from 0; the
snapshot is taken anywhere but between `_candidates()` and `_rerank()`; tag
matching is implemented anywhere but the shared kernel; or any ground-truth
field reaches the Agent or the `ProfileDecision`.

### Predictions

1. **Arm A fails D1.** The nine official tags are dimension names, and the
   `generic` coverage rule will reject most of them — `fit` and `comfort` are
   near-ubiquitous in apparel text. If Arm A passes D1 comfortably, I have
   probably mis-implemented the coverage rule, and that is the first thing to
   check rather than a result to celebrate.
2. **Arm A fails D2 even where D1 passes.** With 43 distinct tag sets over 200
   samples and a median raw Jaccard of 0.50, credibility filtering has to
   *remove shared tags asymmetrically* to create separation. There is no reason
   it should.
3. **Arm B passes all three**, because its tags come from the target product
   and are values rather than dimensions. If it does not, D4 fires.
4. **`duplicated_session_evidence` is rarer than expected.** The official tags
   are dimensions and the session states values, so they mostly will not
   collide as strings — which is itself evidence that the profile and the
   session are describing different things.
5. **`unsupported` is common under word-boundary matching and rare under
   substring matching**, and the gap between the two matchers on the same tags
   is the clearest single number this phase will produce about whether the
   existing `profile_coverage()` was measuring anything real.
6. **If Arm A somehow reaches D5, D5 fails.** Nine dimension words shared
   across 200 users have no mechanism by which they would rank one user's
   target above another's. If D5 passes on Arm A, the first thing to check is
   whether the target leaked into the decision, not whether personalization
   works.

## 6C2 — controlled personalization, conditional

**Not designed here.** Only its gates are fixed, so that a later design cannot
choose thresholds that its measurements happen to meet.

If 6C1's gate opens, 6C2 gets its own pre-registration covering a shadow stage
(`w_profile` still 0) followed by a control stage. The control stage may not be
adopted unless **all five** of the following hold, each measured through the
committed harnesses (`lab/record.py`, `lab/shards.py`, `lab/benchmark.py`) and
each citable under `lab.provenance.citable()`:

| gate | requirement |
|---|---|
| **official benefit** *(new in revision 2)* | **`score_default` may only enable personalization if the OFFICIAL profile arm shows a real gain.** Either (a) the official composite on `clean` improves by **≥ +0.002 absolute**, or (b) a pre-registered official conversion metric improves — **MTTC on `clean` decreases by ≥ 0.05 turns** — with no composite regression. Measured on **Arm A, real profiles**. See the note below on why "clean stayed equal" is not enough. |
| **clean regression guard** | `clean` **must not decrease at all** from its measured baseline (`0.932067`), and no official slice may regress. Necessary, **not** sufficient — it is a floor, and the official-benefit gate is the bar. |
| **informative-profile gain** | on Arm B, score improves by **≥ +0.01 absolute** over the same scenario with personalization off, exceeding the across-seed standard deviation. A gain inside its own noise is not a gain. **Diagnostic only:** an oracle upper bound that may never justify default adoption. |
| **latency** | per-branch through `lab/benchmark.py` under the R2.1 rule: absolute overhead **≤ 0.25 ms**, and the ratio gate applies only where the control median is **≥ 0.10 ms**. Seven fresh-process paired repetitions; four of seven is not a result. |
| **memory** | no new index built in any mode; the shadow snapshot stays within `MAX_ENTRIES` (64) and `MAX_BYTES` (4096); peak RSS delta **≤ 5 MB** from the harness's recorded `peak_rss_bytes`. |
| **supplementary veto** | `supplementary_dev` **must not regress**. It is a veto signal, not a score — `lab/record.py` carries `source`/`official` on every row precisely so a supplementary number cannot be quoted as an official one. |

Any one of the six failing stops adoption. There is no weighted trade among
them: a latency budget is not purchasable with score.

### Why "clean did not regress" is not enough

Revision 1 would have permitted adoption on a **null result plus an oracle
gain**: `clean` unchanged, Arm B up. That is a feature that demonstrably does
nothing for real users, justified by a scenario whose tags are read off the
answer. Shipping it would add a live code path, latency and a config surface in
exchange for a number no evaluated user could ever produce.

So the burden is on the **official** arm, and it is a burden of *improvement*,
not of *harmlessness*.

**Why ≥ +0.002 on `clean`.** `clean` is deterministic — it has no `reply`,
`mutate`, `init` or `sample_tf` hook, so `record.matrix` gives it a single seed
— and it has reproduced at exactly `0.932067` across every 6B2 and 6B2-R2
measurement. Its across-seed variance is not merely small, it is structurally
absent, so any movement is signal rather than noise and the threshold's job is
to exclude the *trivial*, not the *noisy*. `+0.002` is roughly 0.2% relative.

The stochastic official scenarios are held to **no regression beyond their own
across-seed standard deviation** (measured in the R2 matrix: `uncooperative`
0.0146, `contradiction` 0.0078, `override_genuine` 0.0038, and `vague_start` /
`override_category` 0.0), rather than to a hard equality that their own seed
noise would trip.

**Arm B cannot substitute.** Its tags come from the target product. A gain
there is evidence that *the mechanism can exploit signal when signal exists* —
which is worth reporting, and is not evidence that the shipped configuration
helps anyone.

## Explicitly not in Phase 6C

The sealed holdout. The reranker. Downstream weight tuning. Cross-session
memory. Fine-tuning. Any change to question selection, retrieval, or the
Phase 6B2-R2 controller now in place.
