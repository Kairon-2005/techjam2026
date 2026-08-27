# Phase 2B pre-registration

Written **before** the selection matrix runs. Recorded at `6f21d0d`, with the
planes implemented, 140 tests passing, and `dual_plane` still default-off.

## Phase ownership, fixed here so it is not claimed twice

| | Phase 2B (this) | Phase 4 (later) |
|---|---|---|
| CategoryIndex, FacetIndex | yes | — |
| Safe presence-aware filtering | yes | — |
| Route-specific budgets and data planes | yes | — |
| DenseIndex / embeddings | **no** | yes |
| Route-conditioned Weighted RRF | **no** | yes |
| Semantic / hybrid retrieval | **no** | yes |

Phase 2B builds a **lexical/category/facet** browsing plane. It is not
semantic browsing and will not be described as such.

## Parameters — fixed, not searched

There is **no free parameter grid**. Every value below is frozen for the whole
matrix; the only variation is the required ablation set. This is deliberate:
the failure mode this project keeps hitting is a threshold quietly re-tuned
until a scenario improves, and the cheapest way to not do that is to have
nothing to tune.

| parameter | value | why this value |
|---|---|---|
| `facet_min_coverage` | 0.30 | admits material .571, use_case .452, colour .388, style .305, brand .994, department .872; excludes size .209 and price .211, where silence is too common to read |
| `filter_min_confidence` | 0.9 | template evidence is 1.0, open-world extraction 0.6; only the former may filter |
| `buying_depth` | 1200 | ~2.4% of the catalog |
| `buying_min_candidates` | 60 | below this, relax rather than starve |
| `buying_rescue_budget` | 200 | unconditional, every turn |
| `browsing_depth` | 1200 | matched to buying so pool differences are topology, not budget |
| `browsing_expand_up` / `down` | 1 / 2 | one up reaches siblings, two down reaches leaves |
| `browsing_category_cap` | 15 | per shelf, exploration stage only |
| `browsing_neighbour_budget` | 120 | bounded popularity prior |
| `mixed_depth` | 900 | between the two |
| `mixed_category_budget` | 60 | |
| `filter_negative` | **off** | see H4 |

## Predicted candidate topology per route

From a functional probe on "Accessories Belts / genuine leather / black"
(composition only, no scores, not citable):

| route | unique candidates | shelves | entropy |
|---|---|---|---|
| buying | 326 (126 filtered + 200 rescued) | 3 | 1.09 |
| browsing | 1320 | 27 | 3.79 |
| mixed | 900 | 5 | 1.92 |

**H1 — topology.** Buying, Browsing and Mixed produce measurably different
candidate sets on the same query: different unique counts, different shelf
counts, different entropy. I am confident of this; it is close to a
restatement of the code.

## The prediction I expect to be uncomfortable

**H2 — ranking.** I predict Phase 2B produces **no significant gain on the
headline metrics**, and I am writing that down now rather than after.

The reason is structural and visible already. The rescue budget is
unconditional, because a filter that can permanently lose the target is
unacceptable. But carrying 200 unfiltered candidates on every turn means the
reranker still sees nearly everything it saw before, and in the probe the
buying/browsing top-10 overlap was 9/10 once rescue was on. **Guaranteeing
reachability and changing the ranking are in direct tension, and I have chosen
reachability.**

So the expected outcome is: topology differs (H1 holds), headline metrics move
by less than noise (H2), and by the standing rule Phase 2B therefore stays
**behind a feature flag, default off**, with Pillar I explicitly *not* claimed
as complete.

The honest place to look for a real gain is `clean_browsing` and
`vague_start`, where the query is thin and category expansion can surface
products BM25 never reaches. If anything wins, I predict it is there.

**H3 — rescue is load-bearing.** The `safe filter without rescue` arm will
score materially WORSE than with rescue, and will lose targets the rescue lane
recovers. That arm exists only to prove rescue is necessary and must never
become the default whatever it scores.

**H4 — hard-negative filter.** Not run by default. The Phase 1.5B finding was
that rejected attributes are scarce in the *current top 10*; that says nothing
about a 1200-deep pool. Coverage in the deep pool is measured first, as a
descriptive statistic. The filter is enabled only if that coverage is
non-trivial, and even then only measured, never defaulted on in this phase.

## Acceptance thresholds

Non-inferiority, all required:

| gate | threshold |
|---|---|
| compatibility `ask_policy="other"` | bit-exact `0.928708` |
| `dual_plane` off | reproduces `0.928508` |
| default clean with Phase 2B on | ≥ `0.926508` (drop ≤ 0.002) |
| clean HR@10 | ≥ `0.990` |
| clean MTTC | ≤ baseline + 0.05 turns |
| index memory | < 100 MB target, 160 MB hard |
| added p95 per-turn retrieval | < 10 ms, else explain and keep off |

Measured index cost at `6f21d0d`: CategoryIndex 0.30 s, FacetIndex 7.72 s,
**51.7 MB** combined. Build time is one-off and lazy; the compatibility path
pays none of it.

**Default-on requires** non-inferiority AND at least one reproducible gain in:
Buying Recall@100 or MRR; Browsing or `vague_start` Recall@100 or HR@10;
override/category robustness; or MTTC. Different topology alone is **not**
sufficient and will not be presented as Pillar I completion.

## Seeds

Selection seeds `(7, 8, 9, 10, 11)` — the same development seeds used
throughout. Robustness seeds `(12, 13, 14, 15, 16)` are reserved and are NOT
run in this matrix; they exist so a later confirmation has something unspent.
No consumed holdout and no negation v2 is touched.

## Ablations

`off` · `category only` · `facets, no hard filtering` · `safe filter + rescue`
(the candidate default) · `safe filter, no rescue` (diagnostic only) ·
forced-route Buying/Browsing/Mixed on identical queries.
