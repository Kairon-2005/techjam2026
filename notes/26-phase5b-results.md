# Phase 5B results — module split

Five commits, suite green at each. Final state `9424cae`; bit-exact matrix
leased and isolated at that commit, compared against pre-split `0ce1bfc`.

## Verdict: adopt. Bit-exact, no performance regression.

## Structure

`starter/agent.py` was **2,466 lines**. Now:

| module | lines | role |
|---|---|---|
| `evidence.py` | 431 | parsing, slots, polarity, hardness — package leaf |
| `catalog.py` | 667 | corpus, FTS5, category / facet / dense indexes |
| `dialogue.py` | 434 | `DialogueMixin` — state, override, starvation, questions |
| `retrieval.py` | 568 | `RetrievalMixin` — pool, planes, dense/RRF, funnel, rerank |
| `agent.py` | 546 | `DEFAULTS`, `PROFILES`, composition, re-exports |

`class Agent(RetrievalMixin, DialogueMixin)`; MRO asserted by test.

## What the split claims, and what it does not

**The import graph is acyclic** and tested: `evidence` imports nothing from
the package and loads in a subprocess with no catalog present; `catalog`
imports only `evidence`; **the two mixins import neither each other nor
anything that would let them**.

**The domain graph is still cyclic, deliberately.** All five original
bidirectional calls survive — `dialogue → retrieval` (`_route_cfg`,
`_category_on`) and `retrieval → dialogue` (`_uncredible` ×2, `_terms`). Each
mixin's header lists the host capabilities it reaches through `self` and
names the ones that land in the other mixin.

Two tests assert those edges **still exist and are still declared**, so they
cannot go quiet and be mistaken for decoupling. This was a behaviour-preserving
boundary-tidying exercise; strict interfaces, dependency inversion and actually
removing the bidirectional calls are a separate phase with its own budget.

## Equivalence — bit-exact

| scenario | pre-split | post-split | Δ | row key |
|---|---|---|---|---|
| clean | 0.932067 | 0.932067 | `+0.000000000` | `d616b70c0c937e6a` |
| vague_start | 0.919247 | 0.919247 | `+0.000000000` | `b2ccc9bfe3414a2a` |
| uncooperative | 0.831926 | 0.831926 | `+0.000000000` | `33420cbb7cd486b6` |
| override_genuine | 0.925980 | 0.925980 | `+0.000000000` | `59a313042cf08229` |
| override_category | 0.931867 | 0.931867 | `+0.000000000` | `d696ce5b4cfa39ba` |
| contradiction | 0.814187 | 0.814187 | `+0.000000000` | `9164c4a9cae8e6f8` |
| supplementary_dev | 0.441608 | 0.441608 | `+0.000000000` | `f2854c67b33e7b54` |
| compat anchor | 0.928708 | 0.928708 | `+0.000000000` | `eb855bf68bd13da7` |

All four official slices identical.

## Performance — no regression

| | pre-split | post-split |
|---|---|---|
| Agent init | 5.42 s | 5.31 s |
| first constraint turn | 1.161 s | 1.155 s |
| second turn | 1.177 s | 1.170 s |
| warm p50 / p95 | 19.03 / 19.61 ms | 19.02 / 19.60 ms |
| final RSS | 459.5 MB | 461.5 MB |
| facets built | 2 of 7 | 2 of 7 |

Every figure is inside run-to-run noise, measured in an isolated worktree at
`0ce1bfc` against `HEAD`, same script, same machine.

## Two hidden dependencies the split surfaced

Both were invisible while one namespace held everything, and both broke tests
the moment the boundary existed:

1. **`_DenseIndex` uses `time`** — the extracted `catalog.py` did not import
   it, and every dense test raised `NameError`.
2. **`_compose` calls `_clean`**, a catalog helper reached from `dialogue`.

Neither is a behaviour change; both are couplings that are now written down.

## `PROFILES`

`score_default` = `{}` and `showcase_dense` = arm B's three dense keys. Labels
over `DEFAULTS` only — a test asserts that naming the showcase profile does
not arm dense, and `DEFAULTS["dense_browsing"]` remains `False`.

## Tests

192 → **202**. New: public-contract resolution (20 names), `DEFAULTS`
unchanged, `PROFILES` inert, MRO, evidence-is-a-leaf, catalog-depends-only-on-
evidence, mixins-never-import-each-other, evidence-imports-standalone, and the
two domain-graph tests above.
