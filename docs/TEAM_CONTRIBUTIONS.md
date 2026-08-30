# Team contributions

## Team

**Solo entry.** One participant, no GPU, no external API budget.

| | |
|---|---|
| participant | Kairon |
| contact | supplied through the Devpost submission form; not published in this repository |
| role | everything below |
| assistance | Claude Opus 5 (Anthropic) used as a paired engineering and review assistant throughout, under the participant's direction. Every design decision, pre-registration, gate and stop condition in `notes/` was authored and approved by the participant; commits are co-authored where that assistance was material. |

Track 4 was chosen **because** of the solo, no-GPU constraint rather than
despite it: the track's rules cap compute (no base-model fine-tuning, no heavy
vector database, in-memory only) and ship a deterministic local evaluator, which
flattens the resource gap and makes ~25-second iteration possible.

## What was built

| area | work |
|---|---|
| **Agent core** | intent routing, typed evidence state, deterministic funnel, feature reranking, question policy — `starter/` (~4,800 lines) |
| **Context programming** | `ContextSnapshot` / `PreRetrievalSnapshot`, retrieval controller, question controller, profile credibility classifier — `starter/context.py` |
| **Optional capabilities** | dense random-indexing retriever + RRF fusion; TinyBERT ONNX cross-encoder cascade — both implemented, measured, shipped off |
| **Experiment harness** | exclusive leases in isolated worktrees, append-only ledgers, one citability predicate, invalidation records, scenario library, benchmark harness — `lab/` |
| **Evidence discipline** | 8 phases of pre-registration and results notes — `notes/`, 46 documents |
| **Tests** | 813, all executed on a committed tree, including exact public-score locks, the configuration lock, and negative tests for every guard |
| **Packaging** | clean-checkout verification, model card, demo, this documentation |

## What was not done, and is not claimed

* No fine-tuning of any model.
* No LLM in the scored path — no prompts, no tokens, no API.
* No use of the sealed holdout for any configuration decision.
* No second team member; there is no division of labour to report.

## Provenance of the numbers

Every figure quoted in the README, the Devpost draft and the notes traces to a
row in an append-only ledger under `lab/`, produced by a verified lease, and
passes `lab/provenance.py`'s single citability predicate. Rows that do not pass
are kept and marked, not deleted — `lab/invalidations.jsonl` records four of
them, including two superseded cache builds this submission does not rely on.
