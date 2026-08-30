# 00 — Close reading of the Track 4 specification

Sources: the Early Bird brief section 4, plus the participant kit's
`docs/competition_specification.md`, `docs/submission_rules.md` and
`docs/evaluation_config.json`. The participant kit takes precedence -- it is more
precise and it is the official frozen artefact.

## The task

A multi-turn shopping agent that, within **at most 10 turns**, places a hidden
target product into the Top-10 **as early and as highly ranked as possible**.

Targets come from real purchase records in Amazon Reviews 2023. Customer messages
are simulated from a hidden intent card derived from product metadata -- **the
dataset contains no real shopping conversations.**

## Scoring

```
HitRate@10 = hit sessions / N
MRR        = mean(1 / target_rank), 0 for a miss
MTTC       = mean(first hit turn), 11 for a miss
Efficiency = clip((11 - MTTC) / 10, 0, 1)

TechnicalScore = 0.50 x HitRate@10 + 0.30 x MRR + 0.20 x Efficiency
```

Official weak baseline: HR@10 `0.125` / MRR `0.068034` / MTTC `9.81` /
**TechnicalScore `0.10671`**

Judging rubric (weights): Technical Execution 35 / Innovation and Problem Insight
20 / Impact and Relevance 20 / Feasibility and Practicality 15 / Presentation 10
(finals only).
**TechnicalScore is not the same thing as that 35%.** It is supporting evidence;
engineering quality and the narrative count just as much.

## Scenario distribution (identical across both splits)

| scenario | share | public n | private n | characteristic |
|---|---|---|---|---|
| Buying | 40% | 80 | 320 | discloses one hard constraint on turn 1 |
| Browsing | 40% | 80 | 320 | turn 1 is vague, category only |
| Intent Override | 15% | 30 | 120 | replaces an earlier preference on turn 3 or 4 |
| Boundary | 5% | 10 | 40 | the customer may have no preference for an attribute |

**The public and private sets have the same scenario mix, so conclusions drawn
on the public set can be extrapolated.**

## Hard constraints

- **A hard 10-turn cap.** Exceeding it scores zero.
- The product catalog is **read-only**; structural modification or injecting fake
  ASINs is forbidden.
- Training or full fine-tuning of a base LLM is forbidden.
- Deploying a heavyweight industrial vector database is forbidden; it **must run
  lightweight and fully in memory**.
- Text only: a text catalog, structured metadata, text dialogue.
- UI/UX is not scored (a pure backend API evaluated by a headless pipeline).

## The single most important clause: final scoring may have no network

> "For official final scoring, organizer policy may disable network access."
> "The organizer reserves the right to run your submission under CPU, memory,
> timeout, and network restrictions."

**Implication: any approach depending on an external LLM API may simply stop
working at final scoring.** The design principles for this project follow from
that:

1. **The main path must be entirely local, make zero network calls, and run on
   CPU alone.**
2. If a model is introduced, it may only be an **optional enhancement**, and it
   must have an equivalent offline fallback.
3. The submission must state explicitly whether it needs network access (the
   rules require this).

This clause also flattens the advantage of anyone able to spend money on API
credits.

## Visible fields

`parent_asin`, `title`, `features`, `description`, `price`, `categories`,
`details`, `average_rating`, `rating_number`, `store`. Only `parent_asin` is
scored.

> Note: `average_rating` and `rating_number` are **currently unused entirely**.
> Since targets are real purchase records, a popularity prior is likely to help
> MRR.

`user_profile` is an anonymized aggregate: purchase frequency, rating style,
`preference_tags`. **Also currently unused entirely.**

## The interface contract

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None: ...
    def respond(self, session_id: str, user_message: str,
                turn: int, top_k: int) -> dict:
        return {"message": str,               # natural language for the customer
                "ask_attribute": str | None,  # the simulator reads ONLY this
                                              # field; it never parses `message`
                "recommendations": [{"parent_asin": "B000..."}],
                "usage": {"prompt_tokens": int, "completion_tokens": int}}
```

- `ask_attribute` is one of {category, material, color, size, style, brand,
  budget, feature, use_case, other} or `null`.
- Invalid or duplicate ids are dropped, and only the **first 10 valid unique
  ids** are scored, so returning junk simply wastes slots.
- An optional numeric `score` field is accepted but **ignored**.
- **Exceptions, illegal output and timeouts may all be scored directly as a
  miss**, so robustness is a hard requirement.

## Innovation directions the organizer named (i.e. what judges want to see)

- Buying / Browsing routing and multi-route retrieval
- Hybrid retrieval and semantic reranking
- Structured constraint state, intent-override handling, dynamic context
  construction
- **Adaptive clarification and "question value estimation"** -- explicitly named
  by the organizer
- Safe personalization using the aggregate profile
- Failure detection, strategy switching, low latency, low token cost
- Explainable recommendation rationales

## Deliverables

- Source plus install and reproduction instructions (one command runs the
  official harness)
- An Agent conforming to the interface (a single entry-point file exporting
  `Agent`)
- A short report: architecture, model choice, cost, limitations, division of work
- One complete multi-turn session demonstration
- Disclosure of latency, token usage and estimated cost
- A Devpost project description, a public GitHub repository, and a YouTube demo
  video
