# Conversational Shopping Copilot — TechJam Track 4

A multi-turn shopping agent that aims to place a simulated shopper's hidden
target product in the Top-10 within at most 10 turns. On the official public
simulator it did so in **199 of 200 sessions**, by **tracking typed evidence
across turns**, **asking a question that splits the candidate pool**, and
**letting runtime context decide how deep to retrieve**.

**`score_default` -- the submitted, scored configuration -- runs on the Python
standard library alone. No LLM, no API key, no network, no GPU, no learned or
neural model weights, zero tokens, zero cost.** The project also ships optional
capabilities that do use a model; they are off in `score_default` and are
described as such throughout.

| | |
|---|---|
| **TechnicalScore** | **0.932067** |
| HR@10 | 0.995 |
| MRR | 0.852556 |
| MTTC | 2.06 turns |
| cold start | 11.62 s |
| warm turn | 25.571 ms p50 · 30.445 ms p95 |
| full 200-session evaluation | 30.79 s |
| peak RSS | 710.3 MB |
| tokens / network calls / API cost | **0 / 0 / 0** |

This one performance set comes from the detached clean-checkout verification of
commit `6b3cd8b77bcc26f7aec7ef9598d38d3acc90d564` on Python 3.14.6 / Darwin arm64
— see [`docs/FINAL_VERIFICATION.md`](docs/FINAL_VERIFICATION.md). The separate
[Linux portability run](https://github.com/Kairon-2005/techjam2026/actions/runs/33294760426)
succeeded on Ubuntu with Python 3.10 and 3.11 and reproduced TechnicalScore
0.932067 exactly on both.

**Demo video:** _(link placeholder)_

## The short version

The measurable advantages are a dual-route intent pipeline that retargets as a
session becomes specific, starvation-aware widening, pool-aware clarification,
and explicit retrieval and question controllers. They run locally on CPU at
zero API cost. Every experiment is fingerprinted and written to append-only
ledgers. Every quoted result must pass one citability predicate; invalid or
otherwise non-citable rows are retained and identified, not erased. That same
evidence discipline keeps dense retrieval, TinyBERT reranking and profile
ranking out of `score_default` after their gates failed or showed no target
alignment.

Three of our four biggest engineering efforts are shipped **off**, each with the
measurement that says why. That is the point, not an apology: the same
discipline that turned them off is what makes 0.932067 trustworthy.

## Setup

Every command below is run from a fresh clone and nothing else.

```bash
git clone https://github.com/Kairon-2005/techjam2026.git
cd techjam2026
```

The catalog is organizer data and is not in the repository. Download it from the
**organizer's official release** (we publish no catalog asset of our own):

```bash
curl -L -o catalog.jsonl.gz https://github.com/TechJam2026/techjam-conversational-search/releases/download/participant-kit/catalog.jsonl.gz
```

Verify it before decompressing, then verify the decompressed file:

```bash
echo "07fd142631fd6b03e2b4d09988c3eb7d53720e9d57010c79db48eeaada50a8f8  catalog.jsonl.gz" | shasum -a 256 -c -
gzip -dk catalog.jsonl.gz && mv catalog.jsonl data/catalog.jsonl
echo "da979b05a68af864cb0dcf9ee6a81c010c7e66a57978ad286c7a2e005fc69a67  data/catalog.jsonl" | shasum -a 256 -c -
```

(On Linux, `sha256sum -c -` in place of `shasum -a 256 -c -`.)

```bash
pip install -r requirements.txt          # a no-op: there are no dependencies
```

### One command reproduces the score

```bash
python3 -m evaluator.local_evaluator --catalog data/catalog.jsonl --dataset data/public_set.jsonl
```

Expected, exactly: `recommended_technical_score` **0.932067**,
`hit_rate_at_10` 0.995, `mrr` 0.852556, `mttc` 2.06 over 200 sessions.

### The submission entry point

```python
from starter.agent import Agent      # entry point: starter.agent:Agent
agent = Agent("data/catalog.jsonl")  # no config argument == score_default
```

`starter.agent:Agent` implements `reset(session_id, user_profile)` and
`respond(session_id, user_message, turn, top_k)` exactly as the contract in
[`docs/agent_api_contract.json`](docs/agent_api_contract.json) specifies.

### One command shows the system working

```bash
python3 -m demo
```

Four public sessions replayed by the official customer simulator, showing route,
retrieval decision, typed evidence, why each question was asked, Top-3 and
latency per turn. See [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

## Where the score came from

| stage | TechnicalScore | what changed |
|---|---|---|
| weak BM25 starter | 0.10671 | provided baseline |
| + ask `other`, keep evidence on override | 0.7536 | **7.1×** — override-preceding hits score nothing, so the two interact super-additively |
| + feature reranking | 0.9280 | on the public development set, recall@200 was already 1.000, so **the bottleneck there was ranking, not retrieval** |
| + routing, deep funnel, context controllers | **0.932067** | shipped |

Popularity is the single strongest feature (+0.114): target products sit at the
**99.5th percentile of `rating_number`**, because sessions are sampled from
Amazon 5-core. We report that as a **property of this evaluation**, and §
[Limitations](#limitations) explains why we do not present it as a general
insight.

## The four pillars

| pillar | enabled in `score_default` | implemented and evaluated, **disabled by default** |
|---|---|---|
| **I · Intent routing & hybrid pipeline** | Buying/Browsing/Mixed/Override routing, dynamic Browsing→Buying retarget, BM25 over FTS5, category/facet signals, deterministic funnel, feature reranking | dense candidate source + RRF fusion (`showcase_dense`); TinyBERT ONNX semantic reranking (`showcase_semantic`) — **architecture and demo evidence, not part of `score_default`** |
| **II · Multi-turn scenario evolution** | typed `SlotValue` state, negation, contradiction safety, abandonment/suppression, request-more rotation, over-generality detection, pool-aware questioning, starvation-aware widening | targeted override erasure (`on_override='slot'`) |
| **III · Dynamic context programming** | **bounded context snapshots** (`PreRetrievalSnapshot`, `ContextSnapshot`) with the **retrieval controller and the question controller both enabled** (`retrieval_context_mode="control"`, `question_context_mode="control"`) | profile normalization and credibility classification — implemented and evaluated, then **disabled by default** (`profile_context_mode="off"`, `w_profile=0.0`) because the official profiles showed **no target-discriminating signal** |
| **IV · Evaluation matrix** | leases, append-only ledgers, one citability predicate, pre-registration, negative results kept | — |

**The semantic-ranking component of Pillar I is satisfied by the optional offline
ONNX profile** (`showcase_semantic`): a pinned, locally bundled TinyBERT
cross-encoder that reranks the visible Top-10 with no network access. It is
implemented, measured and demonstrable, and it is **off in `score_default`** —
see [Three configurations](#three-configurations-kept-apart-on-purpose).

### Runtime flow, per turn

```
customer message
      │
      ├─ parse → typed SlotValue evidence   (polarity, hardness, confidence, source turn)
      ├─ classify reply                     (informative / uncertain / refusal / request-more / override)
      ├─ route                              buying │ browsing │ mixed │ override
      │
      ├─ PreRetrievalSnapshot → retrieval controller
      │       decides depth and mode        standard │ widened │ deep-funnel   ← Pillar III, ON
      │
      ├─ candidate generation               BM25 over FTS5  (+ dense + RRF — OFF)
      ├─ deterministic funnel               safe positive narrowing + rescue lane
      │                                     negatives remain scored penalties
      ├─ feature rerank                     9 weighted signals            ← the A0 core
      ├─ rotate                             pinned head, refreshed tail on "show me more"
      │                                     (+ semantic Top-10 permutation — OFF)
      │
      ├─ ContextSnapshot → question controller
      │       decides whether to ask, what, and open vs structured  ← Pillar III, ON
      │
      └─ message + ask_attribute + Top-10
```

## Three configurations, kept apart on purpose

| profile | what it is | claims |
|---|---|---|
| **`score_default`** | the submitted, scored configuration | **0.932067 on the public 200** |
| `showcase_semantic` | A2-10 cross-encoder cascade, λ=1.0 | architecture demonstration + supplementary evidence. **No public number.** |
| `showcase_dense` | dense/RRF Browsing candidate source | architecture demonstration. **No public number.** |

Profile normalization and credibility classification are also optional
components: `score_default` keeps `profile_context_mode="off"` and
`w_profile=0.0`. None of the dense, TinyBERT or profile components contributes
to the submitted score.

**There is no combined configuration**, and a test enforces it: no
dense+semantic+personalization profile exists, because none was ever measured
and a named label over an unmeasured combination is a claim about it.

```bash
./.venv/bin/pip install -r requirements-semantic.txt   # optional, showcase only
python3 -m demo --extra semantic
```

`score_default` never reads the model artifact, never imports `numpy`,
`onnxruntime` or `tokenizers`, and does not require the artifact to exist.
Verified: third-party packages loaded during a full evaluation — **none**.

**The narrow, verified fallback claim:** when `showcase_semantic` is enabled and
the model is absent, fails to load, fails inference, or returns a
non-permutation, **the semantic reranker returns A0's ordering byte-exactly**,
with a distinct reason code per case. That is a property of the optional semantic
reranker, and it is not a claim that every component of the system degrades
gracefully under every failure.

## Negative results

Kept because they are the evidence, not despite it.

| we tried | result | shipped? |
|---|---|---|
| **A1** — refit the 9 ranking weights on supplementary data | sup-val MRR **+0.229**, public MRR **−0.116**, every slice and every robustness scenario regressed | **no** |
| **A2-10** — TinyBERT Top-10 cross-encoder rerank | feasible (15.95 ms p95 on Darwin arm64), HR@10/MTTC provably invariant, sup-val MRR **+0.008**, **no public number** | **no** |
| **A2-30** — the same at Top-30 | 69.68 ms against a frozen 25 ms cap | **no** |
| **dense retrieval + RRF** | Browsing MRR up, Boundary MRR 1.000 → 0.870, fails the contradiction guard | **no** |
| **profile ranking weight** | no demonstrated target alignment (Phase 6C1) | **no**, weight pinned at 0.0 |
| **slot erasure on override** | keep 0.9233 > slot 0.9140 > erase 0.8458 over 5 seeds | **no**, `keep` ships |
| **route-conditioned weights** | +0.0013 was 2 sessions better / 7 worse; a noise spike | **no** |
| `w_card` (reverse-engineering the simulator) | +0.0033, and brittle to any rewording | **no**, off by design |

**The A1 result is the most interesting thing we measured**, and it is a
failure:

> A1 demonstrated strong within-generator generalization but failed
> cross-distribution transfer. The supplementary corpus is
> catalog-metadata-grounded and rewards category, exact-match and IDF signals;
> the public Amazon 5-core sessions carry a much stronger popularity prior. A1
> reduced `w_pop` from 4.0 to 1.0 and increased lexical/category weights,
> improving supplementary MRR while reducing public MRR. Popularity is the
> strongest measured explanation, but not claimed as the sole factor because no
> post-public ablation was performed.

`sup-train` and `sup-val` are the **same distribution**, so `sup-val` proves
within-generator generalization and nothing more. The full-Agent run improved
too, so it is not an artefact of off-policy caching. **The supplementary gain is
not a real-user effect**, and we do not present it as one.

## How the numbers were kept honest

Every experiment ran under an **exclusive lease** in an isolated git worktree,
with the agent source, evaluator, catalog, datasets and search inputs
fingerprinted before and after. Results go to **append-only ledgers** and are
**never rewritten** — a wrong row is superseded by an invalidation record, not
edited. **One citability predicate** decides what any report may quote: a dirty
tree, a broken lease, an incomplete matrix, a missing input hash or a semantic
fallback makes a row uncitable.

Phase 7 was **pre-registered before it ran** (`notes/44`): the split, the
objective, the grid, the gates, the finalist rule and the stop conditions.
Two corrections were committed **before the first trial**, both found by the
machinery rather than by inspection — including one where the cached objective
disagreed with the official evaluator on 35 of 120 override sessions.

The public 200 is a **confirmation set**: one finalist, one run, one number,
never re-run to retune. A1 failed all five public gates and `score_default`
stayed A0.

## Limitations

* **`w_pop = 4.0` is partly an artefact of this evaluation.** Public targets sit
  at the 99.5th popularity percentile because sessions come from Amazon 5-core.
  A1 measured the size of that dependence by removing it — and lost 0.116 MRR.
  On a corpus without that sampling bias, the shipped weights are probably wrong.
* **The supplementary corpus is synthetic**, generator-grounded, and produced by
  us. Numbers on it describe that generator.
* **No cross-session memory.** The evaluation API provides no stable user
  identity, so none is invented. The long-term profile is treated as one
  external prior, judged against current-session evidence, and given zero
  ranking weight.
* **Typed hardness is not an enforcement guarantee.** An active, positive,
  high-confidence requirement backed by a sufficiently covered catalog facet
  may narrow the primary Buying pool. The funnel relaxes those filters before
  starvation and always carries a bounded rescue lane of excluded candidates.
  Explicit negative exclusions never filter in `score_default`; they stay out
  of the query and penalise matching candidates in the ranker. This is scored
  safe relaxation, not strict hard-constraint enforcement.
* **A2-10 has no public number** and cannot be given one — the pre-registration
  allows exactly one public confirmation, and A1 spent it.
* **Portability is reported separately for the two paths** — full table in
  [`docs/PORTABILITY.md`](docs/PORTABILITY.md).
  * `score_default`: **verified on Python 3.14.6 and 3.13.7, Darwin arm64**
    (0.932067 exactly on both, zero third-party imports). **Minimum supported
    version is Python 3.10**, established by a concrete failure on 3.9.6 rather
    than by inspection. **Linux Python 3.10 and 3.11 are verified** by successful
    [GitHub Actions run 33294760426](https://github.com/Kairon-2005/techjam2026/actions/runs/33294760426):
    the full suite, exact 0.932067 evaluator check and zero-third-party-import
    check passed in both jobs.
  * `showcase_semantic`: measured on **Darwin arm64 only**, using an
    `arm64`-quantized ONNX file. **No cross-platform semantic performance is
    claimed**; the artifact's own name says which architecture it targets.
* **Evaluated on English-language sessions only**, in one catalog category
  (`Clothing_Shoes_and_Jewelry`), over 50,000 products. Nothing here shows the
  approach transfers to other languages or verticals; it was not tested.

## Repository map

```text
starter/            the agent: agent, retrieval, dialogue, context, evidence, catalog, semantic
evaluator/          the official local evaluator — byte-for-byte unmodified
demo/               python3 -m demo
lab/                experiment harness: leases, ledgers, provenance, scenarios, benchmarks
notes/              46 numbered design, pre-registration and results documents
docs/               verification, model card, architecture, limitations, submission checklist
tests/              828 tests, all executed on a committed tree, including
                    exact score locks and the configuration lock
```

## Data attribution

Derived from **Amazon Reviews 2023** (McAuley Lab, UCSD),
`Clothing_Shoes_and_Jewelry`, joined on `parent_asin`. See
[`DATA_ATTRIBUTION.md`](DATA_ATTRIBUTION.md). No images, no credentials, no
private organizer labels.

The optional semantic showcase bundles
`cross-encoder/ms-marco-TinyBERT-L2-v2` @ `81d1926f…` under **Apache-2.0** — a
**4.5 MB ONNX file** inside a **5.46 MB complete bundle** — with its license,
revision and per-file SHA-256 recorded. See
[`docs/MODEL_CARD.md`](docs/MODEL_CARD.md).

## License

The project code and documentation are released under the root [MIT
License](LICENSE), copyright 2026 Kairon. The bundled TinyBERT model artifact is
third-party material and remains under its own **Apache-2.0** license at
`lab/r0/artifacts/ms-marco-TinyBERT-L2-v2/LICENSE`; the project MIT license does
not replace or modify that license.

## Team

See [`docs/TEAM_CONTRIBUTIONS.md`](docs/TEAM_CONTRIBUTIONS.md).
