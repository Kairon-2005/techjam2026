# Demo script — what to run, what to point at

Everything below runs on the **real public sessions** with the **official
evaluator's own customer simulator**. Nothing is scripted, and the agent never
sees the hidden target: the target and the outcome are printed once per session
under a banner that says they are the harness's, not the agent's.

```bash
python3 -m demo                      # the four default scenarios, ~1 min
python3 -m demo --only override      # just one
python3 -m demo --list
```

Cold start is ~13 s (SQLite FTS5 index build over 50,000 products), then every
turn is tens of milliseconds. Say that out loud — it is the point.

## Reading a turn

```
  turn 3  ·    68.0 ms
    customer   For that, what matters is: Imported; Wrap closure.
    route      buying       retrieval  standard (DEPTH_STANDARD, depth 100)
    evidence   +polyester (h2)  +imported (h3)  +2 more
    question   use_case     POOL_ATTRIBUTE_SELECTED / structured
    agent      Top of the list right now is GUBERRY Womens Wrap V Neck …
    top 1      B08FFGQF72  GUBERRY Womens Wrap V Neck Long Sleeve Velvet…
```

| field | what it shows |
|---|---|
| `route` | Buying / Browsing / Mixed / Override, after retargeting |
| `retrieval` | the retrieval controller's decision, its reason code and depth |
| `evidence` | typed slots: `+`/`−` polarity, `h`/`s` hardness, source turn, confidence; `dropped:` lists suppressed or erased evidence |
| `question` | the attribute asked and **why** — reason code and open vs structured |
| `top N` | the ranked recommendations that turn |
| ms | wall-clock for the whole turn |

## 1 · `buying` — hard constraints on the precision track

**Point at:** `evidence` growing turn by turn as typed slots, and `route`
staying `buying` with the precision retrieval track.

**Say:** every constraint is a typed record — attribute, value, polarity,
hardness, confidence, which turn it came from — not a bag of words.

## 2 · `browsing` — over-generality and proactive clarification

Pinned to `public_0012`, a session whose pool really is over-general.

**Point at:** turn 3's question line: `POOL_ATTRIBUTE_SELECTED / structured`.

**Say:** the agent noticed the surviving pool spanned too many coarse
categories, so it stopped asking the open "anything else" and asked a
**pool-derived** question, choosing the attribute by how well it splits the live
candidates — then rendered it as a structured choice instead of an open prompt.

## 3 · `override` — what `score_default` actually does

**Point at:** the policy comparison printed after the session.

**Say:** the customer replaces a preference mid-session. We ship
`on_override='keep'`, and that is a **measured** choice: this system *scores*
candidates rather than filtering them, so a stale constraint can only add a
little wrong credit, while forgetting destroys evidence outright. Over 5 seeds:
keep **0.9233**, targeted slot erasure 0.9140, full erasure 0.8458. Targeted
erasure is implemented and is one config key away — it simply lost. The demo
then re-runs the same session under `on_override='slot'` so you can watch the
superseded evidence disappear.

**Do not say** "we erase the old preference." We do not, by default.

## 4 · `uncooperative` — recovery with no new evidence

Uses `lab/scenarios.py`'s `uncooperative` scenario — the same object the
robustness matrix measured, not a demo-local copy.

**Point at:** turn 2's retrieval line: `widened (WIDEN_REQUEST_MORE, depth
1000)`, and the Top-1 staying pinned while ranks 2–3 refresh.

**Say:** the customer stopped answering and asked for alternatives. The
retrieval controller widened the funnel tenfold, and the rotation kept the
confident head while refreshing the tail — repeating an identical Top-10 would
burn the turn, rotating everything would wreck MRR.

## Optional showcases — all three are FEATURE-OFF

```bash
python3 -m demo --extra semantic     # needs requirements-semantic.txt
python3 -m demo --extra dense
python3 -m demo --extra profile
```

**Open with this, every time:** these are implemented, measured and **off** in
the scored configuration. None of them contributes to 0.932067.

### 5 · `semantic` — the A2-10 Top-10 permutation

**Point at:** the two-column rank table and the line `same multiset True`.

**Say:** a 4.5 MB quantized TinyBERT cross-encoder reorders a **copy** of the
final visible Top-10 on eligible early Browsing turns — 15.95 ms p95, ~8.5–8.9%
of turns. Because it is a permutation, HR@10 and MTTC are provably invariant and
only MRR can move. It is worth **+0.008 MRR on supplementary validation** and
**has no public number at all** — it was eliminated at finalist selection, and
the pre-registration allows exactly one public confirmation for one finalist.

**It is a reranker, not a generative LLM.** It emits one number per candidate.

Needs `./.venv/bin/pip install -r requirements-semantic.txt`. Without the
packages or the artifact the cascade falls back **byte-exactly** to A0, and the
demo says so instead of failing.

### 6 · `dense` — a second candidate source, fused by RRF

**Point at:** `dense-only 281` against `overlap with BM25 19`.

**Say:** a deterministic in-memory random-indexing retriever, built at load time
from the catalog itself, surfaces candidates with **no lexical overlap at all**
— and reciprocal-rank fusion merges them with BM25. It is off by default because
it improves Browsing MRR while dropping Boundary MRR from 1.000 to 0.870.

### 7 · `profile` — credibility, not personalization

**Point at:** the first case reaching `no_credible_tag`, and the last line:
`w_profile in score_default = 0.0`.

**Say:** the evaluator hands us an external long-term preference profile. We
distill it into bounded evidence and judge each tag against the live candidate
pool — matches nothing → *unsupported*; matches most of the pool → *generic*, it
separates nothing; in between → *specific informative*. Both cases here are
**derived from this session's own pool**, not hand-picked.

**Say next, and do not skip it:** the classification is recorded and **never
moves a rank**. Phase 6C1 found no demonstrated target alignment, so the weight
is zero. And there is **no cross-session memory** — the evaluation API provides
no stable user identity, so we did not invent one.

## Closing line

```bash
python3 -m evaluator.local_evaluator --catalog data/catalog.jsonl --dataset data/public_set.jsonl
```

0.932067 · HR@10 0.995 · MRR 0.852556 · MTTC 2.06 · **0 tokens, 0 network calls,
standard library only.**
