# Devpost draft

Organized by the judging weights. Submitted quality and cost figures are
reproducible from the authoritative clean-checkout report; experiment figures
are traceable to citable ledger rows.

---

## Inspiration

A shopping assistant that asks a good question is worth more than one that
returns a longer list. The weak starter scores **0.107** — its target is often
already in a deep candidate pool, but it does not accumulate enough evidence to
rank that target early or ask a discriminating question.
On the public development set, recall@200 turned out to be **1.000** from the
very beginning: the target was already in the pool. **On that corpus the problem
was never retrieval. It was knowing what to keep, what to ask, and how deep to
look.**

## What it does

A multi-turn shopping copilot that aims to place a simulated shopper's hidden
target in the Top-10 within 10 turns. On the official public simulator it did so
for **199 of 200 sessions**, by tracking **typed evidence** across turns, asking
a question that **splits the live candidate pool**, and letting **runtime context
decide how deep to retrieve**.

| | |
|---|---|
| **TechnicalScore** | **0.932067** (weak baseline 0.10671 — **8.7×**) |
| HR@10 · MRR · MTTC | 0.995 · 0.852556 · 2.06 turns |
| cold start · warm turn | 11.526 s · 24.366 ms p50 / 26.908 ms p95 |
| **tokens · network · API cost** | **0 · 0 · $0** |

All headline quality, latency and memory figures below are one consistent set:
the detached clean-checkout verification of commit
`443f6039657278a4afe45a2a50ed2dadba7637d0` on Python 3.14.6 / Darwin arm64.
The separate [GitHub Actions portability run
33294760426](https://github.com/Kairon-2005/techjam2026/actions/runs/33294760426)
succeeded on Ubuntu with Python 3.10 and 3.11 and reproduced TechnicalScore
0.932067 exactly in both jobs.

### Why this is more than a score

The measurable product advantage is the combination of **dual-route intent
handling**, **starvation-aware widening**, **pool-aware clarification**, and
explicit context controllers that explain each retrieval and question decision.
The engineering advantage is equally concrete: evaluation inputs are
fingerprinted, results are append-only and citable, and costly-looking ideas are
allowed to stay off. Dense/RRF retrieval, TinyBERT reranking and profile ranking
are optional measured components; none contributes to `score_default`, which
runs locally on CPU with zero API cost.

---

## 35% — Technical Execution

**`score_default`: six modules, ~4,800 lines, Python standard library only.**
SQLite FTS5 for retrieval and deterministic feature reranking, with **no learned
or neural model weights** on the scored path -- the nine ranking weights are
hand-configured scalars, set by measurement and frozen. **828 tests, all executed** on a
committed tree, including exact end-to-end score locks and a configuration lock
that asserts every shipped default by name and value.

### Runtime Context Programming (the core idea)

Two **bounded, distilled, immutable snapshots** are computed from live session
state each turn and handed to controllers that decide *how the pipeline runs*:

* **`PreRetrievalSnapshot` → retrieval controller** — candidate depth and
  retrieval mode, with a reason code (`DEPTH_STANDARD`, `WIDEN_REQUEST_MORE`, …).
* **`ContextSnapshot` → question controller** — whether to ask, which attribute,
  and open prompt vs structured choice.

Both are **default-on**. Both were adopted only after a **shadow phase** proved
the extracted controller agreed with the legacy path on every turn — reported as
*counts* of disagreeing turns, never as a rate, because "0.9997 agreement" reads
as a pass while naming three broken turns.

### Typed state evolution

`SlotValue(attribute, value, polarity, hardness, confidence, source_turn,
provenance, active, soft_ok, catalog_support, contradiction)`. Negation is a
first-class polarity, not a keyword. Contradiction and abandonment set
`soft_ok=False` — evidence stops being usable **without being deleted**, which is
what makes override handling recoverable.

`hardness="hard"` is typed evidence, not a promise of strict enforcement. Only
active positive, near-certain, uncontested, catalog-supported requirements on a
sufficiently covered facet may narrow the primary Buying pool; the funnel
relaxes before starvation and carries a bounded rescue lane. Explicit negative
exclusions never filter in `score_default`: they stay out of the query and apply
a confidence-scaled ranking penalty.

### Starvation-aware orchestration

When the surviving pool collapses or the customer asks for alternatives, the
controller widens retrieval depth **tenfold** and rotation pins the confident
head while refreshing the tail. Repeating an identical Top-10 burns a turn;
rotating everything wrecks MRR.

### A set-preserving semantic cascade

A 4.5 MB quantized TinyBERT cross-encoder reorders a **copy** of the final
visible Top-10 on eligible early Browsing turns. Because it is a **permutation of
the returned set**, and the evaluator's hit test is set membership, **HR@10 and
MTTC are provably invariant and only MRR can move** — derived from the
evaluator's source, then verified exactly at every λ and on the full-Agent run.
**15.95 ms p95 on Darwin arm64**, ~8.5% of turns, byte-exact A0 fallback on any
failure. The artifact is a **4.5 MB ONNX file** in a **5.46 MB bundle**, and the
quantization targets arm64 -- no cross-platform semantic performance is
claimed.

### Reproducible negative-result discipline

Every experiment ran under an **exclusive lease** in an isolated git worktree
with all inputs fingerprinted. Results go to **append-only ledgers** and are
**never rewritten** — a wrong row is superseded by an invalidation record. **One
citability predicate** decides what any report may quote.

Phase 7 was **pre-registered before it ran**: split, objective, grid, gates,
finalist rule, stop conditions. **Two corrections were committed before the
first trial**, both found by the machinery rather than by inspection — including
one where the cached objective disagreed with the official evaluator on **35 of
120** override sessions, in both directions.

**The public 200 is a confirmation set: one finalist, one run, one number.**

---

## 20% — Innovation & Problem Insight

**The insight that produced the 8.7×** was that this evaluation's two hardest
scenarios interact: asking `other` harvests bulk disclosure, and *not* clearing
evidence on override keeps it — and because hits *before* an override turn score
nothing, the two are **super-additive**: 0.107 → 0.7536, a 7.1× jump from two
changes that are worth far less apart.

**The insight we are proudest of is a negative one.** We refit the nine ranking
weights on a supplementary corpus we generated:

> **A1 demonstrated strong within-generator generalization but failed
> cross-distribution transfer.** The supplementary corpus is
> catalog-metadata-grounded and rewards category, exact-match and IDF signals;
> the public Amazon 5-core sessions carry a much stronger popularity prior. A1
> reduced `w_pop` from 4.0 to 1.0 and increased lexical/category weights,
> improving supplementary MRR while reducing public MRR. Popularity is the
> strongest measured explanation, but **not claimed as the sole factor because no
> post-public ablation was performed.**

**+0.229 MRR** on supplementary validation. **−0.116 MRR** on public — regressing
on **every** official slice and **every** robustness scenario. The two corpora
want different rankers, and the arm that discovered it is the arm that failed
because of it. `score_default` stayed unchanged.

**Credibility gates as an architectural pattern.** The system treats every
external signal — a long-term profile tag, a stated constraint, a model's output
— as something to be *judged before use*. A profile tag matching the whole pool
separates nothing and is rejected as `generic`; a contested constraint is
suppressed rather than trusted; a semantic reranker that returns a
non-permutation is refused. **The gate, not the signal, is the contribution.**

---

## 20% — Impact & Relevance

**Sub-40 ms turns on a laptop CPU, with no model serving, no vector database and
no API bill.** A shopping copilot that costs nothing per turn can run per-session
for every visitor, not just for a premium tier — and it runs where the network
does not: the shipped path makes **zero network calls** and needs **zero
credentials**.

**MTTC 2.06** means the official simulated sessions that hit the target do so in
about two turns on average. It is useful task evidence, not a measurement of
real customer abandonment.

**The optional semantic reranker degrades honestly, and that claim is scoped.**
With `showcase_semantic` enabled, a missing model directory, a load failure, an
inference failure or a non-permutation from the scorer each return **byte-exact
A0 ordering**, with a distinct reason code. That is a verified property of the
semantic reranker specifically. It is not a claim that every component of the
system degrades gracefully under every possible failure.

**The evidence discipline is the transferable part.** Leases, append-only
ledgers, one citability predicate, pre-registration, and negative results kept
rather than buried — that is a template any team measuring a ranking change can
copy, and it is what makes 0.932067 a number rather than a claim.

---

## 15% — Feasibility & Practicality

| | |
|---|---|
| dependencies (scored path) | **none** — `requirements.txt` is empty and says why |
| network at runtime | **none** |
| GPU | **none** |
| cold start | 11.526 s (FTS5 index over 50,000 products) |
| warm turn | 24.366 ms p50 · 26.908 ms p95 |
| full 200-session evaluation | 30.47 s |
| peak RSS | 701.4 MB |
| verified on | performance set: Python 3.14.6 / Darwin arm64; score/test/dependency gates also pass on Linux Python 3.10/3.11 |
| optional semantic | measured on Darwin arm64 only; the ONNX file is arm64-quantized |

**One command reproduces the score:**

```bash
python3 -m evaluator.local_evaluator --catalog data/catalog.jsonl --dataset data/public_set.jsonl
```

Verified from a **detached clean worktree** with no virtualenv, no local caches
and no developer state linked in — `docs/FINAL_VERIFICATION.md`. Third-party
packages loaded during a full evaluation: **none**.

The optional semantic showcase pins three versions and bundles its Apache-2.0
model with per-file SHA-256 — and `score_default` does not require the artifact
to exist.

Project code and documentation are **MIT licensed**, copyright 2026 Kairon.
The bundled TinyBERT artifact remains separately licensed under **Apache-2.0**;
its unchanged license ships beside the model and is not replaced by the root
project license.

---

## 10% — Presentation

```bash
python3 -m demo
```

Four public sessions replayed by the **official customer simulator**, showing
route, retrieval decision **and its reason code**, typed evidence with polarity
and source turn, **why** each question was asked, Top-3 and per-turn latency.
The agent never sees the target; the harness reveals it once, at the end, under a
banner saying so.

Three optional showcases — the semantic Top-10 permutation with a multiset
equality check, dense candidates with lexical-overlap counts, and profile
credibility rejection — each labelled **feature-off** with no public claim.

`docs/DEMO_SCRIPT.md` gives per-scenario "point at this / say this", including
what **not** to say.

---

## Official submission fields

### Development tools actually used

| tool | use |
|---|---|
| Python 3.14.6 (CPython) | authoritative quality/latency/memory run on Darwin arm64 |
| GitHub Actions, Ubuntu, Python 3.10/3.11 | successful portability matrix for the full suite, exact score and zero-third-party-import gates |
| `sqlite3` with FTS5 (standard library) | the retrieval index |
| `unittest` (standard library) | the 828-test suite |
| `git` (worktrees, tags, append-only ledgers) | isolation for every measurement, and the provenance chain |
| `venv` + `pip` | **only** for the optional semantic showcase |
| macOS on Apple silicon (Darwin arm64) | the measurement host |
| Claude Opus 5 (Anthropic) | a paired engineering and review assistant, used under the participant's direction; commits are co-authored where that assistance was material |

Nothing else. No cloud training, no experiment-tracking service, no hosted
vector database, no annotation platform.

### APIs used

**There is no external runtime API. The scored agent makes zero network calls,
holds no credentials, and spends zero tokens.** This is verified from a clean
checkout: third-party packages loaded during a full 200-session evaluation --
**none**.

Two network calls exist at **preparation time only**, and neither is on any
runtime path:

| API | when | why |
|---|---|---|
| Hugging Face file download | once, to fetch the optional cross-encoder at a **pinned revision SHA** | `lab/r1_artifact.py`; byte counts checked against a committed manifest, sha256 recorded. The artifact is now bundled, so even this is no longer required |
| GitHub Releases | once, to obtain the organizer's `catalog.jsonl.gz` and its `SHA256SUMS` | the catalog is organizer data and is not redistributed by us |

### Libraries and frameworks

**`score_default`: none.** `requirements.txt` contains no packages and says so.

**`showcase_semantic` (optional, off by default):** `onnxruntime==1.24.1`,
`numpy==2.5.2`, `tokenizers==0.22.2`. Deliberately **no `torch` and no
`transformers`** -- measured at 53.2 MB of RSS for
numpy+onnxruntime+tokenizers against 346.1 MB once transformers pulls torch in,
and a shipped ONNX Runtime component needs neither.

### Datasets and model assets

| asset | what it is | provenance |
|---|---|---|
| **Official catalog** `catalog.jsonl` | 50,000 products, `Clothing_Shoes_and_Jewelry` | Amazon Reviews 2023 (McAuley Lab, UCSD), redistributed by the organizer. **Not redistributed by us**; downloaded from the organizer's release and checksum-verified |
| **Official sessions** `public_set.jsonl` | 200 labeled development sessions | organizer participant kit |
| **Synthetic supplementary dataset** `supplementary_dev.jsonl` / `supplementary_holdout.jsonl` | 1,000 + 1,000 sessions **we generated** (`supplementary/generate.py`) from the same catalog | **synthetic and ours.** Catalog-metadata-grounded, so numbers on it describe our generator. Never presented as a real-user or official result |
| **Model** `cross-encoder/ms-marco-TinyBERT-L2-v2` | quantized ONNX cross-encoder, optional and off by default | **Apache-2.0**, pinned revision `81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc`, **4.5 MB ONNX file / 5.46 MB complete bundle**, bundled with its LICENSE and per-file SHA-256. See `docs/MODEL_CARD.md` |

No other model, no fine-tuning, no training of any kind.

The repository's own code and documentation use the root **MIT License**. That
project license and the model's Apache-2.0 license cover different material.

### Architecture and the four pillars

Full walkthrough with a Mermaid flow: **`docs/ARCHITECTURE.md`**.

| pillar | enabled in `score_default` | implemented and evaluated, disabled by default |
|---|---|---|
| **I** intent routing and hybrid pipeline | Buying/Browsing/Mixed/Override routing, dynamic retarget, BM25 over FTS5, category/facet signals, deterministic funnel, feature reranking | dense candidate source + RRF fusion; **TinyBERT ONNX semantic reranking -- architecture and demo evidence, not part of `score_default`** |
| **II** multi-turn scenario evolution | typed `SlotValue` state, negation, contradiction safety, abandonment/suppression, request-more rotation, over-generality detection, pool-aware questioning, starvation-aware widening | targeted override erasure |
| **III** dynamic context programming | **bounded context snapshots with the retrieval controller and the question controller both enabled** | profile normalization and credibility classification, **disabled by default** (`profile_context_mode="off"`, `w_profile=0.0`) because the official profiles showed **no target-discriminating signal** |
| **IV** evaluation matrix | leases, append-only ledgers, one citability predicate, pre-registration, negative results kept | — |

**Pillar I's semantic-ranking component is satisfied by the optional offline ONNX
profile**, which reranks the visible Top-10 locally with no network access.

### Cost, latency and memory

Measured from a clean checkout, Python 3.14.6 on Darwin arm64
(`docs/FINAL_VERIFICATION.md`):

| | `score_default` |
|---|---|
| cold start (process start to first response) | 11.526 s |
| warm turn | 24.366 ms p50 / 26.908 ms p95 |
| full 200-session evaluation | 30.47 s |
| peak RSS | 701.4 MB |
| tokens | **0** |
| network calls | **0** |
| API cost | **$0** |

Optional `showcase_semantic`, on Darwin arm64 only: +15.95 ms p95 for the
semantic component, +0.42 s cold load, +131.6 MB RSS, invoked on about 8.5% of
turns.

### Limitations and future work

Full version: **`docs/LIMITATIONS.md`**. In short:

* `w_pop = 4.0` partly encodes a property of **this** evaluation -- public targets
  sit at the 99.5th popularity percentile because of 5-core sampling. On a corpus
  without that bias the shipped weights are probably wrong.
* The supplementary corpus is **synthetic and ours**; `sup-train` and `sup-val`
  come from the same generator, so a gain there is within-generator only.
* **A2-10 has no public number** and cannot be given one -- the pre-registration
  allows exactly one public confirmation, and A1 spent it.
* **No cross-session memory:** the evaluation API provides no stable user
  identity, so none was invented.
* Typed positive requirements can safely narrow a primary facet pool, but
  relaxation and the rescue lane mean they are not strictly enforced. Explicit
  negative exclusions are scored penalties, never filters.
* Evaluated on **English-language sessions**, one catalog category, 50,000
  products. Other languages and verticals were not tested.
* The `score_default` performance set was measured on **Python 3.14.6 / Darwin
  arm64**; Linux Python 3.10/3.11 CI also passed the exact score, full suite and
  dependency gates. The optional semantic path remains **Darwin arm64** only.

**Future work:** a public-like corpus without 5-core popularity bias; hard
constraint enforcement; and a second confirmation budget so A2-10 can be
measured rather than eliminated.

### Team contributions

**Solo entry**, no GPU, no external API budget. Full breakdown:
**`docs/TEAM_CONTRIBUTIONS.md`**. Contact details are supplied through the
Devpost submission form rather than published in the repository.

---

## Challenges we ran into

**We nearly shipped a number we could not defend.** A1 looked like a
breakthrough — MRR doubling on supplementary data — and the only thing standing
between it and the submission was a pre-registration written before we saw it.
It failed all five public gates. **Writing the gate before the number is the
whole lesson.**

**Our own tooling caught our own errors twice, before either could contaminate a
result:** a cached objective that credited hits the official evaluator discards
(35 of 120 override sessions), and a cache that stored a live mutable reference
so every turn would have replayed against the session's final state. Both were
fixed pre-data, and both are now regression tests.

**A shared catalog cache closed a live agent's database connection** when a
second agent asked for richer fields — a lifetime defect on the experiment's own
path, not a test artefact. The cache key now carries the capability.

## What we learned

* Recall was never the bottleneck **on the public development set**. **Measure
  before optimizing** — recall@200 was 1.000 there on day one, which is a fact
  about that corpus and not a general one.
* A gain on data you generated is a statement about **your generator**.
* Three of our four largest efforts ship **off**. Knowing which is which required
  more engineering than building them.

## What's next

* A public-like corpus **without** 5-core popularity bias — the one experiment
  that would tell us whether the shipped weights or A1's are closer to right.
* Strict hard-constraint enforcement: positive stated requirements currently
  get gated narrowing plus safe relaxation and rescue, while negative exclusions
  are scored penalties. Neither is a Top-10 guarantee.
* A second confirmation budget would let A2-10 be measured rather than
  eliminated.

## Built with

`python` · `sqlite3` (FTS5) · `onnxruntime` (optional showcase) ·
`cross-encoder/ms-marco-TinyBERT-L2-v2` (Apache-2.0, optional, off) ·
Amazon Reviews 2023 (McAuley Lab, UCSD) · project code and docs (MIT)

**Demo video:** _(link placeholder)_
