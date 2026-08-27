# Phase 2 closure

**Phase 2 engineering is complete.** The default is Phase 2B-R3 arm C:

```
deep_funnel        = ON
category_plane     = OFF
starvation_bypass  = ON
```

Locked in `starter/agent.DEFAULTS` and asserted by
`tests/test_score_regression.ScoreRegressionTest.test_the_default_is_phase_2_arm_c`.
The shipped default now scores `0.931967` on the public set, up from
`0.928508`.

## Acceptance: 19 PASS / 1 FAIL

The 14 score and robustness gates all pass, and so do compat, the full suite,
memory, and starved latency. Full accounting is in
[notes/15-phase2b-r3-results.md](15-phase2b-r3-results.md) §6.

**One gate fails.** Non-starved p95 latency rises **33.5 ms → 44.3 ms, an
increase of 10.8 ms** (p50 11.5 → 19.4 ms). This is recorded as a **FAIL that
human review accepted as a deliberate trade-off**. It is not a pass, it must
not be restated as one, and any later summary that reports "all gates passed"
is wrong.

**Why it was accepted:** absolute p95 remains under 50 ms, and the cost buys
stable gains across the board — clean `+0.0035`, browsing MRR `+0.0272`,
boundary MRR `+0.0500` to a perfect `1.000`, and improvements on all three
robustness guards (`override_genuine` +0.0035, `override_category` +0.0035,
`contradiction` +0.0049). The starved path costs `+0.3 ms`, because the bypass
runs Phase 1's retrieval verbatim.

## What this is, and what it is not

This is a **unified deep lexical retrieval improvement**. With
`category_plane` off there is no route-specific data plane: Buying, Browsing
and Mixed all run the same deep BM25 retrieval through the same funnel, and
the routes differ only in configuration that no longer changes candidate
generation.

**It is not a completed Dual-Track, and Pillar I is not claimed.** Genuine
Buying/Browsing data-plane separation is formally deferred to **Phase 4 (Dense
Retrieval)**.

R2 established why the category plane is off: it is a net cost at *both*
retrieval depths (shallow 0.924983 → 0.903003; deep 0.931967 → 0.926881).
CategoryIndex, FacetIndex and category ranking weights are closed for tuning.

## FacetIndex is retained

`FacetIndex` stays in the build even though `category_plane` is off and R2
measured its filter as inert on this data (29.5 MB, 7.66 s one-off). Phase 3
may reuse it to estimate candidate-aware question utility — facet coverage and
value distribution over the live pool — so whether to remove it is a Phase 3
decision, taken after that question is answered rather than before.

## Provenance

Every number here comes from leased, isolated, `matrix_complete`, `citable()`
rows at `agent_sha256 12de984326b0ceb2`. Row keys in
[notes/15-phase2b-r3-results.md](15-phase2b-r3-results.md) §9.
