# 03 — Data and bottleneck measurements

## A. `public_set.jsonl` schema

Each session has only 6 fields, and **the agent can see only `user_profile`**:

```json
{"sample_id": "public_0001",
 "scenario_type": "buying",              // for evaluation; the agent cannot see it
 "category_bucket": "clothing",          // identical for all 200 -> zero information
 "difficulty_bucket": "easy",            // see below; zero information
 "ground_truth": {"parent_asin": "..."}, // for evaluation
 "user_profile": {"average_prior_rating": 5.0,
                  "preference_tags": ["fit","comfort","durability"],
                  "purchase_frequency": "3-4 prior purchases",
                  "rating_style": "usually positive",
                  "summary": "..."}}
```

`intent_card` and `behavior` are **not in the file**; they are generated
deterministically on the fly from the target product by
`materialize_hidden_fields()`.

### `difficulty_bucket` is fully determined by `scenario_type`, so it adds nothing

| scenario | difficulty | n |
|---|---|---|
| buying | easy | 80 |
| browsing | medium | 80 |
| boundary | medium | 10 |
| intent_override | hard | 30 |

### The personalization signal in `user_profile` is very weak

- `purchase_frequency`: all 200 are `"3-4 prior purchases"` -- **a constant, and
  useless.**
- `preference_tags`: only 9 generic tags (fit 163, material 154, comfort 144,
  style 101, durability 47, performance 26, warmth 18, weather 12, general
  shopping 1), **with no direct correspondence to the target product.**
- `rating_style` / `average_prior_rating`: equally indirect.

**Conclusion: do not spend time on personalization.** This is a negative result,
but the time saved goes into ranking.

## B. Intent card structure

- **Exactly 4 constraints per session** (the distribution is `{4: 200}`), which
  confirms that asking `other` twice drains everything.
- Constraint strings are short: median **14** characters, mean 35.8.
- Examples: `Material:alloy`, `leather`, `100% Leather`, `Imported`,
  `Buckle closure`, `Water Resistant`, `3 Year Battery`, `Day / Date Indicator`,
  `Stainless Steel Band`.
- Many are extremely generic (`Imported` appears in a huge number of Amazon
  listings), so **IDF weighting matters.**

### `classify_constraint` category distribution (800 constraints)

| category | count |
|---|---|
| feature | 404 |
| material | 302 |
| color | 60 |
| style | 19 |
| size | 11 |
| use_case | 4 |
| **category / brand / budget** | **0** |

So asking about `category`, `brand` or `budget` is **100% wasted**. This is why
cycling through fixed attributes performs badly.

### The turn on which an override fires

`{3: 12, 4: 18}` -- a mean of 3.6.

## C. How the bottleneck was computed

```
score = 0.50*HR@10 + 0.30*MRR + 0.20*Eff        Eff = clip((11 - MTTC)/10, 0, 1)
```

Measured for the best configuration at the time:

```
HR@10 = 0.870   -> 0.50 x 0.870    = 0.43500
MRR   = 0.560   -> 0.30 x 0.560232 = 0.16807
MTTC  = 3.475   -> Eff = (11-3.475)/10 = 0.7525 -> 0.20 x 0.7525 = 0.15050
                                                            total = 0.75357  (checks out)
```

Upper bound of each component:

| component | current | bound | basis | at most +score |
|---|---|---|---|---|
| HR@10 | 0.870 | 1.000 | definition | +0.0650 |
| MRR | 0.560 | 0.870 | **MRR <= HR@10** (a miss scores 0; a hit scores at most 1/1) | +0.0929 |
| Eff | 0.752 | 0.961 | see below | +0.0417 |

**Theoretical minimum MTTC:** the 170 non-override sessions can hit on turn 1 at
the earliest; the 30 override sessions cannot convert before the new intent is
sent, and the firing turns measured are `{3:12, 4:18}`:

```
min MTTC = (170*1 + 12*3 + 18*4) / 200 = 278/200 = 1.390
max Eff  = (11 - 1.390)/10 = 0.9610
```

The three cannot be added independently (the MRR bound rises with HR). The
**joint upper bound** with HR=1, MRR=1, MTTC=1.39 gives
score = 0.5 + 0.3 + 0.192 = **0.992**.

## D. The decisive measurement: the bottleneck is 100% ranking, not retrieval

### Rank distribution of hit sessions (n=174)

| rank | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | miss |
|---|---|---|---|---|---|---|---|---|---|---|---|
| count | 88 | 20 | 16 | 10 | 12 | 9 | 4 | 7 | 6 | 2 | 26 |

### Best position of the target within a larger BM25 candidate set

| N | 10 | 20 | 50 | 100 | **200** | 2000 |
|---|---|---|---|---|---|---|
| recall@N | 0.870 | 0.915 | 0.970 | 0.995 | **1.000** | 1.000 |

**All 200 targets are recalled within BM25's top 200, without exception, on the
public development set.**

For the 26 sessions that currently fail to reach the top 10, the best positions
are: 9 in 11-20, 11 in 21-50, 5 in 51-100, and 1 in 101-500.

### Conclusion

> **Retrieval is solved on this set. The remaining 0.239 of score lies entirely
> in the ranking layer.**

A reranker that were perfect over the top 200 would take HR@10 to 1.000 and MRR
to 1.000 simultaneously, and score to 0.992. That settles what to do next:
**build the reranker, leave recall alone.**

Note the scope: this is a measurement on the **public development set**, and it
is not a claim that retrieval is solved for any other corpus.

## E. The exact answer on the network question

All the relevant text in the repository (`docs/submission_rules.md`):

> L59 — "For official final scoring, organizer policy **may** disable network access."
> L62 — "your submission must clearly document whether it requires network access"
> L63 — "if your system has an offline fallback, describe it"
> L101 — "The organizer **reserves the right** to run your submission under CPU,
> memory, timeout, and network restrictions."

**The accurate statement is: the network is not certainly disabled; the organizer
reserves the right to disable it.** The repository contains no organizer-only
files (the `organizer/JUDGING_RUNBOOK.md` and similar mentioned in the brief were
not shipped with the participant kit), so nothing further can be confirmed from
the code.

From a decision-theoretic view this is a **highly asymmetric bet**:

| | network actually disabled | network actually available |
|---|---|---|
| we go fully local | scores normally | loss = the increment an LLM could have added (**bounded**) |
| we depend on an API | the agent raises; the spec says explicitly that "Exceptions, invalid output, and timeouts **may count as a miss**" -> **near-zero score (catastrophic)** | scores normally |

Combined with section D -- retrieval is already at ceiling, everything left is
ranking, and ranking can be done with cheap features -- **this insurance is
close to free.**

Decision: the main path is fully local, zero-network, CPU-only; a model is only
a switchable enhancement with an offline fallback, declared in the report as the
rules require.

**To confirm:** the Track 4 workshop Q&A on 8/28 16:00-16:45; ask the organizer
directly. This is the first question on the list.
