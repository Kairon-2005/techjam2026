# 07 — Four-pillar gap review, generalization hardening results, three-day plan

## A. This round's result: per-slot graceful degradation

**Motivation:** the largest genuine risk identified by the three-way review. If
the private simulator rewrites the constraint strings themselves (payload
rewording), the exact-match features (`w_phrase`/`w_exact`/`w_field`, 8.5 of
weight between them) all die, and the honest expectation falls to 0.67-0.86.

**Validation method** (synthesizing new data): a payload-rewording stress set v2,
where three mechanical transforms rewrite the constraint string directly --
`payload_soft` ("Material:alloy" to "made of alloy"), `payload_shuffle` (tokens
reversed, punctuation stripped) and `payload_drop` (only the two longest tokens
kept). The transforms target the payload, and our cue regular expressions have
never seen them (the chrome template is left as-is, isolating the experiment).

**Mechanism:** before reranking, each disclosed constraint is probed against the
whole candidate pool. A slot that **has** a literal hit in the pool keeps using
the exact features; a **dead slot with zero hits** falls back to IDF-weighted
token-overlap soft matching (terms with IDF < 1.5 are excluded so that words like
"made" cannot manufacture noise). On the verbatim simulator the dead set is
empty, so ranking is unchanged and **the insurance premium is zero** -- measured
at +0.0007, in fact, because it incidentally fixed a matching defect in our own
concatenated compound phrases.

**Results:**

| suite | unprotected | slot_soft=4 (new default) |
|---|---|---|
| clean simulator (official harness) | 0.9280 | **0.9287** |
| payload_soft | 0.8554 | **0.8783** |
| payload_shuffle | 0.8736 | **0.8984** |
| payload_drop | 0.8536 | **0.8760** |
| chrome casual / terse / verbose | 0.9176 / 0.9233 / 0.8564 | 0.9173 / 0.9240 / 0.8568 (unchanged) |

Two approaches discarded along the way (recorded as ablations): a static `w_soft`
(a -0.003 insurance premium on the clean set) and session-level adaptive gating
(flipped by an incidental literal hit; weaker recovery than per-slot). The weight
is insensitive (2 and 4 score almost identically), so this is not an overfitted
point.

**Updated worst-case floor:** full payload rewriting gives about 0.876-0.898
(previously 0.854-0.874); combined with unseen chrome styles the honest interval
is about **0.85-0.92** (previously 0.67-0.86). The `ask="other"` disaster branch
is covered by the `dry_others` fallback.

## B. Four-pillar review, item by item (brief section 4.2 vs current state)

| pillar requirement | current state | gap and response |
|---|---|---|
| I. Dual-Track Routing | done: template routing for buying/browsing/override, zero cost | met; `route_overrides` is supporting evidence |
| I. Multi-Route Retrieval to LLM Semantic Ranking (keyword + category + vector) | partial: keyword yes, category yes (`w_cat`), vector no, LLM no | soft matching is a sparse-vector cosine (stated honestly); the report must argue this positively: recall@200 = 1.0 on the development set means a dense route has no missed recall to rescue, and LLM reranking is excluded by the no-network clause, so the substitute is feature reranking plus per-slot soft matching. **This is our largest deviation from the brief's literal expectation and must be written as an evidenced design decision rather than avoided** |
| II. Information Accumulation | done: incremental slots | met |
| II. Intent Override "slot erasure and rewriting" | partial: we measured keep beating erase (0.87 vs 0.47) and explain it honestly | add a **selective erasure** mode (erase only old slots of the same type that conflict with the new constraint) as an honest implementation of the brief's semantics, plus an ablation comparison -> tomorrow's experiment |
| II. Proactive Guidance / over-generality cutoff | not implemented | truncating recommendations purely loses score under this protocol, so implement "detect and guide by asking" rather than "truncate": when the candidate pool is overloaded, use a pool-aware message/ask selection. Same experiment as information-gain questioning -> tomorrow |
| III. Personalized Context Distillation | partial: the profile signal is weak (measured: a constant plus weak tags) | run one more measurement with soft weighting of `preference_tags`; the expected null result goes into the report as an honest negative |
| III. Adaptive Orchestration | done: two real implementations -- the `dry_others` question-policy degradation and the dead-slot soft-match degradation | present in the report using Pillar III's language |
| slot decay, named in scope | not implemented | time-decayed term weights -> tomorrow's experiment (the override scenario may benefit) |
| IV. Metrics | done: fully aligned | — |

## C. The remaining three days

**D1 (tomorrow, experiment day):** an information-gain question policy plus a
proof that it degenerates to `other` under this simulator (the organizer's named
question-value estimation, including pool-aware over-generality guidance);
selective-erasure ablation; slot-decay ablation; the `preference_tags`
measurement. Every one goes into `experiments.jsonl`, positive or negative.

**D2 (submission-package day):** the English report (including the weakened
wording adopted from the three reviews, all ablation tables, the four-pillar
mapping, and the disclosures and declarations); a `submission/` directory
(`agent.py` plus `requirements.txt` plus a README: Python >= 3.10, the FTS5
dependency, `TJ_CONFIG` documentation, and one command reproducing 0.9287);
public repository cleanup and an honest pre-window commit.

**D3 (inside the window):** substantive in-window updates (the MRR push from rank
2 to rank 1, the verbose residual), a demonstration session transcript, recording
the video, the Devpost description, and submitting.

Questions to ask at the workshop on Thursday 8/28 at 16:00: whether final scoring
disables the network; whether private-set messages are paraphrased and whether
constraints keep their original text; and how "other" is implemented in the
private simulator.
