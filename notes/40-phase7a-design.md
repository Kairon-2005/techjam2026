# Phase 7A design — reranker feasibility, and what it is allowed to prove

**Revision 2. Design and pre-registration only.** No implementation, no
dependency installed, no model downloaded, no weights changed, no public
evaluation. Phase 6 is closed (`notes/39`) and is not modified.

Revision 2 changes six things, five of which were errors in revision 1:

1. **Gate 0 was wrong.** It read "numpy is not importable in this interpreter"
   as "a semantic arm is prohibited". Those are different claims.
2. **The phase is split**: R0 is runtime feasibility with **no labels and no
   quality metric**; R1 is quality pre-registration and begins only after A2 is
   frozen.
3. **A1's contract was contradictory** — "hand-specified" weights "set on
   `supplementary_dev`" is a search, not a specification. One contract is now
   chosen and its search is fully specified.
4. **The ceiling was asserted, not measured.** The "+0.142 or it's a bug" claim
   is withdrawn until four named measurements exist.
5. **Resource gates were shared across arms.** They are now separate, and A2's
   is set *after* R0 measures reality — but before any quality result.
6. **"Supplementary must not regress" was undefined.** Metric, tolerance,
   pairing and role are now stated.

## The objective is MRR on the exact purchased item

Not "demonstrate a reranker". The metric that pays is the rank of the one
correct `parent_asin`.

Current heuristic, `clean`, citable (`p6c1-arm-a`, mode `off`):

| | value |
|---|---|
| HR@10 | **0.9950** — 199 of 200 targets already reach Top-10 |
| MRR | **0.8526** |
| MTTC | 2.060 |
| composite | 0.932067 |

| official slice | HR@10 | MRR |
|---|---|---|
| `boundary` | 1.0000 | **1.000000** — nothing to win, everything to lose |
| `intent_override` | 1.0000 | 0.922222 |
| `buying` | 0.9875 | 0.851181 |
| **`browsing`** | 1.0000 | **0.809375** — weakest slice |

Session-level HR@10 is 0.995, so this is a **reordering** problem in positions
2–10 of lists that already contain the answer, not a retrieval problem.

## Gate 0 — runtime feasibility, corrected

**Revision 1 overreached.** It measured that `numpy`, `torch`,
`sentence_transformers`, `transformers`, `onnxruntime`, `sklearn` and `scipy`
are absent from *this interpreter* and concluded a semantic arm was
prohibited. **That is an environment fact, not a rule.** The submission permits
dependency manifests, installation instructions and lightweight local assets.

**The standing decision for research purposes:**

* **Pinned lightweight dependencies are allowed**, in an **isolated
  environment**.
* **Locally vendored model weights are allowed** for feasibility testing.
* **No network access at evaluation or runtime.** Nothing is fetched when the
  agent runs.
* **The lexical default must remain a functional offline fallback.** A2 may
  never be the only path to an answer.
* **Unresolved packaging constraints keep A2 out of `score_default`** — they do
  **not** stop A1, and do not stop the phase.

**The system Python is not modified.** The R0 spike runs in an isolated
worktree with its own environment, and nothing it installs reaches the tree
that produces a score.

## Phase split

### 7A-R0 — model and runtime feasibility only

**No public labels. No quality metric. No MRR.** R0 may not compute a score,
because a candidate chosen partly on quality would already have consumed the
confirmation set's independence.

* Search Hugging Face, GitHub and official runtime documentation.
* Shortlist **at most three** exact CPU-local candidates.
* Record for each, exactly:

| field | why |
|---|---|
| model ID | ambiguity here makes the whole run irreproducible |
| revision / commit hash | a moving `main` is not a fixed artifact |
| license | a permissive score is worthless if the weights cannot ship |
| parameter count and on-disk artifact size | the vendoring budget |
| backend | the dependency being proposed |
| quantization | changes size, latency and determinism together |
| maximum sequence length | decides whether a product blob must be truncated |
| offline loading path | proves nothing is fetched at runtime |

* Benchmark, through the committed harness: **cold load**, **steady pair
  latency**, **RSS**, and **determinism** (identical output for identical input
  across processes).
* **Select exactly one A2 candidate on feasibility criteria only** — never on a
  leaderboard position, a recommendation score, or a quality claim from its
  model card.
* **Commit the R0 result before any quality experiment.**

### 7A-R1 — quality pre-registration

Begins **only after A2 is frozen** by R0. Defines A0, A1 and the single
selected A2 exactly. **The public 200 stays untouched until confirmation.**

## The three arms

### A0 — the current heuristic (control), untuned

### A1 — deterministic reweighting of the existing heuristic

**The contract is reweighting, not a new formulation.** Revision 1 called A1
"hand-specified" while also setting its weights on `supplementary_dev`; those
are different things and the second is a search. **A1 is a search**, and the
whole search is specified here.

The existing reranker is already a linear model
(`starter/retrieval.py:_rerank`). Its exact form, per candidate:

```
total = w_bm25·f_bm25 + w_phrase·f_phrase + w_idf·f_idf + w_cat·f_cat
      + w_pop·f_pop   + w_exact·f_exact   + w_field·f_field
      + w_pos·f_pos   + w_card·f_card     + w_soft_eff·f_soft
      + slot_soft·f_slot + w_profile·f_profile − w_neg·f_neg
```

**The exact feature vector**, all bounded, computed per candidate:

| feature | definition |
|---|---|
| `f_bm25` | min–max normalised BM25 within this candidate list |
| `f_phrase` | IDF-weighted share of query phrases present in the blob |
| `f_exact` | confidence-weighted share of phrases matching a structured value |
| `f_field` | confidence-weighted share of phrases in the feature blob |
| `f_idf` | IDF-weighted share of query terms present |
| `f_cat` | share of category tokens matching the product's categories |
| `f_pop` | popularity prior |
| `f_soft` | token-level soft overlap, confidence-weighted |
| `f_slot` | soft overlap restricted to unsatisfiable ("dead") constraints |
| `f_pos` | reciprocal-rank of phrase position in ordered values (**off**, `w_pos = 0`) |
| `f_card` | share of phrases in the card fields (**off**, `w_card = 0`) |
| `f_profile` | share of profile tags present (**off**, `w_profile = 0`, and 6C1 says it stays off) |
| `f_neg` | confidence-weighted share of *rejected* values present — subtracted |

**A1 re-fits the weights on this fixed feature set.** No new feature is
introduced in 7A. `w_profile` is **pinned at 0.0** and excluded from the search
— Phase 6C1 found no demonstrated target alignment, and re-admitting it through
a weight search would relitigate a closed phase.

**Search specification, frozen here:**

* **Split.** `supplementary_dev`'s 1,000 samples are split deterministically by
  `sha256(sample_id)`: `int(digest, 16) % 5 < 4` goes to **`sup-train`**, the
  rest to **`sup-val`**. No RNG, no seed, reproducible from the ids alone.
  **Computed and frozen here, before any trial exists:**

  | | |
  |---|---|
  | `sup-train` | **806** samples |
  | `sup-val` | **194** samples |
  | **split hash** (`sha256` of the sorted `sup-train` ids, first 16 hex) | **`211be164cec5ff4f`** |

  Reproduced by:

  ```bash
  python3 -c "import hashlib,json; ids=[json.loads(l)['sample_id'] for l in open('data/supplementary_dev.jsonl')]; tr=sorted(str(s) for s in ids if int(hashlib.sha256(str(s).encode()).hexdigest(),16)%5<4); print(len(tr), hashlib.sha256(''.join(tr).encode()).hexdigest()[:16])"
  ```
* **Method.** Deterministic coordinate descent. Fixed sweep order:
  weights sorted alphabetically by name. **3 sweeps.**
* **Grid.** Per weight, 7 points: `{0, 0.25, 0.5, 1, 2, 4, 8} ×` its current
  value. A weight whose current value is 0 is **excluded entirely** — the grid
  is multiplicative so it could never leave zero, and admitting one would mean
  introducing a feature rather than reweighting one.
* **The searched set is exactly 9 weights**, verified against `DEFAULTS`:
  `w_bm25`, `w_phrase`, `w_idf`, `w_cat`, `w_pop`, `w_exact`, `w_field`,
  `slot_soft`, `w_neg`. Excluded as currently zero: `w_pos`, `w_card`,
  `w_soft`. Excluded by rule: `w_profile` (Phase 6C1). Note `w_soft = 0` is not
  an accident — the live value is `w_soft_eff`, selected at run time by
  `soft_adaptive` from `w_soft_hi` / `w_soft_lo`, so it is not a searchable
  scalar in this formulation.
* **Trial count.** 3 sweeps × 9 weights × 7 points = **189 trials**, fixed in
  advance.
* **Objective.** MRR on **`sup-train` only**.
* **Tie-break.** Lowest L1 norm of the weight vector; then the alphabetically
  earlier weight name. Deterministic, so no seed is required, and that is
  stated rather than left to be assumed.
* **Freeze.** The resulting weights are committed **before** any public
  confirmation run.
* **`sup-train` is never reported as validation.** It selected the weights; it
  cannot also measure them.

### A2 — CPU-local semantic reranker, one candidate, selected by R0

`_DenseIndex` is **not** A2: it is a stdlib random-indexing *candidate source*
with a measured BM25 overlap of 0.020, aimed at recall, where there is no
headroom.

## `score_default` and `showcase_model`

| profile | may claim |
|---|---|
| `score_default` | the submission score, and only this one |
| `showcase_model` | capability, never the score |

`showcase_dense` is the precedent: it improves `clean` and `browsing` MRR,
drops `boundary` MRR 1.000 → 0.870, and is therefore never the default. **The
routing rule is fixed now, not after a winner is known.**

## The ceiling must be measured before it is claimed

**Revision 1 asserted a 0.142 headroom and declared any larger gain "a bug".
That claim is withdrawn.** It was derived from `0.995 − 0.8526`, which assumes
the rerank window always contains the target — and the one relevant citable
number says otherwise: turn-level `recall100 = 0.6861` (`p6c1-arm-a`), i.e. the
target is in the 100-candidate pool on about 69% of *turns*. That is a hint,
not the ceiling, and the two are not the same statistic.

**Four measurements at A0, on `clean`, before any headroom claim is made.**
They are diagnostics of the existing baseline — no arm, no selection, no
tuning — and they are the first deliverable of R1:

| # | measurement |
|---|---|
| 1 | **Recall@rerank_window** — share of turns where the target is among the candidates handed to `_rerank` |
| 2 | **Target coverage by candidate depth** — the same at depths 10, 30, 50, 100, so the window size is chosen from evidence |
| 3 | **Conditional MRR given the target is in the window** — what the reranker achieves when it *can* succeed |
| 4 | **Oracle MRR** — every present target moved to rank 1. **This is the ceiling.** |

**The ceiling is `oracle MRR − 0.8526`**, and no gain claim may exceed it. Until
those four numbers exist, this document makes **no** headroom claim, and the
"any gain above +0.142 is a bug" sentence is deleted rather than softened.

## Resource gates, separate per arm

### A1 / `score_default` eligibility

| | requirement |
|---|---|
| latency | median overhead **≤ 0.50 ms** |
| memory | peak RSS increase **≤ 50 MB** |
| dependencies | **no new runtime dependency** |

A1 changes only numeric constants, so all three should be trivially met; they
are stated so that "trivially met" is a measurement and not an assumption.

### A2 feasibility / `showcase_model`

**The ceiling is set after R0 measures reality, and before any quality result
exists.** Fixing a number now would be guessing; fixing it after a quality
result would be choosing the number that admits the answer we liked.

Procedure, itself pre-registered:

1. R0 measures cold load, steady pair latency and RSS for the shortlist.
2. The A2 ceiling is set from those measurements and **committed**, with its
   justification, **before any labelled data is touched**.
3. Offline artifact loading and determinism are required regardless, and are
   not negotiable against latency.

**A gate must not be set so tight that no real model could pass it merely so
the phase can report that semantic reranking "was evaluated".** That would be a
theatre of rigour: the honest outcomes are a real ceiling met, a real ceiling
missed, or Gate 0 unresolved — not a rigged one.

### A2 → `score_default` promotion

* A **separately frozen, stricter** gate, committed before A2's quality run.
* The **lexical fallback must be retained** and must work with A2 absent.
* **No post-hoc promotion.** An arm that scores well but was built as
  `showcase_model` stays `showcase_model`. Promotion requires the stricter gate,
  decided in advance.

## Quality gates

| gate | requirement |
|---|---|
| **clean MRR benefit** | `clean` MRR improves by **≥ +0.010**, paired against A0 |
| **no slice regression** | **no official slice's MRR may decrease at all** — `boundary` is at 1.000000 |
| **composite floor** | `clean` composite must not decrease from **0.932067** |
| **robustness** | per stochastic scenario, on identical seeds `(7,8,9,10,11)`: mean paired **Δscore ≥ −0.005** and mean paired **ΔHR@10 drop ≤ 0.01**. Paired-delta SD reported, never used as a loss budget |
| **supplementary** | defined below |
| **latency / memory** | per the arm's own gate above |

### "Supplementary must not regress", stated exactly

| | |
|---|---|
| **metric** | primary: composite `recommended_technical_score`. Secondary, reported always: MRR |
| **tolerance** | paired **Δcomposite ≥ −0.005** and paired **ΔMRR ≥ −0.010** |
| **pairing** | `supplementary_dev` is deterministic — no `reply`/`mutate`/`init`/`sample_tf` hook, so `record.matrix` runs it at a single seed. Pairing is therefore exact, not statistical, and no seed list is needed |
| **role** | **VETO, not validation** — for A0 and A2, over the full corpus. It can block adoption; its number is never reported as a score, and `record.py` carries `source`/`official` on every row so it cannot be mistaken for one |
| **role for A1** | A1's weights are selected on `sup-train`, so the **full corpus is contaminated for A1**. A1's veto and its validation number both come from **`sup-val` only**, which the search never sees |

## Every experiment reports the full set

`clean` composite/HR@10/MRR/MTTC; **all four official slices separately**;
the five robustness scenarios; and supplementary per the role above.
`showcase_dense` is why: a `clean`-only report would have shipped it.

## The overfitting prohibition

The public 200 has been used in every phase from 1 to 6C1.

* **Selection happens on `sup-train` only.**
* **The public 200 is a CONFIRMATION set**, evaluated **once per arm**, after
  that arm is frozen and committed.
* **No arm may be modified after seeing its public number.** A modified arm is
  a new arm and needs a new pre-registration.
* **Three arms, fixed here.** Reporting the best of many arms searched on the
  confirmation set is the same error in a different hat.
* **The sealed holdout is not run**, for selection or confirmation.
* If a `sup-val` gain does not survive on the public 200, **that is the result**
  and it is reported, not re-tuned.

## Stop conditions

Stop if: an arm is selected or tuned on the public 200; a fourth arm is
proposed after a measurement; the sealed holdout is touched; an artifact is
fetched at evaluation or runtime; the lexical fallback stops working without
A2; R0 selects A2 using any quality signal; the A2 ceiling is set after a
quality result; or a gain is reported without its four slices, robustness and
supplementary.

## Predictions

1. **A1's headroom is small.** Six phases of tuning precede it; `boundary` MRR
   is already 1.000000 and `intent_override` 0.922222, so nearly all available
   gain sits in `browsing` (0.809) and `buying` (0.851).
2. **Slice regression will bind before mean gain does.** Any reranker
   aggressive enough to move `browsing` will move `boundary`, which has nothing
   to gain.
3. **Conditional MRR given the target is in the window will be much higher than
   0.8526**, and the oracle ceiling correspondingly tighter than the naive
   `0.995 − 0.8526 = 0.142`. If measurement 3 comes back near 0.85, the
   reranker is failing on lists that *do* contain the answer, which would make
   A1 far more promising than I expect — and that is the outcome worth
   discovering early, which is why the four measurements come first.
4. **A2's binding constraint will be cold load, not steady latency.** The
   catalog already costs ~10.7 s to load; a vendored model adds to a budget
   that is already the largest fixed cost in the system.
