# 02 — Survey of open-source best practice

Every entry is annotated with **whether it fits this task's constraints**: the
10-turn cap, in-memory only, no base-model fine-tuning, and **final scoring may
have no network**.

## A. Retrieval layer

### A1. Sparse / lexical retrieval
Currently SQLite FTS5 + BM25 with column weights `(0, 6, 4, 2.5, 2.5, 1.5, 1)`.
- **BM25 parameters:** FTS5's `bm25()` fixes k1=1.2 and b=0.75 and is not
  tunable. Tuning them would require our own implementation, or switching to
  `rank_bm25` (pure Python, acceptable for 50k documents). **Worth an ablation;
  cheap.**
- **Query expansion (RM3 / pseudo-relevance feedback):** expand the query with
  frequent terms from the first turn's top-K. Classic and model-free. However,
  the "new information" in this task comes from customer replies rather than from
  the corpus, so the payoff may be limited. Worth trying.
- **Document expansion (doc2query):** needs a generative model over the whole
  catalog. Feasible offline but expensive. **Not considered for now.**

### A2. Dense retrieval
- Candidate models: `all-MiniLM-L6-v2` (22M, 384-dim), `bge-small-en-v1.5`
  (33M, 384-dim), `gte-small`. All far below any parameter cap and CPU-runnable.
- 50k products x 384 dims in float16 is about **38 MB**; PCA down to 128 dims is
  about 12 MB.
- **Network risk:** model weights would have to ship with the repository or be
  downloaded during setup. The rules permit "lightweight local assets", but
  whether a 90 MB model counts as lightweight is open to interpretation.
  **A safer variant: precompute the embedding matrix offline and submit only the
  `.npy`** -- but then queries cannot be encoded. A middle path: precomputed
  product vectors plus a very small encoder (a quantized MiniLM at around 20 MB),
  or fall back to a purely lexical approach. **Decide only after measuring
  whether the gain justifies the risk.**
- **Rules:** "infrastructure-heavy vector databases" are out, so use numpy matrix
  multiplication: brute-force over 50k x 384 is about 20 ms, **entirely
  sufficient, and faiss is not needed.**

### A3. Hybrid fusion
- **RRF (Reciprocal Rank Fusion):** `score(d) = sum 1/(k + rank_i(d))`, with the
  industry default k=60. No weights to tune and comparable across systems, which
  makes it a robust default.
- **Weighted score fusion:** needs normalization; possibly better, but more
  brittle.
- Verdict: **start with RRF (k=60), then try weighting.**

## B. Reranking layer — the largest remaining headroom (+0.093 of MRR)

### B1. Cross-encoder
`bge-reranker-v2-m3` and `ms-marco-MiniLM-L-6-v2` are the standard choices.
**Not applicable:** 200 sessions x about 3.5 turns x 300 candidates is roughly
210,000 cross-encoder forward passes, which is unacceptable on CPU and works
against the low-latency requirement. **Excluded.**

### B2. Lightweight feature reranking (recommended)
Take N candidates from FTS (N = 200 to 500), then rescore in Python with cheap
features:

| feature | rationale |
|---|---|
| raw BM25 score | lexical relevance |
| count of exact/substring **string-level** constraint matches | constraint text is copied verbatim from the target's features/details |
| `average_rating` x log(`rating_number`) | targets are real purchase records, so a popularity prior applies |
| closeness of price to `budget around $X` | budget constraints are currently treated as ordinary words |
| category match (turn 1's `coarse_category` comes from the target's category) | a strong signal |
| title length / field coverage | normalization terms |

Linear weighting, with weights fitted by cross-validation on the public set. With
only 200 sessions, **k-fold is mandatory to guard against overfitting**, and this
must be stated in the report. **This is the next step.**

### B3. LLM reranking
The brief's Pillar I explicitly asks for "LLM Semantic Ranking".
This conflicts directly with the no-network risk. **Conclusion: implement it as
an optional enhancement, default it off, provide B2 as the offline fallback, and
state this explicitly in the report.** That is exactly what the rules encourage
("if your system has an offline fallback, describe it").

## C. Question-policy layer — the layer with the most transferable value

### C1. Standard practice in the literature
Conversational recommender systems (CRS) classically split each turn's decision
into "**ask about an attribute or recommend a product**", then "**which
attribute to ask about**".

| system | question policy |
|---|---|
| Abs Greedy | recommend only, never ask (lower-bound baseline) |
| Max Entropy | rule-based: pick the attribute with maximum entropy in the candidate set |
| CRM (2018) | RL plus a belief tracker |
| EAR (WSDM'20) | three-stage Estimation-Action-Reflection |
| CPR / SCPR (KDD'20) | path reasoning on a graph; **weighted information entropy** `g(u,p,V) = -prob(p)*log2 prob(p)`; RL action space compressed to 2 (ask / rec) |
| UNICORN (SIGIR'21) | unified graph-RL policy |

A recent paper (arXiv 2603.11399) gives a more directly usable form:
```
H(d)      = -sum_v p(v) log2 p(v)        # entropy of attribute d's value
                                         # distribution over the candidate set
H_norm(d) = H(d) / log2 |Val(d)|         # normalized to [0,1] by value cardinality
choose argmax H_norm(d); if all are below tau (the paper uses 0.3), recommend instead
```

### C2. Verdict for this task — something that must be handled honestly

**Under this simulator, the information-gain optimum degenerates to the constant
policy "always ask `other`"**, because `customer_reply` matches any undisclosed
constraint unconditionally for `other` (finding 1). No IG policy can beat it.

So it would be **dishonest** to claim "we obtained a 7x improvement using an
information-gain policy".

**The correct approach, which is also the better story:**
1. Implement a general candidate-set-entropy attribute selection policy.
2. **Prove experimentally** that under this simulator's disclosure model the
   optimal action degenerates to `other`, and give the reason for the
   degeneration.
3. Quantify the split: how much of the gain comes from policy design and how much
   from the simulator's structure.
4. Discuss how much an IG policy would gain if the simulator matched strictly by
   attribute (closer to a real user) -- we can write a `strict` simulator mode
   ourselves and run that counterfactual.

This layer is the part that withstands follow-up questions, and it hits the
organizer's explicitly named **"adaptive clarification and question-value
estimation"**.

## D. Design principles established

1. **The main path is fully local, zero-network, CPU-only** -- derived from the
   no-network clause in `submission_rules.md`.
2. Any model-based component must be switchable off and have an equivalent
   offline fallback.
3. No vector database; brute-force numpy search is sufficient.
4. Every change must be quantified through `lab/sweep.py` and written to
   `experiments.jsonl`.
5. The report must honestly separate "exploiting a simulator mechanism" from "a
   transferable modelling insight".

## References

- Advances and challenges in conversational recommender systems: A survey — https://www.sciencedirect.com/science/article/pii/S2666651021000164
- Interactive Path Reasoning on Graph for Conversational Recommendation (CPR/SCPR, KDD'20) — http://staff.ustc.edu.cn/~hexn/papers/kdd20-graph-crs.pdf
- Entropy Guided Diversification and Preference Elicitation in Agentic Recommendation Systems — https://arxiv.org/html/2603.11399
- CRSPapers (a reading list for conversational recommendation) — https://github.com/Zilize/CRSPapers
- Hybrid Search for RAG: BM25 + Dense (2026) — https://denser.ai/blog/hybrid-search-for-rag/
