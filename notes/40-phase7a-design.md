# Phase 7A design — reranker feasibility, and what it is allowed to prove

**Revision 4. Design and pre-registration only.** No dependency installed, no
model downloaded, no R0 timing, no feature cache, no A1 trial, no `sup-val`
run, no public evaluation. R0 has not begun. Phase 6 is closed (`notes/39`) and
is not modified.

Revision 4 fixes one **code-grounded topology error** and one **compute-plan
gap**, plus five consequences of them.

1. **`pool_depth` is not the rerank input depth.** Revision 3 said Top-30
   "matches `pool_depth`". It does not: `pool_depth = 30` is the window for the
   **pool-aware asker** and the profile/shadow snapshots. `_rerank` receives the
   **complete candidate list** — up to 100 normally, up to **1000** under the
   starvation bypass.
2. **A2 is now an explicit cascade** with its own isolated
   `semantic_rerank_k`, so nothing Phase 7 does can reach `pool_depth`.
3. **The R0 workload measured the wrong thing** — a literal `" [SEP] "` string
   and batch 1 as "per-turn reality". Both are wrong.
4. **The artifact cap counted only weights.** It now counts everything needed
   offline, plus the largest individual file.
5. **The A1 search had no compute plan.** Measured: 189 full-simulation trials
   is **~6.4 h at median and ~17.7 h at the worst observed cell time**. A
   cached-feature contract replaces it.
6. **`f_phrase` was described wrong.** Under the shipped `phrase_idf=False` it
   is confidence-weighted, not IDF-weighted.
7. **`sup-val` needs a fresh paired A0** per arm, not a reused corpus number.

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

**Phase 7 holds retrieval fixed and tests how much additional score can be
recovered by reordering the candidates it receives.**

### The reranker's actual input, from source

Revision 3 claimed a Top-30 workload "matching `pool_depth`". **That was
wrong, and it is withdrawn.**

```python
limit  = max(top_k, depth) if turn_cfg["rerank"] else top_k
cands, trace = self._candidates(state, turn_cfg, limit)
ranked = self._rerank(cands, state)          # the COMPLETE list
```

`_rerank` scores **every** candidate it is handed. The depth comes from the
retrieval decision, not from `pool_depth`:

| | value | when |
|---|---|---|
| `candidates` | **100** | standard deep-funnel path (`funnel_top = 100`) |
| `starved_candidates` | **1000** | starvation bypass |

**`pool_depth = 30` is a different thing entirely** — `agent.py:82` calls it
"candidates inspected by the pool-aware asker". Its consumers are the question
controller (`dialogue.py:208, 233, 298, 486`), the profile window
(`agent.py:677`) and the Phase 6A shadow snapshot (`context.py:486, 492`).
**None of them is the reranker.**

**Phase 7 does not change or reuse `pool_depth`.** Touching it would move
question selection and the profile window, breaking the "retrieval and dialogue
held fixed" contract and invalidating Phase 6B2-R2's and 6C1's evidence in the
same stroke. Any Phase 7 depth is a **new, isolated** config value.

That is the bounded claim, and revision 2's "not a retrieval problem" was not.
A final HR@10 of 0.995 says most *remaining score headroom* is ranking-related,
but it is a **session-level** figure accumulated across turns. The turn-level
number tells a different story: `recall100 = 0.6861` (`p6c1-arm-a`), so on
roughly 31% of turns the target is not in the 100-candidate pool at all, and on
those turns no reranker can do anything. **Retrieval availability still matters
across turns**; Phase 7 simply does not attempt to change it.

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

**No public labels. No quality metric. No MRR.** R0 may not compute a quality
score, because a candidate chosen even partly on quality would already have
spent the confirmation set's independence.

#### Documentation research — exact sources, not "search Hugging Face"

For **each** of at most three candidates, cite:

* the **primary Hugging Face model card** (URL);
* the **upstream GitHub or runtime documentation** for the backend;
* the **license**, by name and source.

Record the **exact revision / commit hash**. **A moving `main` is not a
revision, and "a tiny model" is not a model ID.** A candidate that cannot be
pinned to an immutable revision is not eligible, because nothing measured
against it could be re-run.

| field | why it is required |
|---|---|
| model ID | ambiguity makes the run irreproducible |
| revision / commit hash | `main` moves; a measurement against it cannot be repeated |
| license | a good score is worthless if the weights cannot ship |
| parameter count, on-disk artifact size | the vendoring budget |
| backend | the dependency actually being proposed |
| quantization | changes size, latency and determinism together |
| maximum sequence length | decides whether product text must be truncated |
| offline loading path | proves nothing is fetched at runtime |

#### The workload, fixed before any model is timed

R0 benchmarks **the proposed cascade component**, not the current reranker and
not a synthetic pair loop. For every candidate, the measured unit is
**end-to-end scoring of a fixed Top-30 prefix**, including every step that
would run in production:

1. query construction and tokenization;
2. **30 query–product pairs**;
3. model inference;
4. score extraction;
5. deterministic ordering of the 30.

| | specification |
|---|---|
| **query fixtures** | **32 synthetic, unlabelled** queries, committed in the R0 fixture module, built from `sup-train` message templates only — no ground truth, no target, no label |
| **product-text fixtures** | **100 product blobs**, sampled deterministically from the catalog by `sha256(asin)` rank, committed as a frozen id list |
| **pair construction** | the **tokenizer/model's own native pair API** — e.g. `tokenizer(query, passage)` for a cross-encoder. **No literal `" [SEP] "` string.** Revision 3 specified one, which is wrong: BERT, RoBERTa, DeBERTa and T5 families use different special-token contracts, and hard-coding one silently mis-tokenizes for every family that does not use it, producing a latency and quality number for a model nobody would ship |
| **prefix size** | **Top-30**, chosen as a plausible `semantic_rerank_k` for timing purposes only. It is **not** a claim about the reranker's current input, and R1's actual `semantic_rerank_k` is chosen from `sup-train` coverage |
| **max sequence length** | **256 tokens**, truncating the product side first |
| **GATED workload** | the **fixed end-to-end Top-30 batch strategy that would actually ship** — the production turn receives all 30 candidates at once, so they are scored as one batched call |
| **diagnostics, reported never gated** | batch **1**, **8**, **32**. Revision 3 gated batch 1 as "per-turn reality"; that was backwards. Batch 1 is a per-pair diagnostic, and gating it would have selected a model on a workload that never occurs |
| **warm-up** | **20** end-to-end Top-30 scorings, discarded |
| **measured repetitions** | **7 fresh processes**, alternating order, via `lab/benchmark.py` |
| **cold load** | process start → first scored Top-30 returned, **including** artifact load and tokenizer init |
| **steady latency statistic** | **p95** over the end-to-end Top-30 workload; median reported alongside |
| **RSS** | `peak_rss_bytes` from the harness, minus the same measurement with the model absent |
| **determinism tolerance** | **bit-identical ordering** across processes, and scores equal to **1e-6** |

**Every candidate runs the identical workload.**

**Architecture parity.** If cross-encoders and bi-encoders are compared, each
candidate's measurement must include **all work its architecture requires**:

| | cross-encoder | bi-encoder |
|---|---|---|
| query encoding | per pair | once per turn |
| product encoding | per pair | per candidate, **or** loading precomputed embeddings |
| similarity / scoring | model head | dot product / cosine |
| ordering | required | required |

A bi-encoder that hides its product encoding in a precomputed index has moved
that cost into the **artifact** and the **cold load**, not eliminated it, and
both caps below count it.

#### Artifact size — everything needed offline, not just weights

The cap covers the **complete** vendored footprint:

* model weights;
* tokenizer files (vocab, merges, config, special-token map);
* quantization metadata;
* **precomputed product embeddings or index, if the candidate needs one**;
* any other local model data required to load and score offline.

| cap | limit |
|---|---|
| **total** local artifact | **≤ 120 MB** |
| **largest individual file** | **≤ 100 MB**, and recorded regardless |

The per-file cap exists because **a 120 MB total containing one 115 MB file is
not vendorable** through ordinary repository hosting. If an approved
LFS or package-distribution path exists, the per-file cap is lifted **only by
naming that path in R0's record** — never by observing that a candidate needs
it.

#### Selection rule, fixed now and applied mechanically

1. **Discard** every candidate failing **any** hard cap.
2. Among those remaining, choose the **lowest Top-30 p95 latency**.
3. Tie-break: **lower RSS**, then **smaller artifact**, then
   **lexicographically smaller model ID**.

**If no candidate passes, A2 is infeasible and the phase records that.** **No
cap may be relaxed after seeing the measurements** — a cap moved to admit a
candidate is not a cap, and the infeasible outcome is a real result rather than
a failure to be engineered around.

**Commit the R0 result before any quality experiment.**

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
| `f_phrase` | **confidence-weighted** share of query phrases present in the blob. **Not IDF-weighted:** the IDF branch exists but is dead under the shipped `phrase_idf = False`, which revision 3 described wrongly |
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

* **Split — scenario-stratified, exact counts, frozen before any trial.**
  Revision 2's global `sha256 % 5` split gave 806/194 with **accidental**
  scenario proportions: a hash split does not preserve strata, so `boundary` —
  50 samples in the whole corpus — could have landed anywhere. **That split is
  SUPERSEDED and is not the operative one.** Its hash `211be164cec5ff4f` is
  recorded here only so the change is visible, and it is never used.

  Within each `scenario_type`, sample ids are ranked by canonical
  `sha256(sample_id)` hex ascending and the first N taken as train:

  | scenario | total | train | val |
  |---|---|---|---|
  | `buying` | 400 | **320** | **80** |
  | `browsing` | 400 | **320** | **80** |
  | `intent_override` | 150 | **120** | **30** |
  | `boundary` | 50 | **40** | **10** |
  | **total** | **1,000** | **800** | **200** |

  Hashes over **newline-delimited canonical ids, sorted, with a trailing
  newline**:

  | | |
  |---|---|
  | `sup-train` | `48d14de25a4adf90adbcd9ad621ea2e1d143bd5632a8be67fed239ff4822290d` |
  | `sup-val` | `82e0470ee83d2cf8883399ededda11b5ddb4fa762685196b36a9fe521a105a73` |

  **Verified before registration:** overlap **0**; union **1,000**; every
  sample assigned **exactly once**; no duplicate within either side.

  ```bash
  python3 -c "
  import json,hashlib,collections
  rows=[json.loads(l) for l in open('data/supplementary_dev.jsonl')]
  TRAIN={'buying':320,'browsing':320,'intent_override':120,'boundary':40}
  by=collections.defaultdict(list)
  for r in rows: by[r['scenario_type']].append(str(r['sample_id']))
  tr,va=[],[]
  for sc in sorted(by):
      ids=sorted(by[sc], key=lambda s: hashlib.sha256(s.encode()).hexdigest())
      tr+=ids[:TRAIN[sc]]; va+=ids[TRAIN[sc]:]
  h=lambda xs: hashlib.sha256(('\n'.join(sorted(xs))+'\n').encode()).hexdigest()
  print(len(tr),h(tr)); print(len(va),h(va)); print('overlap',len(set(tr)&set(va)))"
  ```

  No RNG and no seed: the split is a function of the ids and the corpus alone.
* **Method.** Deterministic coordinate descent. Fixed sweep order:
  weights sorted alphabetically by name. **3 sweeps.**
* **Grid, relative to the FROZEN ORIGINAL DEFAULT — not the current value.**

  ```
  candidate(w) = multiplier × original_default(w),
  multiplier in {0, 0.25, 0.5, 1, 2, 4, 8}
  ```

  The current best vector supplies every *other* coordinate; the coordinate
  being tested always uses the original absolute grid. **This removes a trap in
  revision 2:** with a grid multiplying the *current* value, a weight set to 0
  in sweep 1 was pinned at 0 for every later sweep, because every multiplier of
  0 is 0. A weight can now be zeroed early and **restored** later, so sweep
  order cannot permanently delete a feature.
* **Pinned, and not silently in the search**, at their shipped values:
  `phrase_idf = False`, `soft_adaptive = False`, `w_soft = 0.0`,
  `w_soft_lo = 0.0`, `w_soft_hi = 2.5`. **A1 does not activate a currently
  disabled feature** — it reweights what is already live, and turning on a dead
  branch would be a new formulation wearing a reweighting's name.
  `w_soft_lo`, `w_soft_hi` and `soft_adaptive` They govern how
  `w_soft_eff` is chosen at run time, so varying them would change the model's
  *structure* rather than reweight it — and would do so invisibly, since none
  of the three appears in the score expression by name.
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
* **Final tie-break, in order.** (1) highest `sup-train` MRR; (2) lowest L1
  norm of the weight vector; (3) lexicographically smallest canonical tuple of
  all nine weights, in the fixed alphabetical order above. Total and
  deterministic, so no seed is required — stated rather than assumed.
* **Freeze.** The resulting weights are committed **before** any public
  confirmation run.

#### The compute contract — cached features, and why

Revision 3 said "189 trials on `sup-train`" without saying what a trial costs.
**Measured from the ledger** (26 recorded `supplementary_dev` cells): a
1,000-session cell takes a median **153.1 s**, worst observed **421.3 s**. At
800 sessions that is ~123 s per trial at median, so **189 full-simulation
trials ≈ 6.4 hours at median and ≈ 17.7 hours at the worst observed cell** —
before the host starvation this project has already measured at 4×.

**The adopted contract is the cached one:**

1. **Run A0 once** on `sup-train`.
2. **Record a frozen training cache**, per turn: candidate ids, **all 13
   feature values**, the target id, and session/turn identity.
3. **Hash the cache** and commit the hash before the first trial.
4. **Run the 189 coordinate-descent trials over the cached feature matrices
   only.** Each trial is then a linear re-score and a sort — milliseconds, not
   minutes.
5. **After the weights are frozen, run the real full Agent on `sup-val`.**
6. **No retuning after that full-Agent validation.**

**The cache stores FEATURES, never A0's final scores.** A cached score cannot
be re-weighted; a cached feature vector can, and all nine searched weights must
be recomputable exactly from it.

**Cached objective, stated exactly.** Per session, take the turns in order;
for each turn re-score its cached candidates with the trial weights; the
session's reciprocal rank is `1/rank` of the target on the **earliest turn
where it appears in the cached candidate list**, else 0. The objective is the
**mean over `sup-train` sessions** — session-level, matching the evaluator's
semantics rather than a turn-level average.

**This is an off-policy approximation, and the document says so rather than
discovering it later.** The cache is generated under **A0's** behaviour.
Different weights would reorder candidates, which can change which product the
simulated customer sees, which can change the clarification question asked,
which can change later turns entirely. **The cached objective therefore
estimates the effect of reweighting while holding the dialogue path fixed, and
the real effect can differ.** That is exactly why step 5 exists: `sup-val` is
run through the **full Agent**, on-policy, and it is `sup-val` — not the cached
number — that is reported as A1's validation.

If a full-simulation search is ever preferred instead, **record one trial's
wall time and the projected total, and fix a compute budget, before starting.**
Discovering at trial 80 that the search is infeasible and switching method
mid-run would make the completed trials unusable and the method
post-hoc.
* **`sup-train` is never reported as validation.** It selected the weights; it
  cannot also measure them.

### A2 — CPU-local semantic reranker, as an explicit CASCADE

A2 is **not** a replacement scorer over the whole candidate population. It is a
second stage over a prefix, and the topology is pre-registered here:

1. **A0 scores the complete candidate population exactly as today** — all 100,
   or all 1000 under the starvation bypass. Unchanged, not reimplemented, not
   re-weighted.
2. **A2 receives only the first `semantic_rerank_k` candidates of A0's
   ordering.**
3. **A2 may reorder only that prefix.**
4. **Candidates after the prefix keep A0's order**, appended unchanged.

So the output is `A2(prefix) ++ A0(tail)`, and with `semantic_rerank_k = 0` the
result is bit-identical to A0 — which is what makes the lexical fallback a
genuine fallback rather than a claim.

**`semantic_rerank_k` is a NEW, ISOLATED config value.** It must not affect
`pool_depth`, retrieval depth, `candidates`, `starved_candidates`, or question
policy. A test asserting that the question controller's and profile window's
behaviour is invariant to it is part of R1's definition of done.

**Consequence for the ceiling measurements.** They must measure target coverage
**in the post-A0 prefix**, not in the raw retrieval pool, at candidate depths
**10 / 30 / 50 / 100** — because the prefix is what A2 can actually reach. A
target sitting at A0 rank 140 is invisible to a `semantic_rerank_k` of 100 no
matter how good the model is.

**`semantic_rerank_k` is frozen from `sup-train`** before `sup-val` or any
public evaluation is run.

**Deferred to R1, and required there before labelled validation:** whether A2
uses **semantic-only order** over the prefix, or a **frozen fusion** of the A2
score with A0's. If fusion, its formula and weight are fixed in R1 and **cannot
be selected on the public set** — the same rule every other parameter obeys.

`_DenseIndex` is **not** A2: it is a stdlib random-indexing *candidate source*
with a measured BM25 overlap of 0.020, aimed at recall.

## `score_default` and `showcase_model`

| profile | may claim |
|---|---|
| `score_default` | the submission score, and only this one |
| `showcase_model` | capability, never the score |

`showcase_dense` is the precedent: it improves `clean` and `browsing` MRR,
drops `boundary` MRR 1.000 → 0.870, and is therefore never the default. **The
routing rule is fixed now, not after a winner is known.**

## The ceiling — measured on supplementary, never on the public set

**Revision 2 put these four measurements on `clean` as "R1's first
deliverable". That is withdrawn.** A number computed on the public 200 that
then informs rerank-window depth, candidate count, feature set, weights or
model choice is a design input drawn from the confirmation set, and it would
spend the independence the whole phase depends on. The wording that made the
public ceiling a design deliverable is deleted, not softened.

**Where each measurement lives:**

| corpus | role |
|---|---|
| **`sup-train`** | diagnostic and **design input**. Window depth, candidate count, features, weights and model choice may be decided here and nowhere else. |
| **`sup-val`** | **frozen validation.** Not consulted during selection. |
| **public 200** | computed **once, at final confirmation**, after **every** arm *and the rerank window depth* are frozen. |

**Public oracle diagnostics may be reported at confirmation, and no decision
may change afterwards.** If the public ceiling turns out to differ from the
supplementary one, that is a finding to report, not a licence to re-pick a
window depth.

### The oracle is defined at SESSION level

Revision 2's oracle was turn-level, which is not the quantity the evaluator
scores. Per session:

1. Find the **earliest turn** at which the target is in the **rerank input
   window**.
2. The oracle moves it to **rank 1 on that turn**.
3. If it **never** appears in any turn's window, the session **remains a miss**
   — the oracle does not rescue what retrieval never delivered.
4. Recompute **HR@10, MRR, MTTC and composite** using the **evaluator's own
   session semantics**, not a re-derivation of them.

**Turn-level coverage is reported separately and is NOT the MRR ceiling.** The
two differ whenever a session finds the target on a later turn, which is
exactly what MTTC measures, and conflating them would overstate what reordering
can win.

### The four measurements, on `sup-train`

| # | measurement |
|---|---|
| 1 | **Recall@rerank_window** — share of **sessions** with at least one turn whose window contains the target, and share of **turns**, reported separately |
| 2 | **Target coverage by candidate depth** — at 10, 30, 50, 100, so window depth is chosen from evidence rather than from the current default |
| 3 | **Conditional MRR given the target reached the window** — what the reranker achieves when it *can* succeed |
| 4 | **Session-level oracle MRR and composite**, per the definition above |

**The ceiling is `oracle − A0`, measured on `sup-train`.** No headroom claim is
made until these exist, and none is made from the public set at any point
before confirmation.

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

**The caps are the ones fixed in R0 above** — artifact ≤ 120 MB, cold load
≤ +5.0 s, Top-30 p95 ≤ 25 ms, RSS ≤ +400 MB, offline load, determinism,
redistributable license — together with the mechanical selection rule. They are
numeric, they are fixed before any candidate is measured, and they are not
adjusted afterwards.

Revision 2 deferred these to "after R0 measures reality", which sounds
disciplined and is not: a bar drawn after seeing the candidates is a bar drawn
around them. **A derivation rule fixed in advance is the only version of
"set it from measurements" that means anything.**

**A gate must not be set impossibly tight merely so the phase can report that
semantic reranking "was evaluated".** That is a theatre of rigour. The honest
outcomes are a real cap met, a real cap missed, or Gate 0 unresolved — and the
caps above were chosen to be *passable by a real small cross-encoder*, not to
guarantee either answer.

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
| **role** | **VETO, not validation.** It can block adoption; its number is never reported as a score, and `record.py` carries `source`/`official` on every row so it cannot be mistaken for one |
| **which split** | **`sup-val` only, for every arm.** Not just A1. Under revision 3 `sup-train` also supplies the ceiling diagnostics that fix the **rerank window depth**, and that depth applies to A0, A1 and A2 alike — so `sup-train` is a design input for all three and cannot serve as an unbiased veto for any of them. Revision 2 exempted only A1; that was too narrow |
| **A0's own numbers** | A0 is the control, so its `sup-train` figures are diagnostics rather than a claim. Its **veto and validation numbers still come from `sup-val`**, so all three arms are judged on the same untouched split |

**A fresh, paired A0 baseline per arm.** For every arm, A0 is re-run on the
**same `sup-val` rows**, under the **same frozen `semantic_rerank_k` and window
configuration**, and the **same evaluator version**, in the same session as the
arm it is compared against.

**An A1 or A2 `sup-val` number is never compared against an older
full-corpus A0 result.** That comparison would differ in the population (200
rows against 1,000), in the configuration, and possibly in the evaluator — three
confounds at once, any of which could manufacture or hide the effect being
claimed. The paired-delta guards in the quality gates assume a genuine pairing,
and this is what makes them true.

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
3. **Conditional MRR given the target reached the window will be much higher
   than the unconditional figure**, and the session-level oracle ceiling
   correspondingly tighter than any naive `HR@10 − MRR` arithmetic. If it comes
   back close to the unconditional number, the reranker is failing on lists
   that *do* contain the answer, which would make A1 far more promising than I
   expect — the outcome worth discovering early, and why the four measurements
   come first and come from `sup-train`.
4. **A2's binding constraint will be cold load, not steady latency.** The
   catalog already costs ~10.7 s to load; a vendored model adds to a budget
   that is already the largest fixed cost in the system.
