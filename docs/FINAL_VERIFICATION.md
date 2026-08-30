# Final verification — clean checkout

Produced by `python3 -m lab.verify_clean`, which creates a **detached worktree
at the verified commit**, links in only `data/catalog.jsonl`, and runs
everything below in a child interpreter whose import path is that tree. The
developer's `.venv`, the cross-encoder artifact and the A1 feature cache are
**not** linked in — their absence is part of what is verified.

| | |
|---|---|
| commit | `96efae3f6ed0da9e0a7c95236fae17295d8ef7d6` |
| origin tree clean at verification | PASS |
| Python | **3.14.6** |
| platform | darwin / arm64 |
| overall | PASS |

Only the Python version above was exercised. No claim is made about any other.

## 1. The tree really is clean

| path | absent |
|---|---|
| `.venv` | PASS |
| `lab/a1cache.jsonl` | PASS |
| `lab/a2cache.jsonl` | PASS |

The bundled Apache-2.0 showcase artifact IS present, and every file matches the
sha256 recorded in `lab/r0/artifacts/digests.json` when it was fetched at its
pinned revision:

| file | |
|---|---|
| `LICENSE` | PASS |
| `config.json` | PASS |
| `onnx/model_qint8_arm64.onnx` | PASS |
| `special_tokens_map.json` | PASS |
| `tokenizer.json` | PASS |
| `tokenizer_config.json` | PASS |
| `vocab.txt` | PASS |

**`score_default` never reads it.** `semantic_model_dir` resolves to the empty
string (PASS) and `semantic_rerank_mode` to `off`
(PASS), so the scored path cannot reach the artifact even if it is there.

## 2. The official evaluator reproduces the frozen numbers

| metric | expected | measured | |
|---|---|---|---|
| TechnicalScore | 0.932067 | **0.932067** | PASS |
| HR@10 | 0.995 | **0.995** | PASS |
| MRR | 0.852556 | **0.852556** | PASS |
| MTTC | 2.06 | **2.06** | PASS |
| sessions | 200 | 200 | PASS |

Reported token usage: `{'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}` — the agent calls no model
and spends no tokens.

## 3. Response contract

| check | result |
|---|---|
| responses checked | 200 |
| recommendations checked | 2000 |
| `message` is a string, `ask_attribute` is `str` or `None` | PASS |
| every `parent_asin` is in the catalog | PASS |
| no duplicate inside a Top-10 | PASS |
| at most 10 recommendations | PASS |

No problems found.

## 4. Cost

| | |
|---|---|
| evaluator catalog index | 0.286 s |
| agent construction (FTS5 index build) | 5.704 s |
| cold start (process start → first response) | **6.134 s** |
| warm turn p50 | **13.401 ms** |
| warm turn p95 | **14.176 ms** |
| full 200-session evaluation | **15.59 s** |
| peak RSS | **575.7 MB** |
| network calls | **0** |
| API cost | **0** |
| tokens | **0** |

## 5. Standard library only

Third-party packages loaded during the whole run: **none**
— PASS

`numpy`, `onnxruntime`, `tokenizers` and `torch` are not installed in the
interpreter this ran under, so the scored path could not have used them even by
accident. The semantic showcase imports them **inside `Scorer.load`**, which
`score_default` never reaches.

## 6. Test suite, in the clean worktree

```
Ran 813 tests in 43.854s
OK
```
