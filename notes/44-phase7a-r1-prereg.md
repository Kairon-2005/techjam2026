# Phase 7A-R1 pre-registration — quality evaluation of A0, A1 and A2-10

**Design and pre-registration only. Nothing is implemented.** No ONNX in the
Agent, no A1 cache, no λ trials, no `sup-val`, no public 200, no sealed
holdout.

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

1. the **same ten ASINs** before and after A2, as a set;
2. the **HR@10 opportunity set** is unchanged;
3. **`semantic_rerank_k = 0` is byte-equivalent to A0** — message,
   `ask_attribute`, recommendations;
4. **`top_k ∈ {1, 5, 10, 20}`** each preserve the A0 returned set;
5. the **A0 tail order is unchanged**, element-wise;
6. **question policy, retrieval, the profile window and state mutations are
   unaffected** — the Phase 6B2-R2 and 6C1 evidence must survive intact, so
   `pool_depth`, `candidates`, `starved_candidates` and every `state` key are
   asserted identical between A0 and A2 on the same turn;
7. **model absent → byte-equivalent A0**, exercised by pointing the loader at a
   missing artifact.

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

* **negative values** (`polarity < 0`) — a cross-encoder has no notion of
  negation, so "not leather" would score *toward* leather;
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

## 10. Stop conditions

Stop if: any frozen parameter is searched alongside λ; the eligibility rule is
changed after seeing activation counts; A1 and A2 are combined; `sup-val` is
run before both arms are frozen; the public 200 is touched by anything but the
single confirmation run; the sealed holdout is touched at all; or HR@10 or MTTC
moves for A2-10 and is explained as a trade-off.
