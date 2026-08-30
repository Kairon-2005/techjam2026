# 01 — Reverse-engineering the evaluator

Findings from a line-by-line reading of `evaluator/local_evaluator.py` (312
lines). All of them are backed by measurement.

## Full ablation table (public set, 200 sessions)

| configuration | TechnicalScore | HR@10 | MRR | MTTC | boundary | browsing | buying | override |
|---|---|---|---|---|---|---|---|---|
| official weak baseline | 0.1067 | 0.125 | 0.068 | 9.81 | 0.00 | 0.03 | 0.24 | 0.13 |
| control (fixed attribute cycling + clear on override) | 0.6295 | 0.745 | 0.467 | 5.15 | 0.60 | 0.79 | 0.82 | 0.47 |
| H1 alone (ask `other`) | 0.6703 | 0.780 | 0.480 | 4.18 | 0.90 | 0.85 | 0.85 | 0.37 |
| H2 alone (keep pre-override state) | 0.6855 | 0.805 | 0.520 | 4.65 | 0.60 | 0.79 | 0.82 | 0.87 |
| H1 + H2 | 0.7371 | 0.855 | 0.539 | 3.60 | — | — | — | — |
| **H1 + H2 + stopwords** | **0.7536** | **0.870** | **0.560** | **3.48** | — | — | — | — |
| H1 + decay (keep the first 8 terms) | 0.7496 | 0.865 | 0.558 | 3.52 | — | — | — | — |
| other_then_cycle | 0.7533 | 0.870 | 0.560 | 3.49 | — | — | — | — |

**7.06x over the official baseline.** No LLM, no GPU, pure Python standard
library throughout.

**H1 and H2 are super-additive:** +0.041 and +0.056 alone, so together they
should give +0.097; measured, +0.108. Finding 3 explains why. The interaction
itself is worth writing up.

---

## Finding 1 — `ask_attribute="other"` is a universal extractor

```python
matches = [v for v in constraints
           if v not in disclosed
           and (attribute == "other" or classify_constraint(v) == attribute)][:2]
```

When `attribute == "other"` the boolean short-circuits to true, so it matches
**any** undisclosed constraint, returning at most 2 per turn.

Each session has at most 4 constraints in total:

```python
"hard_constraints": cleaned[:2],
"soft_preferences": cleaned[2:4] or cleaned[:1],
```

**So asking `other` twice drains all available information.** Cycling through
fixed attributes in a fixed order frequently hits
`"I don't have an additional preference for X."` and wastes the turn.

Measured: MTTC 5.15 to 4.18; boundary 0.60 to 0.90.

**Classification: this exploits a simulator mechanism; it is not a modelling
insight.** The report must label it as such.

---

## Finding 2 — in override scenarios, the "old preference to ignore" is a genuine constraint of the target product

```python
old_value = soft[-1]     # taken from the target's soft_preferences
new_value = hard[0]      # taken from the target's hard_constraints
message = f"Actually, ignore my earlier preference. What I need is: {new_value}."
```

The customer says "ignore my earlier preference", but `old_value` **is itself a
constraint of the target product**, not a distractor.

The brief's Pillar II says "Intent Override (**slot erasure** and rewriting)" --
**implementing that literally actively discards valid signal.**

Measured: intent_override HR@10 **0.467 to 0.867**.

**Classification: this one sits between the two.** "A preference the user
verbally negates does not necessarily contradict their real need, so state should
not be cleared unconditionally" is a transferable view of dialogue state
management; but its magnitude is amplified by this simulator. It can be written
up as a genuine insight, provided the boundary of its applicability in real
settings is stated.

---

## Finding 3 — a hit before the override does not score

```python
override_applied = sample["scenario_type"] != "intent_override"
...
if override_applied and target in ranked:   # only breaks/scores after the override
```

The official specification says the same thing explicitly:
> "An Intent Override session cannot convert before the new intent is sent."

Implications:
- The first 2 turns of these sessions are "free" and should be used purely to
  extract information.
- **This explains why override drops to 0.37 when H1 is used alone:** faster
  extraction means more accumulated terms, so clearing them loses more. H1 and H2
  therefore have to be enabled together, which is the source of the
  super-additivity.

---

## Finding 4 — intent routing needs no LLM

The opening message's template distinguishes the scenarios directly:

| turn-1 message feature | scenario |
|---|---|
| contains `"A key requirement is"` | buying |
| contains `"still exploring"` | browsing or boundary |
| otherwise | intent_override |

This satisfies the Dual-Track Routing the brief's Pillar I asks for, at zero
cost, zero latency and zero tokens.

---

## Signals not yet used

1. `average_rating` / `rating_number` -- targets are **real purchase records**, so
   a popularity prior is likely to lift MRR.
2. `price` -- constraints contain `budget around $X`, currently treated as an
   ordinary token.
3. `user_profile.preference_tags` -- entirely unused.
4. Candidates are taken as the top 10 with **no reranking stage**.

## The current bottleneck

```
score = 0.5 x 0.870 + 0.3 x 0.560 + 0.2 x 0.752 = 0.7536
                ^            ^              ^
        0.130 remaining  0.310 remaining  MTTC 3.48
        at most +0.065   at most +0.093   at most +0.050
```

The MRR ceiling is bounded by HR (MRR <= HR@10). If every hit session ranked
first, MRR would reach 0.870, i.e. **+0.093 -- the largest single block of
remaining headroom. Reranking is next.**
