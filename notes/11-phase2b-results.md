# Phase 2B results

Every number below is from a leased, isolated, complete matrix at **`b228240`**,
rendered through `lab.report`, which filters on `citable()`. Predictions in
[notes/10-phase2b-prereg.md](10-phase2b-prereg.md) were written before any of it ran.

## Verdict: implemented, measured, **default OFF**

Non-inferiority fails by more than twenty times its tolerance. Under the
pre-registered rule that is decisive on its own, and it is decisive even
though Phase 2B produced the largest single-scenario gain this project has
measured.

| gate | required | actual | |
|---|---|---|---|
| compat `ask_policy="other"` | bit-exact `0.928708` | `0.928708` | pass |
| `dual_plane` off reproduces baseline | `0.928508` | `0.928508` | pass |
| clean with Phase 2B on | ≥ `0.926508` | **`0.883565`** | **FAIL (−0.0449)** |
| clean HR@10 | ≥ `0.990` | `0.995` | pass |
| clean MTTC | ≤ `2.09` | `1.70` | pass |
| index memory | < 100 MB | 51.7 MB | pass |
| added p95 retrieval | < 10 ms | +6.0 ms | pass |

## H1 — topology: CONFIRMED

Forced-route, identical queries, identical seeds:

| route | pool | shelves | entropy | exclusion | rescue carried | Recall@pool |
|---|---|---|---|---|---|---|
| off (Phase 2A) | 100.0 | — | — | — | — | 0.705 |
| buying | 462.1 | 9.07 | 1.51 | 0.777 | 197.8 | 0.973 |
| browsing | 1274.3 | 28.40 | 4.03 | 0 | 0 | 1.000 |
| mixed | 909.3 | 15.67 | 2.34 | 0 | 0 | 0.997 |

Three genuinely different candidate topologies, not three labels over one
pool. Buying is narrow and shelf-concentrated with a heavy exclusion rate;
browsing is four times wider across three times as many shelves; mixed sits
between them. This is the Pillar I gap Phase 2A left open.

## The result I did not predict

**`negative_preference` improves by +0.0889, roughly 4.5 sd.**

| | off | on | Δ |
|---|---|---|---|
| score | 0.700683 | **0.789530** | +0.0889 |
| HR@10 | 0.754 | 0.891 | +0.137 |
| MTTC | 4.13 | 2.48 | −1.65 |

This is the Phase 1.5B question answered. Three scenarios and a challenge set
all said the negative channel did nothing measurable, and the narrowed
conclusion was that rejected attributes are scarce *in the current top 10* and
that this "says nothing about their density in the deeper candidate pools a
category/facet plane will produce". A 660-candidate pool is that deeper pool,
and the channel that was inert over 100 candidates is worth 0.089 over it.

Refusing to generalise the scarcity finding was the right call, and the
narrowed version is what made this interpretable rather than surprising.

`contradiction` moves +0.0019 (inside noise) but its HR@10 rises 0.913 → 0.943
and MTTC falls 3.20 → 2.54. `vague_start` HR@10 rises 0.985 → 0.995 — the
place the pre-registration predicted a gain would appear if one did.

## H2 — ranking: CONFIRMED, and worse than predicted

Predicted "no significant gain on the headline metrics". Actual: a significant
LOSS on clean, −0.0449.

The mechanism is not the one I expected. Diagnostics (unleased, diagnostic
only, not citable):

| probe | clean score |
|---|---|
| Phase 2B on, as pre-registered | 0.883565 |
| …with injected candidates removed | 0.883817 |
| …with depths cut 1200 → 100 | 0.903699 |
| …with the category constraint removed | **0.928508** |

Removing the category constraint restores the baseline exactly. Two
independent specification errors:

1. **Depth was set 12× too deep.** The pre-registered 1200 was chosen to make
   pool differences "topology, not budget", but was never matched against the
   reranker's own operating point of `candidates = 100`. Roughly half the loss.

2. **The category constraint excludes 5.5% of targets.** Measured over all 200
   public sessions: the target is outside the selected shelves 11 times. The
   misses are systematic — the same product type filed under a different
   top-level branch:

   | stated | selected shelf | target actually on |
   |---|---|---|
   | `Men Pants` | `men > clothing > pants` | `sport specific clothing > golf > men > pants` |
   | `Women Shorts` | `women > clothing > shorts` | `sport specific clothing > running > women > shorts` |
   | `Tops & Tees Tanks & Camis` | `women > … > tanks & camis` | `novelty & more > … > tanks & camis` |

   `shelves()` keeps every equally *best-scoring* reading, which fixed the
   Men/Women split, but these duplicates score lower and are dropped. The
   rescue lane recovers them, which is why HR@10 holds at 0.995 while MRR
   collapses 0.839 → 0.667: the target is still reachable, just demoted.

**Retrieval recall went up while ranking went down.** Recall@pool 0.705 →
1.000: the target now reaches the candidate pool on every turn. Recall@50 and
@100 slip (0.582 → 0.549, 0.705 → 0.646) because the reranker cannot order
660 candidates as well as it orders 100. The bottleneck moved from retrieval
to ranking.

## H3 — the rescue lane is load-bearing: CONFIRMED

| | score | HR@10 |
|---|---|---|
| safe filter + rescue | 0.883565 | 0.995 |
| safe filter, **no** rescue | 0.875745 | **0.980** |

Removing rescue makes three targets in 200 unreachable at any rank. This arm
is diagnostic and must never become the default whatever it scores.

## H4 — hard-negative filter

Not run, as pre-registered, and the deep-pool coverage measurement that would
gate it is not done. `filter_negative` stays off. Given that the negative
*penalty* is now worth +0.089 over the deep pool, measuring the filter is the
single most promising item outstanding — but it needs its own pre-registration.

## The facet filter does nothing

| arm | clean |
|---|---|
| category only (facets disabled) | 0.885201 |
| facets, no hard filtering | 0.885201 |
| safe filter + rescue | 0.883565 |

Bit-identical across the first two. On the public set the facet filter never
changes an outcome: with presence-aware semantics and `filter_min_confidence`
at 0.9, almost nothing qualifies, and what does removes almost nothing. The
category constraint is doing all the work — and all the damage.

## What this does not establish

* That dual-track retrieval is wrong. Two fixable specification errors account
  for the entire regression, and one of them is arithmetic. It establishes
  that **this configuration** is not shippable.
* That the fixes will work. Correcting depth and adding cross-branch shelf
  matching are hypotheses, and they need their own pre-registration and a
  fresh matrix. Tuning them against these numbers would be tuning against the
  public set.
* Anything about the private set. Selection seeds `(7,8,9,10,11)` were used
  throughout; robustness seeds `(12,…,16)` remain unspent.
