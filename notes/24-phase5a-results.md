# Phase 5A results — per-attribute lazy facet construction

All rows leased, isolated, `matrix_complete`, `citable()`, at **`2b52f73`**.

## Verdict: **adopt B**, with one gate met in substance but not as worded

## Equivalence — bit-exact everywhere

| scenario | A | B | Δ |
|---|---|---|---|
| clean | 0.932067 | 0.932067 | `+0.000000000` |
| vague_start | 0.919247 | 0.919247 | `+0.000000000` |
| uncooperative | 0.831926 | 0.831926 | `+0.000000000` |
| override_genuine | 0.925980 | 0.925980 | `+0.000000000` |
| override_category | 0.931867 | 0.931867 | `+0.000000000` |
| contradiction | 0.814187 | 0.814187 | `+0.000000000` |
| supplementary_dev | 0.441608 | 0.441608 | `+0.000000000` |

Official slices identical: buying HR 0.988 / MRR 0.851181 · browsing 1.000 /
0.809375 · boundary 1.000 / 1.000000 · intent_override 1.000 / 0.922222.
Compatibility anchor `ask_policy="other"` = `0.928708`, exact.

This is equivalence by construction, not coincidence: the same values are read
from the same inputs with the same vocabularies, only built later and only if
needed.

## Operational effect

Fresh process, `trace=False`, four-turn session:

| | A | B |
|---|---|---|
| Agent init | 5.20 s | 5.42 s |
| **turn 1 — first constraint** | **6.363 s** | **1.204 s** |
| turn 2 | 0.020 s | 1.174 s |
| turns 3–4 | 0.020 / 0.030 s | 0.020 / 0.018 s |
| **total facet cost** | **6.363 s** | **2.378 s** |
| facets built | 7 of 7 | **2 of 7** (`material`, `color`) |
| warm p50 / p95 | 18.37 / 19.31 ms | 18.97 / 22.57 ms |

Retained memory, measured with `tracemalloc` rather than RSS high-water:

| | retained |
|---|---|
| index object, nothing built | 0.00 MB |
| typical session (`material` + `color`) | **7.26 MB** |
| all seven, i.e. the old eager cost | **29.50 MB** |

**B saves 22.24 MB**, so "no new memory cost" is met with room to spare. An
earlier RSS reading suggested B used ~15 MB *more*; that was `ru_maxrss`
high-water noise across a 60-session warm loop and is withdrawn.

## Gates

| gate | result |
|---|---|
| official score / HR@10 / MRR / MTTC bit-exact | **PASS** |
| `ask_policy="other"` anchor exact | **PASS** (`0.928708`) |
| no robustness veto | **PASS** (all bit-exact) |
| no supplementary veto | **PASS** (bit-exact) |
| no new memory cost | **PASS** (−22.24 MB) |
| global `FacetIndex` not built in score-default | **PASS** — 2 of 7 facets; the seven-facet global build never occurs |
| remove the ~6.19 s stall | **PARTIAL — see below** |
| warm post-first-turn p95 < 100 ms | **PASS** (22.57 ms) |
| all tests pass | **PASS** (192) |

### The one gate not met as worded

The gate says *remove* the stall. B **reduces it 5.3× and splits it**: the
first constraint turn falls from 6.363 s to 1.204 s, but turn 2 rises from
0.020 s to 1.174 s when it first needs `color`. Total facet cost across the
session falls from 6.363 s to 2.378 s.

So the worst single turn improves by 5.2 s and the session total by 4.0 s, but
a user still meets a ~1.2 s pause on each of the first two constraint turns.
**That is a reduction, not an elimination, and this note will not describe it
as removed.** Eliminating it entirely would require either precomputing facets
at init — which moves the cost rather than removing it, and lengthens a
5.2 s cold start — or an artefact cache, which this phase forbids.

## Correction to the premise

The task assumed question utility reuses `FacetIndex`. It does not:
`_facet_coverage()` reads `ATTR_VOCAB` against catalog text over the bounded
30-item window. The hypothesis as handed to this phase was already true in
shipped code; the cost came entirely from the safe-filter path
(`_eligible_filters`, `_safe_pool`).

The literal bounded-window replacement was **not** implementable equivalently:
`_safe_pool`'s relaxation counts a subset of all 50,000 products, and
`hard_ok`/`match` ask catalog-wide questions a window cannot answer. The filter
is also not inert — disabling it moves clean to `0.933042` — so it could not
simply be skipped. Arm B was therefore implemented as per-attribute deferral,
which is exactly equivalent. **Arm C was not needed and was not written.**
