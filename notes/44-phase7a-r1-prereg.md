# Phase 7A-R1 pre-registration — quality evaluation of A0, A1 and A2-10

**Revision 4. PRE-DATA CORRECTION, committed 2026-08-30.** No cache has been
built, no trial has been run, no `sup-val` and no public row has been touched.
Revision 4 exists because revision 3's text and the implementation had come
apart on five points, and an experiment that runs under one contract while its
report quotes another is not a pre-registered experiment. Every correction below
is made **before** the first measurement and is listed in §0 so the difference
between revision 3 and revision 4 is a diff and not a memory.

Revision 3's own summary is kept verbatim below, so what changed stays visible.

---

**Revision 3. Design and pre-registration.** R1 implementation begins on this
document; no further review round is required.

Revision 3 adds two eliminations that revision 2 lacked, and corrects one
overclaim:

1. **No-op elimination.** An arm that reduces to A0 is not a passing arm, and
   must not consume the single public confirmation.
2. **Challenger qualification.** Revision 2's `sup-val` gates were *floors* —
   "did not regress badly". Clearing a floor is not evidence of a positive
   signal, and an arm must show one to compete.
3. **The multiple-comparisons claim** was quantified when it should not have
   been.

Both feasibility conclusions stand and neither is edited:

| | |
|---|---|
| **A2-30** | **FAIL** under the frozen 25 ms cap — 69.68 ms, `notes/41` |
| **A2-10** | **PASS** every frozen cap, 7/7 — 15.23 ms, `notes/43` |

**Three arms, fixed: A0, A1, A2-10.** A2-30 never enters quality evaluation.

## 0. Revision 4 — the five pre-data corrections

Nothing here is a result, a threshold change, or a gate loosened after seeing a
number. Each item names what revision 3 said, what is true instead, and why the
difference could have produced a wrong claim.

### 0.1 The A1 no-op condition is `best_mrr <= baseline_mrr`

**Revision 3, §7b Step 0, said:** *"the final nine weights are exactly identical
to the original defaults"*.

**Revision 4 says:** A1 is a no-op iff

```
delta_mrr = best_mrr - baseline_mrr <= 0
```

both measured on the `sup-train` cache under the cached evaluator semantics of
§6. **Weight equality is a DIAGNOSTIC and is not the test.** It is still
recorded, as `weights_unchanged`, and it is still informative — but it cannot
decide the question.

**Why the revision 3 wording was wrong, concretely.** The search's tie-break can
move many weights without improving the objective. On a corpus where no weight
separates anything, every candidate ties on MRR and the tie-break returns
whichever vector it prefers. That vector differs from the defaults, so a
`weights != DEFAULTS` test would report a challenger while `best_mrr ==
baseline_mrr` — an arm with **no quality gain at all** consuming the single
public confirmation. The condition has to be about the objective, because the
objective is what the arm is claiming to improve.

`delta_mrr <= 0`, not `< 0`: an arm that exactly ties the baseline has not been
shown to work, and Step 0 exists to stop exactly that.

### 0.2 Ties keep the default vector, by an L1 distance tie-break

The search's ordering is, in full:

| rank key | |
|---|---|
| 1 | **`sup-train` cached MRR**, higher wins |
| 2 | **lowest L1 distance from the ORIGINAL DEFAULT weights** |
| 3 | the canonical weight tuple, so the order is total |

Tie-break 2 is **distance from the defaults**, not the absolute L1 norm.
With the norm, a tie on MRR is broken by whichever vector happens to be smaller,
so the weights drift for free and a no-op arm arrives at `sup-val` looking
tuned. With distance from the defaults, **equal MRR returns the shipped vector
exactly**, and "the search found nothing" is represented by the vector that says
so.

### 0.3 The operative split is 800/200 and nothing else may run

`notes/40` registered a scenario-stratified split and superseded a global
`sha256 % 5` split that gave **806/194**. Both numbers appear in the notes; only
one is operative, and any driver reporting 806 has run the retired one.

| | train | val |
|---|---|---|
| `buying` | 320 | 80 |
| `browsing` | 320 | 80 |
| `intent_override` | 120 | 30 |
| `boundary` | 40 | 10 |
| **total** | **800** | **200** |

| | |
|---|---|
| `sup-train` id hash | `48d14de25a4adf90adbcd9ad621ea2e1d143bd5632a8be67fed239ff4822290d` |
| `sup-val` id hash | `82e0470ee83d2cf8883399ededda11b5ddb4fa762685196b36a9fe521a105a73` |

**The driver asserts all of this at startup, before a session runs**, and raises
otherwise: row counts, per-scenario counts, both id hashes, `train ∩ val = ∅`,
`|train ∪ val| = 1000`, no id outside `supplementary_dev_`, and no `public_` or
`supplementary_holdout_` id anywhere. **806 or 194 on either side is refused by
count alone**, named as the superseded split rather than reported as a near
miss. Membership in the forbidden corpora is a **namespace test**: a guard that
had to open the sealed holdout to prove it was untouched would be touching it.

A negative test constructs the retired 806/194 split and requires the guard to
reject it — a guard that has never rejected anything is a comment.

### 0.4 The cache captures a frozen snapshot, not a live reference

The cache builder recorded `states_by_key[(sample_id, turn)] = (cands, state)`,
storing the **live session dict**. The session keeps mutating it for the rest of
the run — appending slots, flipping `active`, extending `terms`, rewriting
`shown` — so every cached turn would have replayed against the session's FINAL
state. Turn 2 would have been checked against an input that only existed after
turn 5, and it would have passed, because both sides would be wrong the same
way.

**Required instead:**

* the snapshot is a **deep copy taken at the exact `_rerank` call site**, before
  `_rerank` is entered;
* it carries every `SlotValue` field as it stood **at that call** — `active`,
  `polarity`, `hardness`, `confidence`, `soft_ok`, `source_turn`, and the rest —
  as a comparable fingerprint, not only inside an object graph;
* `cands` keeps its **retrieval order and BM25 scores** exactly as received;
* nothing is reconstructed from the trace afterwards;
* the replay reads the frozen snapshot and never the live session.

A test mutates the live state after capture — appending slots, inverting every
polarity, clearing every `active` and `soft_ok` — and requires the snapshot to
be unchanged. A second test recreates the defect by pointing turn 1's snapshot
at the live state and requires the gate to fail.

**The default replay gate must print, before trial 0:**

| | |
|---|---|
| checked sessions | **800** |
| checked turns | recorded |
| full-order mismatches | **0** |
| cached default MRR | recorded |
| live A0 MRR | recorded |
| delta | **exactly 0** |
| cache sha256 | recorded |
| `sup-train` split hash | recorded |
| agent commit and sha256 | recorded |
| catalog sha256 | recorded |

Each turn is checked **twice against one ground truth** — the order `_rerank`
actually returned during the session. Re-running `_rerank` on the frozen
snapshot must return it (this is what fails if the snapshot was a live
reference), and re-scoring the cached features with the default weights must
return it too (this is what fails if the cache dropped a feature or mis-mapped a
weight). Both compare the **full candidate order, element for element**, not the
Top-10. **Every turn is checked**, so a reported count of zero is a statement
about the whole cache; any mismatch stops the phase before the first trial.

**`live A0 MRR` is A0's own order under the cached evaluator semantics**, not
the evaluator's post-`_rotate` number. Those are different quantities — the
cache records `_rerank`'s order and `_rotate` runs after it — so the evaluator's
MRR over `sup-train` is reported alongside as a **diagnostic**, which turns "the
two coincide because nothing asked for alternatives" from an assumption into a
recorded fact.

### 0.5 Telemetry: what invalidates a shard, and what counts as an invocation

| | |
|---|---|
| **experiment-invalidating** | `model_absent`, `load_failure`, `inference_failure`, `bad_permutation` |
| **model-invoked** | `inference_failure`, `bad_permutation`, `reranked` |
| **legitimate non-invocation** | `mode_off`, `lambda_zero`, `prefix_too_short`, `ineligible`, `empty_query` |

Two corrections against revision 3:

* **`model_absent` is now invalidating.** Revision 3 called it "a configuration
  fact", which is true and beside the point: an A2 shard whose model directory
  was missing measured **A0 on every turn**, and the production path's fail-open
  is exactly what makes that invisible in the score. The question the set
  answers is not who is to blame, it is whether the shard measured what it
  claims. It did not.
* **`inference_failure` now counts as invoked, and `load_failure` does not.**
  An inference failure means the session existed, the batch was fed and the
  forward pass raised — the model was invoked. A load failure means no session
  was ever constructed. Counting a load failure as an invocation would put turns
  with no inference into the denominator of every per-invocation figure.

`ineligible`, `empty_query` and `prefix_too_short` are the cascade correctly
declining to call the model, and are descriptive only.

**λ = 0 is a legitimate outcome, and A2 is then eliminated.** Both, without
tension: §5 says λ = 0 means the semantic signal failed to beat A0 and is
reported as such rather than retried; §7b Step 0 then disqualifies A2 as a
no-op. The arm ran, and what it found was nothing.

**Any invalidating reason with a count above zero makes the shard
non-citable.** Not a rate and not a threshold — one turn on which the model did
not run is one turn of A0 reported as A2. The rule lives in the single
citability predicate (`lab/provenance.py`), so it cannot be forgotten by a
report that did not think to check:

* the shard is **invalid and non-citable**;
* it produces **no A2 quality verdict**;
* the environment is fixed and the shard is **re-run from a fixed commit**. The
  row is never repaired in place, because results are never rewritten.

The same applies to a λ = 0 turn that did not reproduce A0 byte-for-byte: that
is the fallback's correctness proof failing, and it is counted, not averaged.

### 0.6 Two operational rules that follow

**The catalog cache may not close an object a live Agent still holds.** The
shared cache was keyed on the resolved path alone, so a request for richer
semantic fields superseded a cached catalog **and closed its SQLite connection**
— pulling it out from under an A0 Agent that was still live and had done nothing
wrong. In R1 the A0 and A2 arms are constructed against the same catalog **by
design**, so this is on the experiment's path and not a test artefact. The key
now carries the capability, `(resolved_path, extras, semantic_fields)`,
capability versions coexist, and **`clear_catalog_cache()` is the only place
anything is closed**.

**Each arm of a paired `sup-val` run gets its own leased process.** Shared
module-level singletons — the catalog cache, an ONNX session, a tokenizer —
are exactly the surface on which one arm's construction can perturb another's,
and a paired comparison is worth nothing if the pairing is the thing that broke
it.

### 0.7 What revision 4 supersedes

| superseded | by |
|---|---|
| revision 3 §7b Step 0, *"the final nine weights are exactly identical to the original defaults"* | §0.1 — `delta_mrr <= 0` |
| revision 3 §9b, `model_absent` as a non-invalidating configuration fact | §0.5 — invalidating |
| `notes/40`, *"the public 200 is a CONFIRMATION set, evaluated **once per arm**"* | **notes/44 §7b Step 3 — ONE finalist, one run, one number.** `notes/44` is the operative contract for R1 wherever the two documents differ |
| `notes/40`'s superseded 806/194 global-hash split | §0.3 — the stratified 800/200 split, asserted at startup |

`notes/40` remains the design document. **Where `notes/40` and `notes/44`
disagree, `notes/44` governs R1**, and the disagreements are the four rows
above.

## 1. A2 topology, frozen

```
A0(full candidate population)  →  semantic rerank of prefix  →  unchanged A0 tail
```

* `semantic_rerank_k = 10`
* `cross-encoder/ms-marco-TinyBERT-L2-v2` @ `81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc`,
  upstream `onnx/model_qint8_arm64.onnx`, ONNX Runtime + `tokenizers` + numpy
* **the lexical fallback is always available**
* **model absent or load failure → byte-equivalent A0 behaviour**, not an error

### The call site, pinned

```
candidates  →  A0 _rerank  →  _rotate  →  a0_ranked
```

**A2 runs after `_rotate`, never before**, and reorders only a copy of the
final visible window:

```python
a0_ordered = a0_ranked[:top_k]                      # agent.py:685, unchanged
effective_k = min(semantic_rerank_k, top_k, len(a0_ordered))
a2_ordered  = semantic_permute(a0_ordered[:effective_k]) + a0_ordered[effective_k:]
```

**`a0_ranked` is never replaced.** The question controller, the
`ContextSnapshot` and the profile window all continue to read `a0_ranked`, and
none may read A2's ordering. Verified consumers in `agent.py`:

| line | reads | after A2 |
|---|---|---|
| 700 | `_decide_question(state, ranked, …)` | **`a0_ranked`** — question policy unaffected |
| 683 | `_shadow_context(…, ranked, …)` | **`a0_ranked`** |
| 677 | profile window, from **pre-rerank `cands`** | untouched |
| 787 | `_compose(attribute, state, ranked, ordered)` | `ranked` = **`a0_ranked`**; `ordered` = `a2_ordered` |
| 789 | `recommendations` | `a2_ordered` |

**Why after `_rotate` and not before.** `_rotate` re-orders the tail using
`seen = set(state["shown"])` (`retrieval.py:46`) to surface unseen candidates
on a "show me something else" turn. Running A2 first would make the semantic
order an input to that rotation, so the two mechanisms would interact and
neither could be reasoned about alone. Running A2 last leaves `_rotate` reading
exactly what it reads today.

### Recommendation-set invariance

```python
effective_semantic_k = min(semantic_rerank_k, top_k, len(ranked))
```

The `top_k` term is what makes the returned **set** invariant for arbitrary
callers. With `semantic_rerank_k = 10` and a caller passing `top_k = 5`,
reordering the first *10* could promote a rank-7 item into the returned five
and **change the set**; clamping to `min(...)` permutes only within what the
caller will actually see. For the official `top_k = 10` the two coincide and
A2 reorders exactly the returned Top-10.

**Why this matters, from source.** `evaluator/local_evaluator.py:252-254`:

```python
if override_applied and target in ranked:
    best_rank = ranked.index(target) + 1
    hit_turn = turn
    break
```

`hit` and `first_hit_turn` depend only on **membership** of `ranked`;
`reciprocal_rank` depends on the **index**. So under a permutation of the
returned set, **HR@10 and MTTC are provably invariant and only MRR can move.**

### Pre-registered invariance tests

**On the FINAL EMITTED `recommendations`, not on a pre-rotate prefix, and not
by set comparison.** A `set` cannot see a duplicated ASIN or a dropped one —
`{a,b,c}` equals `{a,b,c,c}` minus a duplicate and equals a list that lost an
element and gained a repeat. Every assertion below uses **length +
`collections.Counter` + explicit duplicate and membership checks**:

1. **`len(a2_recs) == len(a0_recs)`**;
2. **`Counter(a2_recs) == Counter(a0_recs)`** — multiset equality, which
   catches a duplicate or a dropped item that set equality cannot;
3. **no duplicates**: `len(set(a2_recs)) == len(a2_recs)`;
4. **no illegal ASIN**: every id is in the catalog and was in `a0_ordered`;
5. **HR@10 opportunity identical** — `target in a2_recs` iff `target in
   a0_recs`, evaluated per turn;
6. **MTTC identical** — `first_hit_turn` equal across the whole session;
7. **A0 tail order unchanged**, element-wise: `a2_ordered[effective_k:] ==
   a0_ordered[effective_k:]`;
8. **`top_k ∈ {1, 5, 10, 20}`** each preserve the A0 returned multiset;
9. **`semantic_rerank_k = 0` byte-equivalent to A0**;
10. **model absent / load failure / inference failure → byte-equivalent A0**,
    each exercised separately.

### State invariance, corrected

Revision 1 said "**every `state` key identical**". **That is false.**
`agent.py:734-736`:

```python
for asin in ordered:
    if asin not in state["shown"]:
        state["shown"].append(asin)
```

`shown` is appended **in display order**, so permuting the recommendations
necessarily permutes the order in which new ASINs are appended. The correct
invariant is membership:

| key | requirement |
|---|---|
| every key **except `shown`** | **bit-exact** between A0 and A2 |
| **`shown`** | **same length**, and **`Counter(shown)` identical**. Order may differ |

**Why order may differ safely, proven rather than assumed.**
`retrieval.py:46`: `seen = set(state.get("shown") or ())`. **`_rotate` consumes
`shown` as a SET**, so no downstream behaviour can observe its order. A
pre-registered test asserts this directly: permuting `state["shown"]` and
re-running `_rotate` must return an identical list.

**Two-turn tests are required.** A single-turn assertion cannot catch state
divergence that only surfaces later, which is exactly the shape of a `shown`
bug. Running A0 and A2 for **two consecutive turns** on the same session, turn
2 must agree on:

* **route**;
* **`ask_attribute`**;
* **candidate membership** (`Counter` of the pre-rerank `cands`);
* **every state key except `shown`**, bit-exact;
* **`Counter(shown)`** and `len(shown)`.

The rotation path is exercised explicitly: a turn-2 "show me something else"
must produce the same `_rotate` output under A0 and A2.

### What is ALLOWED to change, and what is not

Revision 1 stated only what must not move. Both halves are needed, or an
expected difference reads as a defect and a real defect hides behind "expected".

**With λ > 0, these MAY differ between A0 and A2:**

* **recommendation order** — that is the entire point;
* **`_compose`'s top-product sentence.** `dialogue.py:467` reads
  `shown[0]`, so a reordered Top-10 changes which product is named. **This is
  an intended customer-visible consequence, not a defect.**

**With λ > 0, these MUST be identical:**

* **`ask_attribute`** and the whole question decision — it is taken from
  `a0_ranked` at `agent.py:700`, before A2 runs;
* the **question state patch** — `broad_options`, `last_bits`,
  `last_coverage`, `last_weighed`;
* every state key except `shown`, per the table above.

**With λ = 0, or the model absent, or a load failure, or an inference
failure — ALL of these are bit-exact A0:**

* **message** (including the top-product sentence);
* **`ask_attribute`**;
* **recommendations**, in order;
* **existing state and trace fields**.

λ = 0 must be byte-identical and not merely equivalent: it is the fallback's
correctness proof, and an implementation that "usually" matches A0 at λ = 0 is
one that will diverge under a case nobody tested.

## 2. One semantic query, one construction

**One canonical query. No prompt variants, and no alternate may be selected
after `sup-train`.**

Assembled in this fixed order, then stable-deduplicated case-insensitively:

| # | part | source |
|---|---|---|
| 1 | current category | `state["category"]` |
| 2 | use-case evidence | usable slots with `attribute == "use_case"` |
| 3 | other positive constraints | remaining `slot.usable` values (`active and polarity > 0`) |
| 4 | accepted evidence terms | `state["terms"]` not already present |

Within 2 and 3, order by `(source_turn, value)` — deterministic and
independent of dict iteration.

**Excluded, deliberately:**

* **negative values** (`polarity < 0`) — **this MS MARCO relevance model has
  not been validated to enforce hard negative constraints reliably; negative
  constraints remain handled by A0's structured logic and are excluded from the
  semantic query.** (Revision 1 claimed "a cross-encoder has no notion of
  negation", which overreaches: the claim here is about *what has been
  validated for this model*, not about what the architecture can represent.)
* **abandoned / suppressed values** — anything with `soft_ok = False` or in
  `_uncredible(state)`;
* **raw filler turns** — only accepted evidence enters, never message text;
* **profile tags** — Phase 6C1 found no demonstrated target alignment;
* **previous categories after an override** — the pivot's whole point.

**Serialization:** parts joined by `" "`, whitespace-collapsed, casefolded,
**truncated to 200 characters at a word boundary**. The bound exists so the
query cannot crowd the product out of the 256-token budget.

## 3. One product serialization

Canonical, deterministic, in this order:

1. **title**
2. **full category path** — `cat.cats[asin]`, the comma-joined hierarchy.
   The full path, not the leaf: "Clothing, Women, Dresses" carries context a
   bare "Dresses" loses, and the cost is a few tokens.
3. **features**
4. **description / details**

Joined by `". "`, whitespace-collapsed. **Native tokenizer pair encoding**,
`truncation="only_second"` so the **product side truncates first**, max length
**256**.

**Popularity and rating fields are excluded.** They are already A0 features
(`w_pop`, `f_pop`); feeding them to the semantic model too would double-count
one signal and make the fusion weight uninterpretable.

## 4. Robustness gate — product logic, not a hyperparameter

Semantic reranking is **eligible only when all** hold:

| condition | check |
|---|---|
| route is `browsing`, or evidence-sparse `mixed` | `state["route"]` after `_retarget` |
| no active negative slot | no `slot.active and slot.polarity < 0` |
| no abandoned / suppressed category or value | `_uncredible(state)` empty and no `soft_ok = False` |
| no detected override state | `route != "override"` and `state["last_override_turn"] == 0` |
| active hard-slot count ≤ 1 | `sum(1 for s in slots if s.usable and s.hardness == "hard") <= 1` |

**Buying, high-precision, contradiction-sensitive and post-override traffic
stays on A0.**

**This rule is NOT searched.** It is fixed here and may not be modified after
seeing activation counts or any quality number. Activation counts **are**
recorded, by scenario and by official slice, as description.

**Expected interaction, recorded now:** `_retarget` promotes `browsing` →
`buying` as soon as *any* slot is usable (`dialogue.py:73`), and `mixed`
survives only when there is neither a category nor a usable slot. So the route
test already does most of the filtering, and the remaining four conditions are
guards against edge cases rather than the primary filter. **Activation is
expected to be concentrated in early, evidence-sparse `browsing` turns**, and
if it turns out to be broad that is a signal the route classifier is behaving
differently than this paragraph assumes — a thing to investigate, not to tune.

## 5. Conservative frozen fusion

**Semantic-only ordering is NOT the default A2 arm.** Raw cross-encoder logits
are unbounded and their scale varies across runtimes and quantizations, so an
ordering that depends on their magnitude is not portable. **Fusion is
rank-based.**

**Weighted Reciprocal Rank Fusion**, over the prefix only:

```
score(c) = (1 − λ) · 1/(K + rank_A0(c))  +  λ · 1/(K + rank_sem(c))
K = 60          ranks 1-based within the prefix
```

Ties broken by **ascending A0 rank**, so the result is total and stable.
λ = 0 reproduces A0's order exactly; λ = 1 is semantic-only *by rank*, never by
logit magnitude.

**Search, fully specified:**

| | |
|---|---|
| grid | **λ ∈ {0.0, 0.10, 0.25, 0.50, 1.0}** — five trials, fixed |
| corpus | **`sup-train` only** |
| objective | **session-level MRR**, identical semantics to A1's (§6) |
| tie-break | **smaller λ**, then canonical ordering |
| freeze | **λ committed before `sup-val`** |

**λ = 0 is a legitimate outcome and means the semantic signal failed to beat
A0.** It must be reported as such, not treated as a failed run to be retried.

**Nothing else is searched alongside λ** — not route eligibility, query format,
product format, model, `semantic_rerank_k`, or max length. All are frozen
above. Searching two things and reporting one is how a single lucky
combination becomes a "result".

**A2's cache.** Running the model per λ would re-infer identical scores five
times. Instead: run A0 + TinyBERT **once** over `sup-train`, cache per turn the
candidate ids, **A0 ranks**, **semantic ranks** and the target id; hash the
cache; evaluate the five λ over the cache. **Ranks are cached, not logits** —
the fusion consumes ranks, so caching logits would store a quantity no formula
reads. Off-policy for the same reason A1's cache is.

## 6. A1 contract, unchanged and separate

Frozen feature cache, **189 deterministic coordinate trials**, grid relative to
the **original default** weights.

**Cached evaluator semantics** — process turns in order; target rank **1–10** →
record `1/rank` and **stop**; rank **> 10** → **continue**; never in the Top-10
→ **0**; mean over `sup-train` sessions.

The cache is **generated and hashed before the first trial** and stores
**feature vectors, not final scores**, so all nine weights are recomputable
exactly. It is **off-policy training**; full-Agent `sup-val` is the validation.

**A1 and A2 both start from A0 independently.** They are **not combined** in
R1 — A1 weights plus A2 reranking would be a **fourth arm**, and the phase has
three.

## 7. Validation order

1. **Build and freeze A1** on `sup-train`.
2. **Build and freeze A2's λ** on `sup-train`.
3. **Run full-Agent A0, A1, A2-10 on the exact same `sup-val` 200 rows**, same
   frozen configuration, same evaluator version, in the same session.
4. **Any arm failing the supplementary gates stops there.**
5. **Only an arm passing `sup-val` receives its one public confirmation run.**
6. **Public results never cause retuning.**
7. **The sealed holdout stays untouched.**

## 7b. The finalist rule — ONE arm reaches public

Revision 1 never said which arm wins if **both** A1 and A2 pass. Deciding that
after seeing quality numbers would be choosing the winner and then calling it a
rule, so it is fixed here, before any of them exist.

### Step 0 — no-op elimination, BEFORE `sup-val`

An arm that reduces to A0 has not been shown to work; it has been shown to be
absent. **A no-op is not a pass, and must not consume the single public
confirmation.**

| arm | no-op condition | consequence |
|---|---|---|
| **A2-10** | `sup-train` selects **λ = 0** | recorded as **"semantic signal failed"**. A2 **stops immediately**: no `sup-val` run, **not finalist-eligible** |
| **A1** | **`delta_mrr = best_mrr - baseline_mrr <= 0`** on the `sup-train` cache (revision 4, §0.1; revision 3's *"weights exactly identical to the original defaults"* is **superseded** and survives only as the `weights_unchanged` diagnostic) | recorded as a **no-op**. A1 **stops immediately**: no `sup-val` run, **not finalist-eligible** |

Checked **after freezing and before any `sup-val` run**, so a no-op costs
nothing beyond the `sup-train` search that produced it.

λ = 0 is a legitimate `sup-train` outcome (§5) and this is what it *means*
downstream: legitimate to select, and disqualifying to carry forward. The same
holds for an A1 search that returns no MRR gain — the search ran, found nothing
better than the shipped weights, and that is a result, not a candidate. Under
revision 4's tie-break (§0.2) such a search also returns the shipped weights
themselves, so the diagnostic and the verdict agree; when they do not, **the
verdict is `delta_mrr`**.

**If both arms are no-ops:** A0 remains the default, no public confirmation
runs, and Phase 7A ends as a **negative result**.

### Step 1 — `sup-val` elimination

Each arm is judged **independently** against `sup-val`, paired against a fresh
A0 on the same 200 rows:

| floor | requirement |
|---|---|
| composite | **Δ ≥ −0.005** |
| MRR | **Δ ≥ −0.010** |
| A2-10 only | **HR@10 and MTTC exactly invariant** |

An arm failing any of these **stops there** and does not reach public.

### Step 1b — challenger qualification

The gates above are **floors**: they say an arm *did not regress badly*. That
is not evidence of a positive signal, and revision 2 would have let a
neutral-but-harmless arm consume the public confirmation.

To enter finalist selection an arm must **additionally** show:

| | requirement |
|---|---|
| `sup-val` session-level **MRR** | **Δ > 0** — strictly positive |
| `sup-val` **composite** | **Δ ≥ 0** |

An arm meeting the floors but not these is **not a challenger**. It is
recorded as *"no severe regression, no demonstrated positive signal"* — which
is a real and reportable finding, and is not a reason to spend the public run.

**If neither A1 nor A2-10 qualifies:**

* **A0 remains the default;**
* **no public confirmation is run;**
* **Phase 7A ends as a negative result.**

That outcome is pre-registered as acceptable. A phase whose honest answer is
"the challengers did not beat the baseline" has produced a finding, and
spending the one public run to dress it up as a near-miss would spend the thing
the run exists to protect.

### Step 2 — one finalist, by a fixed order

If **both qualify**, the finalist is chosen on **`sup-val` alone**:

1. **primary metric: `sup-val` session-level MRR**, higher wins;
2. **tie-break 1:** `sup-val` composite, higher wins;
3. **tie-break 2:** **fewer moving parts** — A1 (no dependency, no artifact, no
   runtime) beats A2-10 (a pinned model, an ONNX runtime and a vendored
   artifact). A dead-heat on quality should not buy a dependency;
4. **tie-break 3:** canonical arm-name order, `A1 < A2-10`.

### Step 3 — public confirmation, exactly once

**Only the single finalist is run on the public 200, one time.** Not a matrix
over all frozen arms.

The reason is the multiple-comparisons one: confirming *n* arms on the same 200
samples and reporting the best gives the best of *n* draws. **Testing and
selecting among multiple correlated arms increases the family-wise
false-positive risk; the exact factor is not claimed because the arms are
paired and correlated.** Revision 2 said "roughly triples", which asserts an
independence the arms do not have — they share A0's candidate generation, the
same 200 samples and the same session trajectories.

**One finalist, one run, one number.**

The eliminated arm's `sup-val` result is reported as what it is — a
supplementary result — and is never promoted to a public claim.

### Step 4 — when `score_default` may change

`score_default` moves to the finalist **only if every public gate in §9
passes**, in particular **`clean` MRR Δ ≥ +0.010**.

**If no challenger reaches +0.010 on public `clean` MRR, A0 remains the
default.** Not "the best of the two", not "the one that regressed least" —
**A0**. A challenger that cannot clear the bar it was measured against has not
earned the default, and the bar was set before any of them ran.

**Public results never cause retuning.** A finalist that fails on public is
recorded as failing; it is not adjusted and re-run.

## 8. A2 integrated feasibility, remeasured

R0.1 measured synthetic fixtures. After integration, **remeasure on real
`sup-train` queries and product texts**:

| gate | limit |
|---|---|
| semantic component Top-10 p95 | **≤ 25 ms** |
| total turn latency | **reported separately**, not gated |
| additional cold load | ≤ +5 s |
| RSS delta | ≤ +400 MB |
| offline load | required |
| deterministic output | required |
| dependency/model unavailable | **fallback bit-exact with A0** |

**Thread sensitivity at 1, 2, 4 and default cores is recorded as a
diagnostic.** It does **not** change R0.1's verdict or gate. R0.1 measured
15.23 ms on 10 cores at ORT default, and a judging host with fewer cores may
differ — this documents that risk rather than pretending the single-host number
generalises.

## 9. Quality gates

| gate | requirement |
|---|---|
| `clean` MRR | **Δ ≥ +0.010** |
| official slices | **no slice MRR regression** |
| composite | **not below 0.932067** |
| robustness | paired **Δscore ≥ −0.005** |
| robustness | paired **HR@10 drop ≤ 0.01** |
| `sup-val` | composite **Δ ≥ −0.005** and MRR **Δ ≥ −0.010** |

### For A2-10, HR@10 and MTTC must be invariant

Proven in §1 from the evaluator's source: both depend on **set membership**,
which a permutation cannot change. **Any movement in either is an
implementation defect, not a model trade-off**, and is investigated as a bug
rather than reported as a result.

The likely causes, if it happens: `effective_semantic_k` not clamped to
`top_k`; the tail being reordered or truncated; or a non-total tie-break making
the sort unstable.

## 9b. Frozen telemetry — descriptive only

Recorded per turn and aggregated **by scenario and by official slice**:

| field | meaning |
|---|---|
| `a2_eligible_turns` / `a2_total_turns` | how often the robustness gate opened |
| `a2_model_invoked_turns` | turns where the model actually ran — differs from eligible when the prefix is empty or `effective_k < 2` |
| `a2_fallback_count`, **by reason** | `model_absent`, `load_failure`, `inference_failure`, `bad_permutation`, `empty_query`, `prefix_too_short`, `ineligible`. The first four are **experiment-invalidating** (§0.5) and make the shard non-citable |
| `a2_empty_query_count` | turns where the semantic query assembled to `""` |
| `a2_effective_k_distribution` | histogram of `effective_k`, so a silent clamp to 0 or 1 is visible |
| `a2_lambda_zero_degenerate` | whether λ = 0 reproduced A0 **byte-for-byte**, asserted per turn rather than assumed |

**These are DESCRIPTIVE ONLY.** They may not be used to modify the eligibility
gate, the query construction, `semantic_rerank_k`, or λ. That prohibition is
the point of recording them: activation counts are exactly the kind of number
that invites "the gate is a bit tight, let me widen it", and the gate is
product logic frozen in §4.

If the telemetry contradicts the §4 expectation — activation is broad rather
than concentrated in early evidence-sparse `browsing` turns — that is
**recorded as a finding and investigated as a possible defect in the route
classifier**, not resolved by editing the gate.

## 10. Stop conditions

Stop if: the driver runs any split but the operative 800/200, or reports 806/194
anywhere; the cache stores a live state reference rather than a frozen snapshot;
the default replay gate reports a non-zero mismatch count or a non-zero delta
and a trial runs anyway; an A2 shard with a non-zero invalidating-reason count
is quoted as an A2 quality verdict; any frozen parameter is searched alongside
λ; the eligibility rule is changed after seeing activation counts; A1 and A2 are combined; `sup-val` is
run before both arms are frozen; the public 200 is touched by anything but the
single confirmation run; the sealed holdout is touched at all; HR@10 or MTTC
moves for A2-10 and is explained as a trade-off; more than one arm is run on
the public 200; the eligibility gate is modified after seeing activation
counts; `score_default` moves to an arm that did not clear +0.010 on public
`clean` MRR; a no-op arm is carried past Step 0 — judged on `delta_mrr`, not on
weight equality; or an arm that met the floors without qualifying is run on the
public 200.
