# Phase 7A-R1 results — the A1 cache, the A1 freeze, and where A2 stops

**Status: A1 frozen. A2 blocked on an absent environment. `sup-val` not run,
public 200 not touched, sealed holdout not touched.**

Every number here comes from a citable row in `lab/a1builds.jsonl` or
`lab/r1builds.jsonl` — a valid lease, an isolated worktree, and every input
fingerprint present (`lab/provenance.py`).

## 1. The split, asserted rather than described

| | train | val |
|---|---|---|
| `buying` | 320 | 80 |
| `browsing` | 320 | 80 |
| `intent_override` | 120 | 30 |
| `boundary` | 40 | 10 |
| **total** | **800** | **200** |

`sup-train` `48d14de25a4adf90adbcd9ad621ea2e1d143bd5632a8be67fed239ff4822290d`,
`sup-val` `82e0470ee83d2cf8883399ededda11b5ddb4fa762685196b36a9fe521a105a73`.

The superseded 806/194 global-hash split is refused by count alone, and a
negative test rebuilds it — accidental strata included, `boundary` 38/12 — and
requires the guard to reject it.

## 2. The A1 feature cache

| | |
|---|---|
| cache sha256 | `41bd811310e5364abc83d7a6b531164f79726dd331e177c76e73824a5ed6f553` |
| sessions / turns | **800 / 4402** |
| ledger row | `e8de9a88edd37c0d`, commit `842da7d`, lease valid |

**The default replay gate, before trial 0:**

| | |
|---|---|
| full-order mismatches | **0** |
| cached default MRR | **0.20282688492063491** |
| live A0 MRR | **0.20282688492063491** |
| delta | **0.0 exactly** |
| evaluator MRR | **0.20282688492063491** |
| evaluator delta | **0.0 exactly** |
| rotated turns | 0 |

Three agreements, not one: the cache re-derives A0's full candidate order from
its stored features, re-running `_rerank` on each frozen snapshot returns the
same order, and the cached objective equals the evaluator's own MRR over the
same 800 sessions.

**The hash reproduces.** Three independent leased builds at commits `27f3730`,
`483d514` and `842da7d` all produced `41bd8113…`, so the cache is a function of
its inputs and not of the run.

### What the gate caught, and what that cost

The first build reported cached MRR 0.199835 against the evaluator's 0.202827.
`notes/44` revision 4 §0.4 had predicted any such gap would be `_rotate`. **It
was not — `_rotate` fired on 0 turns.** The whole difference was the evaluator's
`override_applied` rule, which the cached objective did not implement: on an
`intent_override` session a Top-10 hit **before** the override turn scores
nothing. It was wrong in both directions — it credited hits the evaluator
discards, and because a credited hit also stops the session it never reached the
later hit the evaluator scores — and it disagreed on **35 of 120** `sup-train`
override sessions. Corrected in revision 5 §0.8, before any trial.

## 3. A1: 189 trials, frozen

| | |
|---|---|
| trials | **189** = 3 sweeps × 9 weights × 7 grid points, asserted |
| baseline MRR | 0.20282688492063491 |
| best MRR | **0.42251984126984127** |
| `delta_mrr` | **+0.21969295634920635** |
| L1 from defaults | 25.1 |
| Step 0 | **not a no-op — finalist-eligible** |

| weight | default | frozen |
|---|---|---|
| `w_bm25` | 0.3 | **2.4** |
| `w_cat` | 1.0 | **8.0** |
| `w_exact` | 1.5 | **3.0** |
| `w_field` | 2.0 | **0.0** |
| `w_idf` | 0.25 | **2.0** |
| `w_neg` | 2.0 | 2.0 |
| `w_phrase` | 5.0 | **1.25** |
| `w_pop` | 4.0 | **1.0** |
| `slot_soft` | 4.0 | **0.0** |

**Deterministic.** Two leased runs at different commits returned the identical
vector, the identical MRR and the identical 16 accepted moves.

**Where the gain lives** — descriptive, computed after freezing:

| scenario | default | frozen | Δ | n |
|---|---|---|---|---|
| `boundary` | 0.115446 | 0.301488 | +0.186 | 40 |
| `browsing` | 0.199479 | 0.403873 | +0.204 | 320 |
| `buying` | 0.225548 | 0.425094 | +0.200 | 320 |
| `intent_override` | 0.180291 | 0.505724 | +0.325 | 120 |

Broad, not one slice carrying the rest.

### Read the size of this with the caution it needs

Doubling MRR by reweighting nine existing features is not a modelling
breakthrough. **The shipped weights were fitted on the PUBLIC set in phases 1–3
and have never been fitted to the supplementary distribution**, where A0 scores
0.2028 against 0.8526 on public. A large gain is what fitting a new distribution
looks like. Two specific cautions, recorded now rather than after `sup-val`:

* **`w_pop` 4.0 → 1.0 runs against the public set's strongest single feature.**
  `NOTES.md` records `w_pop = 0` costing −0.061 there. Public targets sit at the
  99.5th popularity percentile because sessions were sampled from Amazon 5-core;
  if the supplementary corpus lacks that bias, the shipped `w_pop` is partly a
  public-set artefact — **and these weights may be worse on public.**
* **The cache is off-policy by construction** (`notes/40` §6). It holds the turns
  A0 ran, so a trial reranks A0's candidate lists along A0's trajectories, while
  different weights would change which questions get asked. **Full-Agent
  `sup-val` is the validation and this number is not it.**

`notes/44` §9 gates on public `clean` MRR Δ ≥ +0.010 with no slice regression,
and §7b Step 4 keeps A0 as the default if that bar is not cleared. **Nothing
here moves `score_default`.**

## 4. A2 integrated feasibility — half measured, half blocked

**Measured**, on the real 60 MB catalog, three fresh interpreters per stage
(row `c9098c3a44ba66d7`):

| gate | limit | measured | |
|---|---|---|---|
| additional cold load | ≤ +5 s | **+0.42 s** | **PASS** (run spread 0.14 s) |
| RSS delta | ≤ +400 MB | **+32.4 MB** | **PASS** |
| field store | — | **18.94 MB** | — |

**Measured**, on the real `sup-train` queries and product texts — 800 sessions,
4402 turns, written to `lab/r1_texts.jsonl`:

| | |
|---|---|
| turns that would invoke the model | **390 of 4402 (8.9%)** |
| reasons | `ineligible` 4012, `reranked` 390 |
| `effective_k` | **10 on every turn**, never clamped to 0 or 1 |
| query chars | p50 43, p95 77, max 112 |
| **queries at the 200-char cap** | **0** |
| product text chars | p50 967, p95 2291, max 4960, none empty |

Two things this settles. **Activation is concentrated**, which is what `notes/44`
§4 predicted — had it been broad, §9b required investigating the route
classifier rather than editing the gate. And **the 200-char query cap never
binds on real text**, so the query cannot crowd the product out of the 256-token
window; the product side truncates first, as `only_second` intends.

### Blocked, and why it is not reported as a pass

**`onnxruntime`, `tokenizers`, `numpy` and the pinned TinyBERT artifact are all
absent from this environment.** `lab/r0/artifacts/` holds only a manifest; the
`.venv` R0 and R0.1 ran in no longer exists.

| gate | status |
|---|---|
| semantic component Top-10 p95 ≤ 25 ms | **not measured** |
| offline load | **not measured** |
| deterministic output | **not measured** |

Under `notes/44` §0.5 a `model_absent` turn is **experiment-invalidating**:
running the A2 arm now would fall back to A0 on every turn and report A0's
quality under A2's name. `lab/provenance.py` refuses such a row, so the result
would not merely be wrong — it would be uncitable. **A2 is therefore not frozen,
and no λ has been searched.**

Restoring it needs, at the versions R0.1 pinned:

```
onnxruntime==1.24.1   numpy==2.5.2   tokenizers==0.22.2
cross-encoder/ms-marco-TinyBERT-L2-v2 @ 81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc
  onnx/model_qint8_arm64.onnx (4,518,071 bytes) + tokenizer.json + vocab.txt
```

Both are network fetches, and neither has been made.

## 5. What has not happened

* **`sup-val` has not run.** `notes/44` §7 requires **both** arms frozen first,
  and A2 is not frozen.
* **The public 200 has not been touched.** It runs once, for one finalist,
  after `sup-val`.
* **The sealed holdout has not been touched at all**, including by the split
  guard, which tests corpus membership by namespace rather than by opening the
  file.
