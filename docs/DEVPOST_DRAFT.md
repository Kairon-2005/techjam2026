# Devpost draft

Organized by the judging weights. Every number here is reproducible from a clean
checkout and traceable to a ledger row.

---

## Inspiration

A shopping assistant that asks a good question is worth more than one that
returns a longer list. The weak starter scores **0.107** — not because it cannot
find products, but because it never learns what the customer actually wants.
Recall@200 turned out to be **1.000** from the very beginning: the target was
almost always already in the pool. **The problem was never retrieval. It was
knowing what to keep, what to ask, and how deep to look.**

## What it does

A multi-turn shopping copilot that finds the customer's hidden target in the
Top-10 within 10 turns, by tracking **typed evidence** across turns, asking the
question that **actually splits the live candidate pool**, and letting **runtime
context decide how deep to retrieve**.

| | |
|---|---|
| **TechnicalScore** | **0.932067** (weak baseline 0.10671 — **8.7×**) |
| HR@10 · MRR · MTTC | 0.995 · 0.852556 · 2.06 turns |
| cold start · warm turn | 11.4 s · 25.7 ms p50 |
| **tokens · network · API cost** | **0 · 0 · $0** |

---

## 35% — Technical Execution

**Six modules, ~4,800 lines, Python standard library only.** SQLite FTS5 for
retrieval, deterministic feature reranking, no weights on the scored path.
**783 tests**, including exact end-to-end score locks and a configuration lock
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
**15.95 ms p95**, ~8.5% of turns, byte-exact A0 fallback on any failure.

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

**MTTC 2.06** means customers reach their product in about two turns — the
metric that actually maps to abandonment.

**It degrades honestly.** Every optional capability falls back to the
deterministic core: no model, no runtime, no artifact, an inference failure, a
malformed model output — all return byte-identical A0 behaviour. There is no
configuration in which this system fails closed.

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
| cold start | 11.4 s (FTS5 index over 50,000 products) |
| warm turn | 25.7 ms p50 · 39.3 ms p95 |
| full 200-session evaluation | 30.3 s |
| peak RSS | 703 MB |
| verified on | Python 3.14.6, darwin/arm64 — and only there, stated as such |

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

---

## 10% — Presentation

```bash
python3 -m demo
```

Four real public sessions driven by the **official customer simulator**, showing
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

* Recall was never the bottleneck. **Measure before optimizing** — recall@200 was
  1.000 on day one.
* A gain on data you generated is a statement about **your generator**.
* Three of our four largest efforts ship **off**. Knowing which is which required
  more engineering than building them.

## What's next

* A public-like corpus **without** 5-core popularity bias — the one experiment
  that would tell us whether the shipped weights or A1's are closer to right.
* Hard-constraint enforcement: the pipeline currently **scores** rather than
  **filters**, so "nothing above $50" biases the ranking without guaranteeing it.
* A second confirmation budget would let A2-10 be measured rather than
  eliminated.

## Built with

`python` · `sqlite3` (FTS5) · `onnxruntime` (optional showcase) ·
`cross-encoder/ms-marco-TinyBERT-L2-v2` (Apache-2.0, optional, off) ·
Amazon Reviews 2023 (McAuley Lab, UCSD)

**Demo video:** _(link placeholder)_
