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
flattens the resource gap. The authoritative clean-checkout run evaluates all
200 simulated sessions in **31.11 s** on the development host.

## What was built

| area | work |
|---|---|
| **Agent core** | intent routing, typed evidence state, deterministic funnel, feature reranking, question policy — `starter/` (~4,800 lines) |
| **Context programming** | `ContextSnapshot` / `PreRetrievalSnapshot`, retrieval controller, question controller, profile credibility classifier — `starter/context.py` |
| **Optional capabilities** | dense random-indexing retriever + RRF fusion; TinyBERT ONNX cross-encoder cascade — both implemented, measured, shipped off |
| **Experiment harness** | exclusive leases in isolated worktrees, append-only ledgers, one citability predicate, invalidation records, scenario library, benchmark harness — `lab/` |
| **Evidence discipline** | 8 phases of pre-registration and results notes — `notes/`, 46 documents |
| **Tests** | 827, all executed on a committed tree, including exact public-score locks, the configuration lock, and negative tests for every guard |
| **Packaging** | clean-checkout verification, model card, demo, this documentation |

## Why the work is award-relevant

The contribution is the measured combination, not an unverified feature count:

* **Dual-route intent handling** gives Buying and Browsing different retrieval
  topologies and retargets a session as soon as stated evidence becomes usable.
* **Starvation-aware widening** and **pool-aware clarification** respond to the
  live candidate state, under explicit retrieval and question controllers with
  per-turn reason codes.
* **Append-only, fingerprinted, citable evaluation** makes feature adoption and
  rejection auditable. Dense/RRF retrieval, TinyBERT reranking and profile
  ranking were implemented and measured, then kept out of `score_default` when
  their evidence did not clear the gates.
* The shipped path runs locally on CPU with **zero model tokens, zero network
  calls and zero API cost**. In the authoritative detached clean-checkout run it
  scored TechnicalScore **0.932067**, HR@10 **0.995** (199/200 simulated
  sessions), MRR **0.852556** and MTTC **2.06**.

That performance set comes from commit
`4d85dc5b3d229a6698bea53f61c9ae8c7de539f7`, Python 3.14.6 / Darwin arm64.
Published portability CI independently succeeded on Ubuntu for Python 3.10 and
3.11 and reproduced 0.932067 exactly: [GitHub Actions run
33290548542](https://github.com/Kairon-2005/techjam2026/actions/runs/33290548542).

## What was not done, and is not claimed

* No fine-tuning of any model.
* No LLM in the scored path — no prompts, no tokens, no API.
* No use of the sealed holdout for any configuration decision.
* No second team member; there is no division of labour to report.

## License

Project code and documentation: **MIT**, copyright 2026 Kairon. Bundled
`cross-encoder/ms-marco-TinyBERT-L2-v2` model files: **Apache-2.0**, with their
unchanged license stored beside the artifact. The two licensing scopes are
deliberately separate.

## Provenance of the numbers

Submitted quality and cost figures trace to `docs/FINAL_VERIFICATION.md(.json)`;
experiment figures trace to rows in append-only ledgers under `lab/`, produced
by verified leases and checked by `lab/provenance.py`'s single citability
predicate. Rows that do not pass are kept and marked, not deleted —
`lab/invalidations.jsonl` records four of them, including two superseded cache
builds this submission does not rely on.
