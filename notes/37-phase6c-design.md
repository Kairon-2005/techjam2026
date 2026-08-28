# Phase 6C design — profile credibility, and personalization only if it is earned

**Design and pre-registration. No implementation.** Phase 6B2 is closed through
R2 (`notes/36`); this is the next phase's contract, written before any 6C code
exists.

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

### The five categories

Per **tag**, mutually exclusive, first match wins. The precedence is fixed here
so the classification cannot depend on evaluation order:

| # | category | definition |
|---|---|---|
| 1 | **`conflicting`** | the tag matches a value the customer explicitly negated this session (`polarity < 0`), or one superseded by an override (`_uncredible()`'s blocked set). Acting on it would contradict a stated preference. |
| 2 | **`duplicated_session_evidence`** | the tag equals an active positive slot value already stated this session. The session already has it, at higher confidence and with provenance. A prior that repeats stated evidence adds nothing and double-counts. |
| 3 | **`generic`** | coverage in the bounded ranked window **>** `profile_max_coverage`. A tag that most surviving candidates already satisfy cannot reorder them. |
| 4 | **`specific_informative`** | survives 1–3: present in the window, but not in most of it, not already stated, not contradicted. |

Per **session**, one verdict:

| category | definition |
|---|---|
| **`no_signal`** | `preference_tags` is absent, empty, or every entry is empty after normalization — there is nothing to classify. Distinct from "all tags classified as generic", which is a *finding* about the tags, not their absence. |

`no_signal` is deliberately **not** the union of the failure categories. Collapsing
"the user told us nothing" into "what the user told us was useless" would make
the two indistinguishable in the telemetry, and they call for different
conclusions.

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

### Measurements, reported in two arms that are never averaged

**Arm A — official profiles**, seven scenarios, the real
`user_profile` from the public set.

**Arm B — synthesized informative profiles**, the existing
`profile_informative` scenario, which builds `preference_tags` from the
ground-truth product's own tokens (`lab/scenarios.py:713`).

> **Arm B is an oracle construction and an upper bound, not an achievable
> gain.** Its tags are derived from the answer. It exists to separate *"the
> data has no signal"* from *"we cannot see the signal that is there"* — the
> same job the negative controls did for 6B2's comparator. **No number from
> Arm B may ever be quoted as a result the agent achieved.**

Reported per arm, never pooled:

* the five-category distribution, as **raw counts** over turns and sessions;
* sessions with ≥ 1 `specific_informative` tag;
* median coverage of credible tags;
* median pairwise Jaccard between sessions' **credible** tag sets;
* the dataset characterization above.

### The gate that decides whether 6C2 is designed at all

Pre-registered now, evaluated on **Arm A**:

| | criterion | threshold |
|---|---|---|
| **D1** | sessions with ≥ 1 `specific_informative` tag | **≥ 20%** |
| **D2** | median pairwise Jaccard between credible tag sets | **≤ 0.30** |
| **D3** | median coverage of credible tags | **within [0.05, 0.50]** |
| **D4** | *instrument check:* Arm B passes D1–D3 | **required** |

D1 asks whether personalization could fire often enough to matter. D2 asks
whether it would say anything *different* per user — the raw profiles are
already at 0.50, so this tests whether credibility filtering *creates*
separation rather than inheriting it. D3 asks whether credible tags can
actually reorder candidates: too rare and they match nothing, too common and
they rank everything equally.

**D4 is the negative control on the measurement itself.** If Arm B fails too,
the classifier is broken and no conclusion about Arm A is admissible.

**Outcomes:**

* **Arm A passes D1–D3 and D4 holds** → 6C2 is designed, in its own
  pre-registration.
* **Arm A fails, Arm B passes** → recorded conclusion: *the mechanism works;
  the official profile carries no usable per-user signal.* **6C2 is not
  designed for this submission.** That is a real, publishable finding about the
  data and it is the outcome the dataset characterization predicts.
* **Arm B fails (D4)** → the instrument is broken. No conclusion about Arm A.
  Fix the classifier and re-run 6C1; do not proceed to 6C2 on a broken
  measurement.

### Stop conditions

Stop immediately if: shadow mode moves score, message, recommendations or
`ask_attribute` by any amount; the profile decision acquires a callback into
the host, a catalog reference or an index build; any classification depends on
a corpus-derived constant; or `w_profile` / `w_profile_adaptive` move from 0.

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
| **clean regression guard** | `clean` score **must not decrease at all** from its measured baseline (`0.932067`), and no official slice may regress. A personalization prior that costs the headline scenario anything is not paying for itself. |
| **informative-profile gain** | on Arm B, score improves by **≥ +0.01 absolute** over the same scenario with personalization off, and the improvement **exceeds the across-seed standard deviation**. A gain inside its own noise is not a gain. Reported as an oracle upper bound, never as an achieved score. |
| **latency** | per-branch through `lab/benchmark.py` under the R2.1 rule: absolute overhead **≤ 0.25 ms**, and the ratio gate applies only where the control median is **≥ 0.10 ms**. Seven fresh-process paired repetitions; four of seven is not a result. |
| **memory** | no new index built in any mode; the shadow snapshot stays within `MAX_ENTRIES` (64) and `MAX_BYTES` (4096); peak RSS delta **≤ 5 MB** from the harness's recorded `peak_rss_bytes`. |
| **supplementary veto** | `supplementary_dev` **must not regress**. It is a veto signal, not a score — `lab/record.py` carries `source`/`official` on every row precisely so a supplementary number cannot be quoted as an official one. |

Any one of the five failing stops adoption. There is no weighted trade among
them: a latency budget is not purchasable with score.

## Explicitly not in Phase 6C

The sealed holdout. The reranker. Downstream weight tuning. Cross-session
memory. Fine-tuning. Any change to question selection, retrieval, or the
Phase 6B2-R2 controller now in place.
