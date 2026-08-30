# Model card — the one model this project uses

**`score_default`, the submitted and scored configuration, uses NO model.** It
is Python standard library only: SQLite FTS5 for retrieval, deterministic
feature reranking, **no learned or neural model weights**, no network, no
tokens. (It does have nine hand-configured scalar ranking weights, set by
measurement and frozen; those are configuration, not learned parameters.) If you only want to
reproduce the submitted score, nothing on this page is required and nothing on
it is installed.

Everything below describes **`showcase_semantic`**, the optional A2-10 cascade,
which is **off by default** and has **no public result**.

## 1. What the model is

| | |
|---|---|
| id | `cross-encoder/ms-marco-TinyBERT-L2-v2` |
| revision | `81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc` (pinned; never `main`) |
| base model | `nreimers/BERT-Tiny_L-2_H-128_A-2` |
| training data | MS MARCO Passage Ranking |
| task | **text ranking** — one relevance score per (query, passage) pair |
| license | **Apache-2.0** |
| source | `https://huggingface.co/cross-encoder/ms-marco-TinyBERT-L2-v2` |
| form used here | quantized ONNX, `onnx/model_qint8_arm64.onnx` — a **4.5 MB ONNX file** in a **5.46 MB complete bundle** |
| built for | **arm64**, as the filename states. Measured on **Darwin arm64 only**; no cross-platform performance is claimed |
| runtime | ONNX Runtime CPU + `tokenizers` + `numpy`. **No torch, no transformers.** |
| bundled at | `lab/r0/artifacts/ms-marco-TinyBERT-L2-v2/` |

**It is a transformer cross-encoder reranker, not a generative LLM.** It emits
no text, accepts no instructions, and cannot be prompted. Its entire output is
one scalar per candidate.

## 2. Provenance and integrity

Bundled in this repository under Apache-2.0, with the license text at
`lab/r0/artifacts/ms-marco-TinyBERT-L2-v2/LICENSE`. Every file was fetched at
the **pinned revision SHA in the URL**, and its byte count checked against the
sizes recorded in `lab/r0/artifacts/manifest.json` before Phase 7A-R0 measured
anything.

| file | bytes | sha256 |
|---|---|---|
| `onnx/model_qint8_arm64.onnx` | 4,518,071 | `7497b40504d425ef6482693039690106dca4f1f8d88fb5c4aedd63e73ed6ef68` |
| `tokenizer.json` | 711,396 | `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66` |
| `vocab.txt` | 231,508 | `07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3` |
| `config.json` | 787 | `2144195e107cd7ea61556478e7add12986ebfbc3085f924fc0b90c2410604879` |
| `tokenizer_config.json` | 1,330 | `a5c2e5a7b1a29a0702cd28c08a399b5ecc110c263009d17f7e3b415f25905fd8` |
| `special_tokens_map.json` | 132 | `3c3507f36dff57bce437223db3b3081d1e2b52ec3e56ee55438193ecb2c94dd6` |
| **total** | **5,463,224** | |

Machine-readable: `lab/r0/artifacts/digests.json`.

To re-fetch and re-verify from the pinned revision rather than trusting the
bundle:

```bash
python3 -m lab.r1_artifact
```

That script requests each file at the revision SHA, **refuses any file whose
byte count disagrees with the manifest**, and raises rather than overwriting if
a pinned revision ever serves different bytes. It is the only networked step in
the project, and it is a preparation step: **runtime is offline.**

## 3. How it is used

> **A2-10 is a selective local transformer cross-encoder cascade.** After A0
> retrieval, reranking and request-more rotation, it reorders only a copy of the
> final visible Top-10. It never adds or removes an item, so **HR@10 and MTTC
> are invariant; only MRR can change.** It runs only on eligible early
> Browsing/Mixed turns and falls back **byte-exactly** to A0 on any unavailable
> or invalid model path.

* **Query**: the session's own accepted evidence — category, use-case, other
  positive constraints, accepted terms — deduplicated, casefolded, capped at 200
  characters. No raw message text, no negatives, no profile tags.
* **Document**: title → full category path → features/details → description,
  each exactly once, truncated `only_second` at 256 tokens so the product side
  yields first.
* **Fusion**: weighted reciprocal-rank fusion over the prefix, `K = 60`,
  λ = 1.0. Ranks, never logits — logit magnitude is not portable across
  runtimes and quantizations.

## 4. Measured cost and effect

On the real `sup-train` corpus, seven fresh processes, **on Darwin arm64**
(`notes/45` §4). Every figure below is for that host and architecture; no
cross-platform semantic performance is claimed.

| | |
|---|---|
| Top-10 p95 | **15.95 ms** (cap 25 ms; spread 0.18 ms) |
| thread sensitivity p95 | 1 → 16.29 ms, 2 → 11.83, 4 → 11.60, ORT default → 15.95 |
| additional cold load | **+0.42 s** (cap 5 s) |
| RSS | **+131.6 MB** (cap 400 MB) |
| catalog semantic field store | **18.94 MB** |
| turns invoked | **~8.5–8.9%** (390/4402 `sup-train`, 97/1142 `sup-val`) |
| offline load | verified |
| determinism | one order signature across all runs |
| **`sup-val` MRR** | **+0.008248** |
| **public result** | **none — never run** |

**Every thread setting clears the 25 ms cap on this host**, which retires the
recorded risk that a machine with fewer cores might not -- for Darwin arm64. A
different architecture would need its own measurement, and this quantization
targets arm64.

## 5. Limits, stated plainly

* **No public number exists for A2-10.** It was eliminated at finalist selection
  (`notes/44` §7b Step 2) and the pre-registration allows exactly one public
  confirmation for one finalist. Whether it would have passed is a question the
  protocol forbids asking afterwards.
* **+0.008248 `sup-val` MRR is real and small**, and it is measured on a
  synthetic, generator-grounded corpus. It is not a claim about real users.
* **Negative constraints are excluded from the query on purpose.** This MS MARCO
  relevance model has not been validated to enforce hard negative constraints;
  those stay with A0's structured logic.
* **Eligibility is product logic and was never searched.** Buying,
  high-precision, contradiction-sensitive and post-override traffic stays on A0.

## 6. Running it

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-semantic.txt
./.venv/bin/python -m demo --profile showcase_semantic
```

Nothing above is needed for `score_default`:

```bash
python3 -m evaluator.local_evaluator --catalog data/catalog.jsonl --dataset data/public_set.jsonl
```
