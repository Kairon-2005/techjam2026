# TechJam 2026 · Track 4 — Shopping Copilot

A multi-turn conversational product-retrieval agent. It has a 10-turn budget to
place the hidden target product in the Top-10; earlier and higher is better.

> **Historical working document.** The status table below records the project as
> it stood mid-project. The final, verified submission numbers are in `README.md`
> and `docs/FINAL_VERIFICATION.md`: TechnicalScore **0.932067**, HR@10 0.995,
> MRR 0.852556, MTTC 2.06.

## Status at the time of writing

| | TechnicalScore | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| official weak baseline | 0.1067 | 0.125 | 0.068 | 9.81 |
| stage 1: dialogue policy | 0.7536 | 0.870 | 0.560 | 3.48 |
| stage 2: reranking layer | 0.9287 | 0.995 | 0.8394 | 2.03 |
| **stage 3: pool-aware questioning (default then)** | **0.9285** | **0.995** | **0.8394** | **2.04** |
| *realistic joint upper bound* | *0.9822* | *1.000* | *1.000* | *1.89* |

**8.70x over the baseline.** Pure Python standard library: zero network, zero
GPU, no learned or neural model weights on the scored path, 0 tokens, a single
full evaluation in 11.3 seconds at a 627 MB peak. `python3 -m
evaluator.local_evaluator` with no arguments reproduced **0.928508** at that
time.

The default then used pool-aware questioning (`ask_policy="other_then_pool"`):
17% of turns ask a real information-gain question derived from candidate-pool
entropy, at a cost of 0.0002. It **affects questioning only, never ranking** --
HR@10 and MRR are identical digit for digit to the pure `other` policy
(0.995 / 0.839361), and the entire difference is in MTTC (2.03 to 2.04). Pure
`other` reproduces 0.928708 with a single key, `ask_policy="other"`, and both are
locked by tests.

## Directory layout

```
notes/          reading and research notes (the basis for decisions)
  00-problem-spec.md         task / constraints / resources / scoring
  01-evaluator-mechanics.md  reverse-engineering the evaluator + full ablation table
  02-literature.md           survey of open-source best practice and its applicability
NOTES.md        decision log (what was decided, on what evidence, what would overturn it)
lab/            experiment infrastructure
  sweep.py          run several configurations, reusing catalog indexes across them
  analyze.py        generate the ablation table from experiments.jsonl
  experiments.jsonl append-only experiment log (config / metrics / per-scenario / timing / git hash / timestamp)
starter/agent.py    the submitted agent; every behaviour is a configuration key
docs/           the organizer's original documents (do not modify)
data/           frozen catalog + 200 public sessions
evaluator/      the official evaluator (do not modify)
```

## Usage

```bash
python3 -m lab.sweep                                  # run the default ablation group
python3 -m lab.sweep name='{"ask_policy":"other"}'    # run one custom configuration
python3 -m lab.analyze                                # print the ablation table
python3 -m evaluator.local_evaluator                  # the official harness, default config
```

## Design principles

1. **The main path must make zero network calls and run on CPU only.**
   `docs/submission_rules.md` states explicitly that final scoring may disable
   network access. Any model-based component must be switchable off and have an
   equivalent offline fallback.
2. No vector database. Brute-force search over 50k x 384 with numpy is around
   20 ms, which is enough.
3. Every change must be quantified through `lab/sweep.py` and written to
   `experiments.jsonl`. "It feels better" is not accepted.
4. The report must honestly separate "exploiting a simulator mechanism" from
   "a transferable modelling insight".

## Architecture

```
user message -> intent routing (buying/browsing/override, zero-cost template match)
             -> structured constraint state (parse_message: category + constraint phrase list;
                override does not clear it)
             -> question policy (ask_attribute)
             -> FTS5/BM25 recall of the top 100
             -> linear feature reranking (phrase / exact / field / idf / cat / popularity / bm25)
             -> top 10
```

## Response to external review (notes/08)

Of 10 criticisms, 4 were empirically testable. All four were implemented as
configuration keys defaulting to off and then quantified: **2 adopted, 2
rejected.**

| criticism | verdict | evidence |
|---|---|---|
| dual-track routing is ineffective | description correct, **remedy rejected** | the +0.0013 from `w_pop=6` is 2 sessions better / 7 worse, and only 2 of 5 folds improved |
| override is not real erasure | description correct, **remedy rejected** | under genuine contradictory overrides, keep 0.9233 > slot 0.9140 > erase 0.8458 |
| questioning is a simulator shortcut | correct, **adopted** | `other_then_pool` asks a real information-gain question on 17% of turns at a cost of -0.0002 |
| resource usage undisclosed | correct, **adopted** | lazy indexes: 7.70s/430MB to 5.07s/393MB, score unchanged |

Both rejections share one cause: **this approach scores and does not filter.**
Obsolete evidence cannot exclude the correct product, so the benefit of both
"forgetting" and "branching by route" is lower than its cost.

## Backlog

- [x] reranking layer -- 0.7536 to 0.9280
- [x] popularity prior -- the single strongest feature, +0.114
- [x] cross-validated tuning -- 5 folds, mean 0.9280 +/- 0.0203
- [x] confirmed `price` is available for only 21% of products, so the payoff is
      limited; deprioritized
- [x] confirmed the `user_profile` personalization signal is very weak; abandoned
- [ ] MRR: lift ranks 2-5 to rank 1 (+0.053 remaining, 91% of all headroom)
- [ ] a general information-gain question policy, plus a proof that it degenerates
      to `other` under this simulator
- [ ] before submission: README, requirements, one-command reproduction, report,
      demo video
