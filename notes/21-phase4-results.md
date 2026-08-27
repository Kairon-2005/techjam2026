# Phase 4 results — dual-track dense retrieval

All rows leased, isolated, `matrix_complete`, `citable()`. Matrix completed by
the takeover session; arms and parameters are unchanged from
[notes/20-phase4r1-prereg.md](20-phase4r1-prereg.md).

## Verdict: architecture proven, **dense stays default OFF**

The architecture test passes — this is the first phase to show a genuinely
independent Browsing data plane. **Neither dense arm passes its acceptance
gates**, so the route ships feature-off.

> Phase 4 architecture implementation complete; dense route feature-off
> pending gate failures and packaging/operational feasibility.

## Takeover audit

| check | result |
|---|---|
| P4 rows in ledger | **21**, all citable (10 inherited + 11 completed here) |
| `agent_sha256` | 1 distinct across all rows |
| `scenario_sha256` | 1 distinct |
| `catalog_sha256` | 1 distinct |
| `dataset_sha256` | 2 — public set and supplementary dev, as expected |
| `agent_commit` | 2 (`ac473ad`, `7e17a31`) resolving the same agent blob |
| `p4-D5` abort | `supplementary_dev × A`, 0 rows journalled, **covered** by citable rerun `b870d54461d44fcf` |
| default | dense OFF throughout |

The earlier verbal claim of "13 rows" was wrong — that was a Phase 3B count.
The ledger holds 21 P4 rows.

## A/B/C matrix

| scenario | A lexical | B dense-only | C RRF |
|---|---|---|---|
| clean | 0.932067 | **0.932942** | 0.930629 |
| vague_start | 0.919247 | 0.919247 | 0.919247 |
| uncooperative | 0.831926 | **0.834373** | 0.830488 |
| override_genuine | 0.925980 | **0.926855** | 0.924542 |
| override_category | 0.931867 | **0.932742** | 0.930429 |
| contradiction | **0.814187** | 0.808136 | 0.812750 |
| supplementary_dev | 0.441608 | 0.436305 | **0.442015** |

### Official slices (clean)

| arm | buying | browsing | boundary | intent_override |
|---|---|---|---|---|
| A | HR 0.988 / MRR 0.851181 | 1.000 / 0.809375 | 1.000 / **1.000000** | 1.000 / 0.922222 |
| B | HR 0.988 / MRR 0.851181 | 1.000 / **0.834583** | 1.000 / **0.870000** | 1.000 / 0.922222 |
| C | HR 0.988 / MRR 0.851181 | 1.000 / 0.797396 | 1.000 / **1.000000** | 1.000 / 0.922222 |

**Buying MRR is bit-identical across all three arms.** Dense is structurally
unreachable from the buying plane — `_dense_source` is called only from
`_plane_browsing` and `_plane_mixed` — so this is a property of the code, not
a coincidence of the data.

## The Boundary regression, answered directly

**Yes, RRF recovers it.** Boundary MRR is `1.000000` under A, drops to
`0.870000` under dense-only B, and returns to `1.000000` under C.

Boundary sessions are the 10 where the customer refuses to answer until asked
one specific attribute. Dense-only replaces the lexical ordering wholesale on
browsing/mixed turns, and on those sessions the correct item is present but
demoted — HR stays 1.000 while MRR falls 0.13. RRF keeps the lexical ranking
as one of two fused inputs, which is enough to restore it exactly.

This is reported as a first-class result, not a rounding note: it is the
largest single-slice movement in the phase, and it is the reason the best
overall arm is not simply adopted. **No post-hoc weight search was run to
repair it** — `rrf_weight_lexical` and `rrf_weight_dense` remain 1.0/1.0 as
pre-registered.

## Gates, against frozen Phase 3 C (arm A)

| gate | limit | B | | C | |
|---|---|---|---|---|---|
| official score | ≤0.002 | +0.000875 | PASS | −0.001438 | PASS |
| official HR@10 | ≤0.005 | 0 | PASS | 0 | PASS |
| official MRR | ≤0.003 | +0.003583 | PASS | **−0.004792** | **FAIL** |
| Buying MRR | ≤0.005 | 0 | PASS | 0 | PASS |
| Browsing MRR | ≤0.005 | +0.025208 | PASS | **−0.011979** | **FAIL** |
| Boundary HR@10 | ≤0.010 | 0 | PASS | 0 | PASS |
| Intent Override HR@10 | ≤0.010 | 0 | PASS | 0 | PASS |
| vague_start | ≤0.010 | 0 | PASS | 0 | PASS |
| uncooperative | ≤0.010 | +0.002447 | PASS | −0.001438 | PASS |
| override_genuine | ≤0.005 | +0.000875 | PASS | −0.001438 | PASS |
| override_category | ≤0.005 | +0.000875 | PASS | −0.001438 | PASS |
| contradiction | ≤0.005 | **−0.006051** | **FAIL** | −0.001438 | PASS |
| supplementary score (veto) | >0.010 rejects | −0.005303 | PASS | +0.000407 | PASS |
| supplementary HR@10 (veto) | >0.010 rejects | −0.010000 | **boundary** | 0 | PASS |

**B fails the contradiction guard** at −0.006051 against a 0.005 limit.

**C fails official MRR** (−0.004792 vs 0.003) **and Browsing MRR** (−0.011979
vs 0.005) — RRF buys Boundary back by diluting exactly the slice dense was
added to improve.

The supplementary HR@10 case for B lands **exactly on** its threshold: 0.560 →
0.550, a drop of precisely 0.010. The rule rejects a drop *greater than* 0.010,
so it does not trigger the veto. A naive float comparison reports it as a
failure because `0.55 - 0.56 == -0.010000000000000009`; the exact decimal drop
is 0.010 and the veto does not fire. It is recorded as a boundary case rather
than silently rounded either way.

## Architecture evidence — the test that passes

| | value |
|---|---|
| dense candidates returned per dense turn | 300.0 |
| of which **dense-only** (BM25 never surfaced) | **283.16 (94.4%)** |
| overlapping with BM25 | 16.84 (5.6%) |
| turns invoking the dense route | 23.5% |
| Buying MRR delta, all arms | 0.000000 |

**94.4% of dense candidates are unreachable by the lexical route.** This is not
a renamed BM25 list, which was the explicit failure mode to avoid. Browsing
receives material dense-origin candidates; Buying receives none by
construction.

## Cold start — measured, and the R0 note corrected

Fresh process, empty caches, `trace=False`:

| | lexical | dense |
|---|---|---|
| import | 0.02 s | 0.01 s |
| Agent init (catalog + FTS) | 5.32 s | 5.35 s |
| first Browsing turn | 0.10 s | **4.78 s** |
| cold total to first reply | **5.44 s** | **10.14 s** |
| peak RSS | 451.6 MB | 539.5 MB |

**The R0 note's "~137 s" dense build is wrong by roughly 28×.** Measured
directly at the R1 configuration (`dense_dim=32`, `dense_seed=20260827`, all
50,000 documents): **4.83 s**. The correction makes dense *more* feasible than
R0 claimed, not less, and it is recorded because an unchecked figure that
flatters a decision is as dangerous as one that damns it.

**A finding independent of dense:** both modes pay **6.19 s** on the first
turn that states a constraint, building `FacetIndex` lazily. That cost is in
the *shipped* default today, and `category_plane` being off does not avoid it,
because `_eligible_filters` reads the facet index regardless. It is the
largest single cold-start cost in the product and nothing in Phase 4 caused it.

**No offline prebuilt/load path exists** for the dense artifact. Since dense
ships off, this is not a blocking failure now, but it is a hard prerequisite
for any future default-ON.

## Uninstrumented latency — `trace` and `trace_candidates` both OFF

Warm, after every lazy index is built:

| | lexical p50 / p95 | dense p50 / p95 |
|---|---|---|
| buying turns | 16.23 / 29.29 ms | 16.28 / 29.30 ms |
| browsing turns | 13.60 / 26.96 ms | 14.04 / **28.02** ms |
| steady RSS | 660.9 MB | 758.9 MB |

Dense costs **+1.06 ms p95 on browsing** and nothing measurable on buying.
Every figure is far inside the 100 ms p95 gate. The ~90 ms p95 quoted earlier
in the phase was trace-on and is not the cost of the product.

Dense adds **98 MB** resident over lexical. Combined index memory stays under
the 160 MB gate.

## Corrections to this note's own feasibility figures

Two numbers above were re-measured and are wrong as first written. Both are
recorded here rather than edited away.

### Dense build time is order-dependent, and degrades badly

| when dense is built | build time |
|---|---|
| fresh process, before `FacetIndex` exists | **4.66 s** |
| after `FacetIndex` is already resident | **92.17 s** |

A 20× difference from ordering alone. The cause is memory pressure: the
catalog plus `CategoryIndex` plus `FacetIndex` already hold ~660 MB, and the
dense build then peaks a further ~110 MB of Python allocation on top.

The shipped route ordering happens to hit the fast case — dense fires only on
browsing/mixed turns, which precede any constraint, and `_retarget` never
returns a session to browsing once it has firmed up — so a real session builds
dense *before* facets. **That is a fragile property of route ordering, not a
guarantee**, and "4.83 s" should never have been reported as *the* build cost.

### Resident memory: three numbers measuring three different things

| method | value | what it measures |
|---|---|---|
| `tracemalloc` retained | **19.9 MB** | Python objects the index still holds |
| `tracemalloc` peak | **109.7 MB** | including transient build allocation |
| process high-water growth | **+122.3 MB** | `ru_maxrss` across the dense turn |
| cross-process steady delta | **98 MB** | two processes at the end of a run |
| R0's "resident artefacts" | 39.4 MB | **not reproducible at `dense_dim=32`** |

**The operational figure is ~98–122 MB, and that is what the report uses.**
R0's 39.4 MB is withdrawn: no measurement method reproduces it at the R1
configuration, so it is not carried forward as if it were a smaller estimate
of the same quantity.

**The 160 MB gate is not comfortable headroom and will not be described as
such.** It counts index artefacts, which is narrower than operational memory:
the dense process reaches ~539 MB peak RSS cold and ~759 MB steady after a
240-turn run. The gate passes as written; the process is not small.

## Decision

**Dense default OFF.** Blocking items, precisely:

1. **B** (best overall score, +0.000875, and the only arm that improves
   Browsing MRR) fails the **contradiction guard** at −0.006051, and carries
   the **Boundary MRR −0.130** trade-off.
2. **C** recovers Boundary exactly but fails **official MRR** and **Browsing
   MRR**, losing the gain dense was added to produce.
3. No offline prebuilt artifact path, required before any default-ON.

Per the stop rule: no further changes to the dense algorithm, dimension, seed,
RRF weights or the category plane. The implementation and its architecture
evidence are retained feature-off.
