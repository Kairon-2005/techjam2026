# Phase 7A-R1 pre-registration — quality evaluation of A0, A1 and A2-10

**Revision 2. Design and pre-registration only. Nothing is implemented.** No
ONNX in the Agent, no A1 cache, no λ trials, no `sup-val`, no public 200, no
sealed holdout.

Revision 2 closes seven gaps found in review of `a492f05`. Two were wrong
rather than merely absent:

1. **The call site was not pinned.** A2's position relative to `_rotate` was
   undefined, and running before it would have let the semantic order feed the
   rotation.
2. **"Every state key identical" was FALSE.** `state["shown"]` records the
   order candidates were displayed in, so A2 necessarily changes it. The
   correct invariant is membership, not bytes.
3. **Output equality was asserted on sets**, which cannot see a duplicate or a
   dropped item.
4. **What is ALLOWED to change** was never stated, only what must not.
5. **No finalist rule** — the document did not say which arm wins if A1 and A2
   both pass.
6. **The negation claim overreached.**
7. **Telemetry was unfrozen.**

Both feasibility conclusions stand and neither is edited:

| | |
|---|---|
| **A2-30** | **FAIL** under the frozen 25 ms cap — 69.68 ms, `notes/41` |
| **A2-10** | **PASS** every frozen cap, 7/7 — 15.23 ms, `notes/43` |

**Three arms, fixed: A0, A1, A2-10.** A2-30 never enters quality evaluation.

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

### Step 1 — `sup-val` elimination

Each arm is judged **independently** against `sup-val`, paired against a fresh
A0 on the same 200 rows:

| gate | requirement |
|---|---|
| composite | **Δ ≥ −0.005** |
| MRR | **Δ ≥ −0.010** |
| A2-10 only | **HR@10 and MTTC exactly invariant** |

An arm failing any of these **stops there** and does not reach public.

### Step 2 — one finalist, by a fixed order

If **both** survive, the finalist is chosen on **`sup-val` alone**:

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
samples and reporting the best gives the best of *n* draws, and with the
per-slice gates each arm faces, testing three arms roughly triples the chance
that one clears them by luck. **One finalist, one run, one number.**

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
| `a2_fallback_count`, **by reason** | `model_absent`, `load_failure`, `inference_failure`, `empty_query`, `prefix_too_short`, `ineligible` |
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

Stop if: any frozen parameter is searched alongside λ; the eligibility rule is
changed after seeing activation counts; A1 and A2 are combined; `sup-val` is
run before both arms are frozen; the public 200 is touched by anything but the
single confirmation run; the sealed holdout is touched at all; HR@10 or MTTC
moves for A2-10 and is explained as a trade-off; more than one arm is run on
the public 200; the eligibility gate is modified after seeing activation
counts; or `score_default` moves to an arm that did not clear +0.010 on public
`clean` MRR.
