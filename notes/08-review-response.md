# Response to external review: treat every criticism as a hypothesis and test it

An external review raised 10 criticisms. This document handles only the **4
empirically testable, behavioural ones**. Each was implemented as a configuration
key defaulting to off, quantified with `lab/sweep.py`, and only then accepted or
rejected.

Conclusion first: **2 adopted, 2 rejected by experiment.** For the two rejected,
the criticism's description of the code was entirely correct, but the remedy it
proposed measured as a net loss.

Baseline (default configuration, commit 2f85538):
`0.928708 / HR 0.995 / MRR 0.839361 / MTTC 2.03`

---

## Criticism 3: dual-track routing "has no actual effect"

**The description is correct.** `route_overrides` defaults to `{}`, and all three
routes run an identical pipeline. Separately, 7 of the ablation configurations in
`lab/sweep.py` set `"route": false` -- the agent has no such switch, so those 7
experiment records are **void**.

Two things had to be fixed first. Route classification accuracy measures
**200/200** (a boundary session's first message is word-for-word identical to a
browsing one, so they are informationally indistinguishable and classifying it as
browsing is correct behaviour). And `route_overrides` now applies **for the whole
turn** (previously it affected only rerank weights, so patching `ask_policy` or
`on_override` failed silently -- which is how `RT4` produced a score identical to
the baseline).

Then, whether route-conditioning is worth anything:

| configuration | score | delta |
|---|---|---|
| baseline | 0.9287 | — |
| browsing `w_pop`=5.0 | 0.9283 | -0.0004 |
| browsing `w_pop`=6.0 | 0.9301 | **+0.0013** |
| browsing `w_pop`=8.0 | 0.9261 | -0.0026 |
| buying `w_phrase`=7.0 | 0.9272 | -0.0015 |
| browsing to pool questioning | 0.9282 | -0.0005 |
| browsing candidate pool 200 | 0.9178 | -0.0109 |
| buying candidate pool 50 | 0.9174 | -0.0113 |

The +0.0013 from `w_pop=6.0` is a **noise spike**. Paired analysis:

```
w_pop=6.0   2 sessions better, 7 worse; only 2 of 5 folds improved
            fold-mean delta +0.0030, sd 0.0064 (the sd is twice the mean)
```

The net gain is positive only because the 2 improvements are jumps to rank 1
while the 7 regressions are all small. The neighbourhood (5.0 / 5.5 / 6.5 / 7.0)
does not support it. **Verdict: weight conditioning not adopted.** What was
adopted is the pipeline change making routes apply for the whole turn, plus a
warning on unknown configuration keys (zero change in default behaviour).

## Criticism 4: `on_override="keep"` is not a real intent override

**The description is correct.** By default old phrases and old terms are kept,
and `category` is only assigned when empty. Doing this costs nothing on the
public set because of `evaluator.behavior_for`:
`new_value = hard_constraints[0]` and `old_value = soft_preferences[-1]`, so
**both come from the target product** -- the preference the customer asks us to
forget is still evidence for the correct answer.

So `on_override="slot"` was implemented (selective rewriting by contract
attribute slot: parse the new message, decide which slot is superseded, delete
only that slot's phrases and the terms it contributed **exclusively**, keep
everything else), together with a new `lab/override_stress.py` that replaces
`old_value` with a constraint from **a different product**, making the "obsolete
preference" genuinely misleading (for example, the target is a leather belt and
the obsolete preference says silk).

Averaged over 5 random seeds (30 intent_override sessions per seed):

| policy | score | sd | override HR | override MRR |
|---|---|---|---|---|
| **keep (current default)** | **0.9233** | 0.0014 | **0.973** | **0.864** |
| erase | 0.8458 | 0.0000 | 0.400 | 0.383 |
| decay (keep newest) | 0.9164 | 0.0015 | 0.913 | 0.837 |
| decay_head (keep oldest, the original implementation) | 0.8867 | 0.0039 | 0.773 | 0.479 |
| slot (selective rewriting) | 0.9140 | 0.0030 | 0.927 | 0.763 |

**Even when the override is genuine and the old preference directly contradicts
the correct answer, `keep` still wins.**

Mechanism: this approach **scores and does not filter**. An obsolete constraint
can contribute at most a little wrong credit; it cannot exclude the correct
product. Forgetting, by contrast, genuinely discards evidence -- most of what the
customer disclosed earlier remains valid. This is a transferable conclusion, not
a property of the simulator.

**Verdict: `slot` is not made the default** (-0.0066 on the public set, -0.0093
under genuine overrides). It is retained as an implemented, measured capability,
and the report describes it as an evidenced design decision rather than claiming
slot erasure was performed. A real bug was fixed along the way: `decay`
originally kept the **oldest** evidence with `[:8]` / `[:1]`; keeping the newest
instead gains that strategy +0.030.

## Criticism 5: the question policy is a simulator shortcut

**The description is correct, and quantification makes it look worse: the default
policy asks `other` on 399 of 405 turns (99%).** The simulator treats `other` as
"return any undisclosed constraint", so two turns drain the intent card.

`ask_policy="pool"` was implemented: compute the Shannon entropy of each
attribute's value distribution over the surviving candidate pool and ask the
attribute that **best splits the current pool** -- do not ask about colour when
every candidate is black.

| policy | score | MTTC | share of targeted questions |
|---|---|---|---|
| other (default) | 0.9287 | 2.03 | 1% |
| pool (pure) | 0.9006 | 3.19 | 89% |
| other_then_pool | 0.9282 | 2.06 | 18% |
| **other_then_pool + give_up=1** | **0.9285** | 2.04 | **17%** |

Paired analysis (other_then_pool vs default): **0 sessions better, 0 worse, 0
lost hits** -- ranking quality is identical session by session, and the whole
-0.0005 comes from MTTC.

Why targeted questioning loses under this simulator: the simulator buckets hidden
constraints with `classify_constraint`, and only **hitting that bucket** discloses
anything. When the remaining constraint is "Rubber sole" (bucket = feature),
asking about material/colour/use_case always returns "no preference". Hence the
dry-streak protection: one missed targeted question falls back to open-ended
asking, reducing the cost to **-0.0002** (well below the +/-0.001 noise floor).

**Verdict: adopted.** 17% of turns ask a genuine candidate-pool information-gain
question, at a cost of 0.0002.

## Criticism 9: resource usage needs a more honest account

**The description is correct.** The `order` (position feature) and `card`
(simulator replica) index structures were built unconditionally, yet their
weights `w_pos` and `w_card` **both default to 0.0** -- roughly 80 MB of pure dead
weight. They are now built lazily, gated on their weights:

| | index build | peak RSS |
|---|---|---|
| before (unconditional) | 7.70 s | 430.1 MB |
| **after (on demand)** | **5.07 s** | **392.8 MB** |

The score is identical digit for digit. The whole evaluation process goes from
13.6 s / 653 MB to **11.3 s / 627 MB**. A new short-title index (about 6 MB) used
for recommendation rationale text is included in the table above.

---

## Disposition of the remaining criticisms

- **Criticism 1 (not reproducible from git) -- highest risk, fixed.** The 490
  lines of uncommitted changes plus the notes and lab tooling are frozen as
  commit `2f85538`, with one-command reproduction instructions.
  `lab/sweep.py`'s `git_hash()` now marks a dirty working tree with `+dirty` so
  that "experiment records pointing at a commit that does not contain the code"
  cannot recur.
- **Criticism 2 (CV is not an unbiased estimate) -- correct, wording changed.**
  `lab/tune.py`'s docstring previously said the number "estimates private-set
  performance"; it now says the folds check the stability of the **weight
  selection step** only, since everything upstream (features, stopwords, parsing
  templates, question/override policies) has seen all 200 public sessions.
  Externally the number is always stated as a **public development score of
  0.928708**.
- **Criticism 10 (tests are too shallow) -- correct, addressed.** Added
  `tests/test_agent.py` (25 cases: template parsing, slot classification,
  override state, noise replies, routing, contract schema, `top_k` clamping,
  malicious input, illegal configuration) and `tests/test_score_regression.py`
  (a score lock plus zero tokens). All 30 pass. Also fixed the two `__main__`
  guards in `lab/stress.py` -- `stress v2` previously ran the v1 suite first.
- **Criticisms 6/7/8 (pillar gaps, simulator coupling, missing deliverables)**
  are correct. They concern narrative and deliverables rather than experiments.
  Criticism 8 is the largest current gap.

---

# Four-pillar review, item by item (brief section 4.2 vs the current code)

Re-checked after the review, entirely against measurement. **8 sub-items: 5 met,
1 partial, 2 deliberately abandoned after measurement (with numbers for the
negative results).**

## I. Core Architecture

**Dual-Track Routing -- partially met.**
Route classification measures **200/200 correct** (a boundary session's first
message is word-for-word identical to a browsing one, so they are
informationally indistinguishable; classifying it as browsing is correct
behaviour, not a defect). `route_overrides` now applies **for the whole turn**
(it can patch retrieval depth, question policy and override policy, not just
rerank weights). But **the three routes still run the same pipeline by default**,
because differentiation measured as a net loss (see the criticism-3 table).
On the brief's "high-precision filter track": this approach **never filters, it
only scores.** That is deliberate -- filtering makes recall a ceiling, scoring
does not.

**Multi-Route Retrieval to LLM Semantic Ranking -- partially met.**
keyword yes (FTS5/BM25), category yes (`w_cat`), vector no, LLM no.

The basis for abandoning the dense route is the measured recall curve:

| k | recall@k | sessions not recalled |
|---|---|---|
| 10 | 0.545 | 91 |
| 50 | 0.830 | 34 |
| 100 | 0.995 | 1 |
| **200** | **1.000** | **0** |

**Not one of the 200 sessions is a "cannot retrieve it" case** on the public
development set; the target's median BM25 rank is 8. Dense retrieval would
improve recall, and recall **has no headroom left here**. Deepening the candidate
pool is actively worse (100 to 200 moves MRR from 0.839 to 0.766, as the
popularity prior lifts more popular wrong items above the target). LLM reranking
is excluded by the no-network clause in `docs/submission_rules.md`.
**This is our largest deviation from the brief's literal expectation, and the
report must present it as an evidenced design decision.**

## II. Dialog Strategy

**Information Accumulation -- met.** Incremental slot accumulation plus
`provenance`, which records which terms each phrase contributed (making selective
deletion possible).

**Intent Override "slot erasure and rewriting" -- implemented, but not made the
default after measurement.** `on_override="slot"` is an honest implementation of
the brief's semantics. A new `lab/override_stress.py` constructs **genuinely
contradictory** overrides (silk to leather), and averaged over 5 seeds `keep`
still wins (0.9233 vs slot 0.9140 vs erase 0.8458). Same reason as above:
**scoring, not filtering** -- an obsolete constraint cannot exclude the correct
product, whereas forgetting genuinely loses evidence.

**Proactive Guidance / over-generality cutoff -- met (added this round).**
`_overgeneral()`: when the surviving candidate pool spans 6 or more leaf
categories, the situation is judged "not a ranking problem but an
under-specified request". **Truncating recommendations purely loses score under
this metric, so the cutoff acts on the QUESTION rather than on the results:** on a
hit it forces pool-aware questioning (suspending the dry-streak protection) and
replaces the open-ended question with **structured options**, whose labels
automatically drop the shared prefix and keep only the distinguishing part
("women slippers / men slippers / men sandals"). Measured to fire on **36 of 407
turns (9%)**, concentrated in intent_override (27 times, where the pool genuinely
widens again after the pivot). **Zero score cost** (0.9285, identical digit for
digit to having it off).

## III. Self-Evolution

**Personalized Context Distillation -- abandoned after measurement, with numbers
for the negative result.** `user_profile` was previously stored and never used.
This round implemented the `w_profile` feature (preference-tag hit rate) and swept
the weight:

| w_profile | 0.0 | 0.5 | 1.0 | 2.0 | -0.5 |
|---|---|---|---|---|---|
| score | **0.9285** | 0.9239 | 0.9176 | 0.9073 | 0.9272 |

**Monotonically worse.** The cause is in the data itself: `purchase_frequency` is
**identical across all 200 sessions**, and `preference_tags` has only 9 generic
words (fit / material / comfort / style and so on) with hit rates of 50-80% --
weighting them credits nearly every product equally and purely dilutes the real
constraint signal. **The honest conclusion is that this profile carries no usable
personalization signal, not that we failed to implement one.**

**Adaptive Orchestration -- met.** Three genuine runtime reroutes, all triggered
by observation rather than hard-coded in configuration:
1. **Dead-slot soft matching:** each constraint is probed for literal hits across
   the whole pool, and a slot with zero hits switches to IDF-weighted soft
   matching (on the verbatim simulator the dead set is empty, so the insurance
   premium is zero).
2. **`dry_others` degradation:** after 2 consecutive dry replies, abandon `other`
   and cycle concrete attributes.
3. **`dry_streak` give-up plus overload suspension:** a missed targeted question
   falls back to open-ended, but that protection is suspended when the request is
   under-specified.

## IV. Evaluation Matrix -- fully met

Coverage / Precision / Efficiency are all aligned with the official harness. The
default configuration gives `0.928508 / HR@10 0.995 / MRR 0.839361 / MTTC 2.04`,
and both configurations' scores are locked by
`tests/test_score_regression.py`.

## One-sentence summary

**8 sub-items: 5 met, 1 partial (route classification is perfect but routes are
not differentiated), 2 deliberately abandoned with numbers for the negative
results.** The only genuine capability gap is **vector plus LLM reranking**, and
the recall curve proves the former has no headroom to recover while the
no-network clause excludes the latter. Every other "not done" item is a
**measured rejection**, not an omission.

---

# The capability-evaluation substrate (lab/scenarios.py + lab/capability.py)

## Why this had to come first

The public set is a **weak proxy**. It is structurally incapable of evaluating
the following capabilities -- not that we did not measure them, but that it cannot
measure them:

| capability | why the public set cannot measure it |
|---|---|
| intent override | `behavior_for`'s `old_value` and `new_value` are **both taken from the target product**, so the preference to forget is still evidence for the correct answer |
| personalization | `purchase_frequency` is **identical across all 200 sessions**; `preference_tags` has only 9 generic words |
| vague browsing | every opening message **names the category** |
| an uncooperative user | replies are always well-formed; boundary has only 10 sessions and only stonewalls for one turn |
| contradictory constraints | the user's stated constraints are **always true** |

Tuning only against this proxy means optimizing for **conditions the real task
does not have**.

## Design

A `Scenario` is a set of hooks over the official evaluation loop that **changes
exactly one thing**, leaving the harness, metrics and scoring untouched, so
results are comparable across rows. A hook returning `None` means "use the
official behaviour", so every scenario is a **diff against the real evaluator**
rather than a reimplementation that could drift.

`lab/capability.py` outputs **a matrix, not a single number**: rows are
scenarios, columns are configurations. Read a column to judge a configuration;
read a row to see which module a capability depends on. **If every configuration
in a row is level, no module is responsible for that capability.**

## The first capability scorecard

| scenario | default | ask=other | ov=slot | ov=erase | no_guidance | profile=1.0 | no_softslot | no_pop |
|---|---|---|---|---|---|---|---|---|
| clean (control) | 0.9285 | **0.9287** | 0.9218 | 0.8425 | 0.9285 | 0.9176 | 0.9278 | 0.8658 |
| override_genuine | 0.9196 | **0.9199** | 0.9183 | 0.8425 | 0.9196 | 0.9075 | 0.9188 | 0.8589 |
| override_category | 0.9190 | 0.9197 | 0.9190 | 0.9190 | 0.9194 | 0.9050 | **0.9234** | 0.8473 |
| vague_start | 0.8724 | **0.8742** | 0.8657 | 0.7864 | 0.8738 | 0.8648 | 0.8724 | 0.8141 |
| **uncooperative** | **0.7051** | 0.7051 | 0.6988 | 0.6188 | 0.7051 | 0.6990 | 0.7043 | 0.5576 |
| **contradiction** | 0.7990 | 0.8000 | 0.7880 | 0.7066 | 0.7997 | 0.7846 | **0.8018** | 0.6987 |
| profile_informative | 0.9285 | 0.9287 | 0.9218 | 0.8425 | 0.9285 | **0.9465** | 0.9278 | 0.8658 |

## Four conclusions (all of which changed priorities)

**1. Personalization is exploitable -- what is missing is data, not
architecture.** In the `profile_informative` row, `w_profile=1.0` scores 0.9465
(+0.018), and the adaptive version is higher still (0.9703). That **cleanly
separates** "we did not implement personalization" from "this data has no
personalization signal". But the adaptive version loses 0.029 on clean: whether
by pool coverage or global IDF, **the value ranges of generic and informative tags
overlap** (generic 1.42-4.05, informative 2.31-10.82), so there is no clean
discriminator. **Off by default**, retained as a switch for "enable it if the
private set has signal". This is a research question, not a tuning question --
hard-coding a threshold now is exactly the mistake this substrate exists to
prevent.

**2. The largest real gap is the uncooperative user: 0.7051, 0.22 below clean.**
And **the entire row is level apart from `no_pop`** -- by the reading rule above,
that means **no module is currently responsible for this capability.** The public
set cannot see this gap at all.

**3. Contradictory constraints at 0.7990 is the second-largest gap.** And
`no_softslot` is actually best there -- `slot_soft` is mildly harmful under
contradiction (it finds a soft match for a constraint that was wrong to begin
with). `override_category` also peaks at `no_softslot`. **`slot_soft` needs a
prior judgement of whether the constraint is credible**, rather than
unconditionally soft-rescuing dead slots.

**4. The popularity prior is a load-bearing wall in every scenario:** `no_pop`
drops 0.06-0.15 in every row, the only feature that is critical along every
capability dimension.

## A module roadmap by priority (data-driven, not guessed)

1. **A fallback for uncooperative users** (0.705, no module responsible) --
   highest priority.
2. **Constraint credibility under contradiction** (0.799; also fixes
   `slot_soft`'s reverse effect).
3. **A personalization discriminator** (+0.042 where signal exists; needs a real
   discriminator).
4. Vector / LLM routes (the recall curve proves there is no headroom; retained as
   an evidenced design decision).

---

# Phase 1: typed evidence state plus uncooperative recovery

Executed at the scope expanded by external feedback (not merely the `SlotValue`
data structure).

## Results (5 seeds, paired against each configuration's own clean baseline)

| scenario | before Phase 1 | after Phase 1 | delta |
|---|---|---|---|
| clean (default) | 0.928508 | **0.928508** | 0 (unchanged) |
| compat `ask_policy="other"` | 0.928708 | **0.928708** | 0 (digit for digit) |
| **uncooperative** | 0.7219 | **0.8372** | **+0.1153** |
| **vague_start** | 0.8724 | **0.9267** | **+0.0543** |
| **contradiction** | 0.7990 | **0.8427** | **+0.0437** |
| override_genuine | 0.9196 | 0.9255 | +0.0059 |
| override_category | 0.9190 | 0.9172 | -0.0018 (sd 0.0038, within noise) |

## Key finding: noise contamination was doing accidental query expansion

The contamination the feedback predicted does exist: `hmm / hard / say / really /
sure / think / can / just / show / more` all entered the BM25 query. **But fixing
it made `uncooperative` worse** (0.7219 to 0.6973). Decomposed, there are two
reasons:

1. **The detection alone has no benefit:** Phase 1B lets the agent correctly
   recognize that the user is stonewalling, but its existing response (fall back
   to `other`, cycle concrete attributes) is itself a bad strategy.
2. **The contaminating words were accidentally widening recall:** verified
   separately, `filter_noise=0, evidence_query=0` gives HR **0.785** while the
   clean version gives only **0.758**. When the user supplies no information the
   query is extremely narrow, and a narrow query means narrow recall -- the target
   never reaches the candidate pool at all. The junk words were widening the OR
   query.

So the correct fix is not to restore the contamination but to **widen recall
deliberately when evidence is thin**:

| starved_candidates | uncooperative | HR | MRR | MTTC |
|---|---|---|---|---|
| off | 0.7036 | 0.758 | 0.622 | 4.10 |
| 200 | 0.7684 | 0.836 | 0.668 | 3.50 |
| 500 | 0.8292 | 0.914 | 0.701 | 2.90 |
| **1000 (default)** | **0.8372** | **0.925** | **0.706** | **2.85** |
| 2000 | 0.8363 | 0.924 | 0.704 | 2.85 |

`starved_after=2`: `after=1` drops clean to 0.9251, and `after=3` is slightly
worse. **A deep pool is harmful when evidence is sufficient (MRR is crushed by
popular near-matches), and recall is the binding constraint only when evidence is
thin** -- which is exactly the Pillar III runtime adaptation, with clean untouched.

The same mechanism incidentally fixed `vague_start` (+0.0543 to 0.9267, HR
0.995). Note that **not a single routing label was changed.** The feedback's
diagnosis was right: the main cause of that score was "no category on turn 1
means BM25 has no usable query terms", a recall problem rather than a routing
label problem.

## What was implemented

- **1A typed evidence:** `SlotValue` (attribute / value / polarity / hardness /
  confidence / source_turn / provenance / active / catalog_support /
  contradiction). The retrieval query is **rebuilt from active evidence only**,
  rather than swallowing every token of every message. Both paths are identical
  digit for digit on clean (where messages are either parseable or already
  filtered), so it costs nothing.
- **1B reply-outcome classification:** `Outcome` = INFORMATIVE / OVERRIDE /
  NO_PREFERENCE / UNCERTAIN / REFUSAL / REQUEST_MORE / CORRECTION. Only
  INFORMATIVE and OVERRIDE merge into evidence. **A trap we hit:** initially a
  browsing opening with "only a category, no constraint" was classified
  UNCERTAIN, so 90 sessions lost their category on turn 1 and the score fell to
  0.8865. A regression test now locks this.
- **1C recovery without information:** widen recall when evidence is thin (table
  above); `REQUEST_MORE` triggers candidate rotation (protecting the top 3 so MRR
  is not harmed, refreshing only the tail with unshown candidates); and
  distinguish "no preference" (the wrong dimension was asked, so switch to
  open-ended) from "cannot answer" (that dimension is too hard, so ask an easier
  one).
- **1D credibility gating -- a negative result:** `slot_soft` really is worth
  -0.0100 on `override_category` (0.9272 with it off vs 0.9172 with it on). But a
  gate implemented as "earlier than the pivot, or a single-valued attribute
  superseded by an update" **did not fix it**: it recovers only 0.0008 and costs
  about 0.0007 in every scenario. **Off by default; the mechanism is still
  unexplained.** My original damage hypothesis was wrong.

## Ideas that produced no gain (recorded as such)

- **Answerability-weighted questioning:** no benefit at all (0.7036 with it on
  and off). The cause is that the simulator's "answerability" is determined by
  `classify_constraint`'s bucket matching rather than by human difficulty --
  asking use_case only discloses constraints bucketed as use_case. It is sound
  product design, but **this simulator cannot reward it**, the same class of
  conclusion as pool-aware questioning.

## Methodology upgrade (as the feedback required)

`lab/capability.py` now runs 5 seeds for each stochastic scenario, reports mean
+/- sd, outputs **penalty = scenario score minus that configuration's own clean
score** (rather than comparing absolute scores across rows), and reports HR / MRR
/ MTTC together.

## Incident record

This round `starter/agent.py` was corrupted once. Slicing with
`s.index(A):s.index(B)` where A occurs after B in the file produced an empty
string, and `str.replace("", block)` inserted the code block **between every pair
of characters**, inflating the file to 73 MB. Because the insertion was uniform,
the inserted block could be recovered from the repetition period and removed
wholesale, restoring the file **byte for byte** (48,721 bytes after restoration,
AST-validated). Lesson: always edit strings with a unique anchor plus a count
assertion, never with index slicing.

---

# Disposition of review items 1-8

## 1. Reproducibility (root cause fixed)

The review re-ran on `bcfbca2` with seeds 7-11 and got
`uncooperative=0.829795`, where the report said `0.8372`. **There was no code
difference at all -- the seed sets differed.** `lab/capability.py`'s documentation
says the default is `range(7,12)`, but the numbers were produced by a throwaway
script using `(7,11,23,42,101)`, and that script never wrote a log, so the
discrepancy was invisible until someone else re-ran it.

Root-cause fix: **`lab/record.py` is the only entry point permitted to produce
numbers.** Each row carries commit / dirty flag / dirty file list / full config /
full seed list / four metrics per seed / mean / sd, appended to
`lab/results.jsonl`. **An aggregate without its seed list may not be reported.**

Corrected Phase 1 results (both sides re-measured on seeds 7-11):

| scenario | pre (1d5718c) | Phase 1 | delta |
|---|---|---|---|
| clean | 0.928508 | 0.928508 | 0 |
| uncooperative | 0.711598 | 0.833266 | **+0.1217** |
| vague_start | 0.870627 | 0.917534 | **+0.0469** |
| contradiction | 0.784395 | 0.809592 | **+0.0252** |
| override_genuine | 0.921971 | 0.921971 | **0** (the previously reported +0.0059 was a seed artefact) |
| override_category | 0.913515 | 0.915013 | +0.0015 (sd 0.0066, within noise) |

## 2. The CPU cost of depth=1000

| depth | suite wall clock | peak RSS | normal turn p50/p95 | starved turn p50/p95 | score |
|---|---|---|---|---|---|
| off | 12.9 s | 591 MB | 6.2 / 27.7 ms | — | 0.6787 |
| 500 | 7.0 s | 593 MB | 9.9 / 20.3 ms | 9.9 / 20.3 ms | 0.8239 |
| 1000 | 7.3 s | 594 MB | 10.4 / 34.3 ms | 12.8 / 23.7 ms | 0.8353 |

**Widening recall makes the whole evaluation faster** (12.9 s to 7.3 s), because
sessions converge earlier and there are fewer turns overall. 1000 against 500
costs **+2.9 ms p50 / +3.4 ms p95** on starved turns and **+1 MB** of RSS. In
absolute terms, 12.8 ms p50 is negligible against a 60 s budget.

**The depth choice was validated on a holdout** (uncooperative_holdout, seeds
12-21, unseen seeds and unseen wording): depth_500 = 0.815102 +/- 0.0138,
depth_1000 = **0.825127 +/- 0.0145**, a **+0.0100** difference consistent with the
+0.0114 on the selection set. **+2.9 ms p50 for +0.010 holds up.**

## 3. The starvation signal is no longer equated with "consecutively uninformative"

Measured: **the median stalled turn on clean has 17 query terms and 7 active
constraints** -- precisely the strong queries that must never be widened to 1000.
`_starved()` now requires "stalled (or an explicit REQUEST_MORE) **and** the query
really is thin": at most 8 terms or at most 1 active constraint. The cost is
`vague_start` 0.9267 to 0.9175. **Keep the conservative gate.**

## 4. Rotation is now a one-shot event

`wants_more` only ever increased, so every subsequent turn kept rotating. Now
`REQUEST_MORE` arms `rotate_pending` exactly once and `_rotate` consumes it; and
**`shown` and `rotate_pending` are cleared when new evidence arrives**, because
the old pagination belongs to a different result set.

## 5. Attribution of contradiction's +0.0252 (factorized ablation)

| factor disabled | contradiction | contribution |
|---|---|---|
| (all on) | 0.809592 | — |
| -evidence_query | 0.809592 | **0.000** |
| -outcome_filter | 0.809592 | **0.000** |
| **-starved** | 0.784395 | **+0.0252 (all of it)** |
| -rotation | 0.809592 | **0.000** |
| -slot_soft | 0.812014 | -0.0024 (`slot_soft` is harmful here) |

Turning off starvation widening **reproduces the pre-Phase-1 0.784395 digit for
digit**. Conclusion: the entire gain comes from widened recall.

## 6. The `slot_soft` mechanism is now understood (`lab/diag_slotsoft.py`)

On `override_category` the only dead phrase is **the abandoned category itself**
(`'i want shoes slippers'`). `slot_soft` revives it: candidates that are still
slippers get f_slot=1.0, i.e. **+4.000**, while the genuine post-pivot target gets
**+0.000** -- that single term decides the ranking (competitor 8.55 vs target
7.21).

**Root cause:** `last_override_turn=0`, meaning **the override was never detected
at all**. The old `OVERRIDE_RE` required a literal "forget what i said", while the
message was "forget shoes slippers entirely". It has been extended to cover
forget / changed my mind / no longer / instead of / not ... anymore. "Forget
boots, I want running shoes" is the most typical intent override there is and was
previously invisible -- a real robustness defect, not one manufactured by the
scenario.

With detection fixed, `soft_needs_credible` lifts `override_category` from 0.9150
to **0.9237**, exactly matching `slot_soft=0`. **It is still not made the
default**, because it costs payload-rewording robustness (payload_soft
0.8777 to 0.8617, shuffle 0.8982 to 0.8929, drop 0.8842 to 0.8737) plus 0.0008 on
clean. Payload rewording is a plausible private-set risk while the category pivot
is a scenario we authored ourselves, so the trade is not worth it.
`on_override="slot"` remains worse even with detection fixed (0.9010 vs keep
0.9150) -- **scoring still beats filtering.**

## 7. Phase 2A (see commit 07c191a)

Unknown openings now classify as `mixed` rather than `override` 100% of the time;
routes firm up from mixed/browsing to buying based on per-turn evidence (75
`browsing -> buying` transitions on clean); and `_retrieve()` now uses this turn's
route config (previously `term_cap` and `bm25` read `self.cfg` directly, so any
route patch was silently void -- the same class of defect as `"route": false`).
Route weights are left neutral, so behaviour is unchanged: clean 0.928508 and
compat 0.928708, digit for digit.

## 8. Holdout validation (seeds 12-31, configuration frozen)

| scenario | pre-Phase-1 | Phase 1+2A | delta |
|---|---|---|---|
| uncooperative (development wording) | 0.714024 +/- 0.0197 | **0.833022 +/- 0.0102** | **+0.1190** |
| uncooperative_holdout (**unseen wording**) | 0.705402 +/- 0.0249 | **0.817144 +/- 0.0147** | **+0.1117** |

- **Selection seeds 7-11 give 0.833266 and holdout seeds 12-31 give 0.833022** --
  the `starved_after` and `depth` choices generalize across seeds; they were not
  tuned in.
- +0.112 is retained under unseen wording, only 0.016 below the development
  wording. (The same gap for the old agent was 0.009, so the new agent is
  slightly more wording-sensitive, but the gain is overwhelmingly preserved.)

**One real defect the holdout exposed (recorded, not fixed):** `'Could I see a few
different ones?'` should classify as REQUEST_MORE but lands on UNCERTAIN --
`MORE_RE` requires a literal "more". **Not fixed on the holdout**, since that
would void the holdout. Left for the next round with a new development set.

UNCERTAIN is the catch-all branch that any unparseable message not matching a
known pattern falls into -- so detection of unseen wording generalizes **by
design**, not by pattern-matching development-set wording.

---

# Pre-registered experiment: suppress only the explicitly abandoned span

**Registered before the results were observed.** The commit history shows this
section committed ahead of the results.

## Hypothesis

The current two options are too coarse-grained:
1. Keep `slot_soft`: preserves payload-paraphrase robustness, but the category
   pivot suffers (0.9150).
2. `soft_needs_credible=True`: fixes the pivot (0.9237), but **blocks all soft
   evidence from before the pivot**, so payload robustness falls (0.8777 to
   0.8617 / 0.8982 to 0.8929 / 0.8842 to 0.8737).

**Hypothesis:** the user has already told us which part is void. Disabling
soft-rescue only for the **explicitly named abandoned span** ("forget shoes
slippers" gives `shoes slippers`), while other old evidence (colour, material)
can still soft-match, should obtain both benefits at once.

## Predictions (written before observation)

| metric | prediction |
|---|---|
| override_category | about 0.923, comparable to `soft_needs_credible` / `slot_soft=0` (baseline 0.9150) |
| payload_soft | about 0.8777 (holds at the default level, **not** dropping to 0.8617) |
| payload_shuffle | about 0.8982 |
| payload_drop | about 0.8842 |
| clean | 0.928508 unchanged |
| compat `ask_policy="other"` | 0.928708 unchanged digit for digit |
| override_genuine | about 0.9220 unchanged |

**Falsification condition:** if `override_category` is not significantly above
0.9150, or if any payload style falls to the blanket gate's level, the hypothesis
is rejected and we return to choosing one of the two.

## Pre-registered experiment result: the hypothesis holds, and is strictly better than both old options

| metric | prediction | measured | |
|---|---|---|---|
| override_category | about 0.923 | **0.924458 +/- 0.0019** | confirmed, and above both the blanket gate (0.923708) and `slot_soft=0` (0.923708) |
| payload_soft | about 0.8777 | **0.8777** | confirmed, identical to the default digit for digit |
| payload_shuffle | about 0.8982 | **0.8982** | confirmed, digit for digit |
| payload_drop | about 0.8842 | **0.8842** | confirmed, digit for digit |
| clean | 0.928508 | **0.928508** | confirmed |
| override_genuine | about 0.9220 | **0.921971** | confirmed, and above the blanket gate (0.921461) |

The blanket gate costs 0.0160 / 0.0053 / 0.0105 across the three payload styles;
**span suppression costs nothing**, because *which part is void* was stated by the
user and does not have to be guessed. `suppress_abandoned=True` is now the
default.

---

# Open-world evidence extraction (review item 5)

The original logic was a **high-precision, low-recall** parser: if the template
did not parse, there was no category and no phrase, so the turn became UNCERTAIN
and was discarded entirely. That is safe against unknown stonewalling, but it
**discards unknown real constraints just as readily.**

Now, when the template fails, the raw sentence is run through the slot regular
expressions plus a feature lexicon plus negation detection; **INFORMATIVE is
assigned only when a definite attribute/value is extracted**, otherwise it still
falls to UNCERTAIN. Extracted evidence carries `hardness="soft"` and
`confidence=0.6`, **below template evidence**, so it is not weighted equally.

| input | extraction |
|---|---|
| "Leather would be ideal." | material=leather (+) |
| "I'd love something blue." | color=blue (+) |
| "Mostly for hiking." | use_case=hiking (+) |
| "Something waterproof would help." | feature=waterproof (+) |
| "I need it machine washable." | feature=machine washable (+) |
| **"Nothing too formal."** | **use_case=formal (-1)** -- the negation is recognized |

Nine stonewalling phrasings (including the six holdout sentences) extract
**nothing at all** and still classify as UNCERTAIN. Clean 0.928508 and compat
0.928708 are both unchanged (on clean the template always parses, so this path
never fires).

---

# Holdout status: consumed (review item 4)

Seeds 12-31 plus those six unseen phrasings **have already fed back into
development** (they exposed the gap where `MORE_RE` only accepts a literal
"more"), and therefore:

- that combination is **no longer an untouched holdout** and must not be used for
  further tuning;
- the six phrasings have been merged into the development regression in
  `tests/test_agent.py` (`OpenWorldEvidenceTest.UNINFORMATIVE`);
- **a new sealed phrasing set must be created before the next round of tuning**,
  and run exactly once before use.

The holdout conclusions already recorded remain valid -- they validated the
**Phase 1 configuration as frozen at that time**, and that validation itself was
not contaminated.

## Required external wording (review requirement)

> **The dual-route control plane is complete; the Buying/Browsing route-specific
> retrieval data planes are not implemented.** What Phase 2A completed is routing
> label semantics, evidence-driven route transitions, configuration plumbing and
> observability; the two routes still share identical default weights, and there
> is no facet/filter route, no dense route and no route fusion.
> **Phase 2A must not be written up as Pillar I being complete.**

---

# Phase 1.5 hardening (second review round: four semantic gaps)

The previous round **overstated things**. All four criticisms below are correct,
and each has been verified and fixed.

## 1. `confidence` was a write-only field (correct)

Across the whole codebase, `confidence` appeared only where it was written and
**never participated in scoring**. So the claim that "evidence extracted from
natural language never carries the same weight as template evidence" **was not
true at the time**.

It now genuinely scales evidence weight: `f_phrase` / `f_exact` / `f_field` are
weighted by each phrase's confidence (with the sum of confidences as the
denominator), template evidence at 1.0 and open-world extraction at 0.6. A new
regression test, `test_confidence_is_actually_read_not_just_stored`, requires
that **changing only the confidence changes the ranking**, and fails otherwise.
Clean is unchanged (every confidence there is 1.0, which is equivalent to the
original formula).

## 2. Negative constraints were recognized but never enforced (correct)

`SlotValue.usable` requires `polarity > 0`, so the `polarity=-1` slot generated by
"Nothing too formal" neither entered the query nor deducted anything -- that is
**"recognizing negation", not "handling negation"**.

An independent negative channel has been added: `f_neg` is the share of a
candidate's hits on rejected constraints, **deducted** with `w_neg=2.0`. Negative
evidence **never enters the query** (a rejection is not a search term).
**Implemented by scoring rather than filtering:** an incorrectly extracted
negative constraint must never empty the candidate pool. A high-confidence hard
filter plus a rescue lane is left to be designed together with Phase 2B's Buying
safe-filter.

## 3. The override detector was not unified (correct)

`respond()` used `is_override()`, while `classify_reply()` and `_route()` still
used the old `OVERRIDE_RE` directly -- so the same message could be treated as an
override by the state machine, recorded as informative/uncertain by the outcome,
and not be an override for routing, contaminating the dry-streak counter and the
telemetry. All three now go through `is_override()`, with an
`OverrideDetectorUnityTest` asserting that the three call sites **agree** on 12
true and false positives.

## 4. The abandoned span previously only disabled soft rescue (correct) -- real targeted erasure is now implemented

Previously an abandoned slot was still `active=True`, still in `phrases`/`terms`,
and still contributed to BM25/exact/phrase/field/IDF scoring. **"Suppressing
soft-rescue" really is not the same as slot erasure.**

Rather than changing the wording, it was built and quantified:

| policy | override_category | clean | payload soft/shuffle/drop |
|---|---|---|---|
| no suppression | 0.915013 +/- 0.0066 | 0.928508 | 0.8777 / 0.8982 / 0.8842 |
| span suppression of soft-rescue | 0.924458 +/- 0.0019 | 0.928508 | 0.8777 / 0.8982 / 0.8842 |
| **span-targeted erasure (new default)** | **0.928308 +/- 0.0000** | **0.928508** | **0.8777 / 0.8982 / 0.8842** |
| full erasure `on_override="slot"` | 0.9010 | 0.925173 | — |

**Targeted erasure strictly dominates:** `override_category` reaches 0.9283
(HR 0.995 / MRR 0.839, already at clean levels), while clean and all three
payload rewordings are **unchanged digit for digit**. The decisive difference is
**scope**: erasing the span the user named is effective, erasing everything on an
override (0.9010) is not. The wording can therefore honestly be
**span-targeted dynamic slot erasure**, presented alongside the comparison with
full erasure.

## 5. Ledger historical validity (correct)

Of the 62 rows in `lab/results.jsonl`, 50 were still the old schema, and **no row
had ever actually recorded `agent_commit=1d5718c`** -- I had only called the
function live to verify it and never persisted anything.

`lab/migrate_results.py` **rewrites nothing and deletes no measurement**; it only
classifies rows by content and annotates `schema_version` / `self_describing` /
`provenance_note`. Result: 12 rows at schema 2 (self-describing) and 50 at schema
1 (annotated "cannot be reproduced from this row alone"); the
`pre-phase1-baseline` rows say explicitly that the `agent_commit` is "asserted by
tag, NOT recorded at run time". A **genuine** isolated baseline was also recorded:
`agent_commit=1d5718c`, `agent_sha256=0e6fe9a0809a8444`, score 0.711598.

## 6. Minor items

- `_HASH_CACHE` is now keyed by `(path, mtime_ns, size)` so a hash cannot go stale
  when a file is modified within the same process.
- `Agent.close()` **no longer clears the global catalog cache** -- doing so would
  invalidate the SQLite connection of another Agent still using the same catalog.
  It now clears only its own sessions; process-level teardown still uses
  `clear_catalog_cache()`.
- **Pre-registration wording corrected:** `11b5563` contains both the
  implementation and the predictions, which makes it
  **"predictions committed before measurement"** and **not** a strongly isolated
  protocol-only pre-registration. It is no longer advertised as the latter.

---

# Phase 1.5B (third review round: confidence mathematics, negation evaluation, a clean re-record)

## 1. `confidence` was doing "relative allocation", not absolute confidence (correct)

The original formula was `sum(confidence of matches) / sum(all confidences)`.
**With only one constraint the numerator and denominator cancel:** 0.1/0.1 =
0.6/0.6 = 1.0/1.0 = 1.0, so a single low-confidence extraction still scored full
credit. My original test also only compared two numbers inside the dataclass, so
**it would have passed even if ranking ignored confidence entirely.**

It now uses the **phrase count as the denominator**, making confidence an absolute
discount. Measured (P2 = a silk scarf, positive evidence "silk"):

| confidence | 1.0 | 0.6 | 0.1 |
|---|---|---|---|
| P2 score | **7.0000** | **4.2000** | **0.7000** |

The negative side behaves the same way (with `w_neg=5`): confidence 0.0 / 0.3 /
1.0 gives 0.0 / -1.5 / -5.0. Confidence is now also wired into the soft-overlap
and `slot_soft` paths. A new `score_candidates()` exposes the scores, and the
tests now **assert score changes** rather than merely comparing ranks or fields.

## 2. Negative constraints: wired into ranking, and now evaluated by scenario

`f_neg` is now **weighted by each slot's confidence** (a low-confidence extraction
must not carry the same weight as an explicit rejection).

Two new scenarios: `negative_preference` (rejecting an attribute **the target does
not have**, which is pure signal) and `negation_scope_holdout` (**a brand-new
sealed set**, mixing in restrictive affirmatives).

**A real scope bug was found and fixed** (the review predicted it correctly):

| input | before the fix | after the fix |
|---|---|---|
| "Nothing but leather." | leather = **-1** (wrong; it means "only leather") | leather = +1 |
| "Not only leather." | leather = **-1** (wrong) | leather = +1 |
| "Leather, not synthetic." | only +leather extracted | +leather **and** -synthetic |

At `w_neg=2.0` this bug would **invert a positive constraint into a penalty**,
actively suppressing the correct product. A `RESTRICTIVE_RE` guard plus 8 unit
tests have been added.

**The empirical conclusion about `w_neg` (honest version):**

| w_neg | 0 | 1 | 2 (default) | 4 |
|---|---|---|---|---|
| negative_preference | 0.698583 | 0.699256 | 0.700683 | 0.700683 |

**+0.0021 against sd = 0.019 -- indistinguishable from zero.** On the sealed scope
holdout, `w_neg=0` and `w_neg=2` score identically (0.700124), i.e. **it does no
harm**. The reason has been quantified: on turns with an active rejection, **only
0.8% / 0.0% of shown candidates actually match the rejected value** -- candidates
that reach the top 10 rarely carry a rejected attribute in the first place.
**Conclusion: the negative channel is correct and safe, but nearly inert on this
catalog and simulator.** `w_neg=2.0` is kept (saturated and harmless), but **no
measurable gain may be claimed for it.** This also affects Phase 2B's priorities:
a hard-negative filter has very little to filter.

**The sealed holdout exposed another defect (recorded, not fixed):**
"I'd rather steer clear of wool." is not recognized as a negation (`NEGATION_RE`
has no "steer clear of"). **Not fixed on the holdout**; the phrasing has been
removed from the development unit tests and left for the next round's development
set. (Lesson restated: writing holdout phrasings into unit tests burns the
holdout.)

## 3. Clean re-record

The previous round's key targeted-erasure numbers came from a dirty run
(`agent_commit=worktree`, `code_dirty=true`). By our own discipline those numbers
**were not citable at the time**. They have been re-recorded on a clean commit.
**The re-record itself caught another silently void switch:**
`suppress_abandoned=False` scored the same as the default (0.928308), because
`_suppress_abandoned()` ran regardless of the switch and only `_rerank`'s
blocklist was controlled by it -- **the ablation's "off" arm was never actually
off.** That is the same class of defect as `"route": false` and `term_cap`
reading `self.cfg`.

The fixed clean re-record (seeds 7-11, `tag=phase15b-ablation-fixed`, schema 2,
`code_dirty=false`):

| policy | override_category | clean |
|---|---|---|
| no suppression | 0.914603 +/- 0.0067 | 0.928508 |
| soft-rescue suppression only | 0.924458 +/- 0.0019 | 0.928508 |
| **targeted erasure (default)** | **0.928308 +/- 0.0000** | **0.928508** |
| full erasure `on_override="slot"` | 0.923723 | 0.925173 |

The conclusion is unchanged and now citable: **targeted erasure > soft-rescue
suppression only > no suppression**, and targeted erasure costs nothing on clean
or on any of the three payload rewordings.
