# Phase 2B-R3 results — starvation-aware deep funnel

All rows leased, isolated, `matrix_complete`, and `citable()`. Predictions in
[notes/14-phase2b-r3-prereg.md](14-phase2b-r3-prereg.md) were written before the
implementation existed.

Three harness commits appear (`7f03ab6`, `e56132b`, `4b00e4a`) because two
shards hit a wall clock and the ledger fix landed between them. **The measured
artefacts are identical across all 19 rows** — one `agent_sha256`
(`12de984326b0ceb2`), one `scenario_sha256` (`5c4efe2d76d10a30`), one dataset
and one catalog hash — so the comparison is sound. The differing commits touch
only `lab/lease.py`'s dirty-set prefix and the ledgers, neither of which can
reach a measurement.

## Verdict

**The hypothesis holds. 14 of 14 score and robustness gates pass. One
efficiency gate fails: non-starved turns cost +10.8 ms p95.**

Recommendation: adopt as candidate default, with the latency cost disclosed —
but that single judgment is the reviewer's, because it is the only gate whose
wording is qualitative ("no measurable significant increase") rather than a
number, and it decides default-ON.

## 1. The three arms — `clean`

| arm | score | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| A feature-off | 0.928508 | 0.995 | 0.839361 | 2.040 |
| B deep funnel | **0.931967** | 0.995 | 0.852556 | 2.065 |
| C +starvation bypass | **0.931967** | 0.995 | 0.852556 | 2.065 |
| compat `ask_policy="other"` | 0.928708 | 0.995 | 0.839361 | 2.030 |

B reproduces R2's configuration C to the last digit, so the pre-registered
stop condition did not trigger and the flag split is sound. C is bit-identical
to B on `clean`: the bypass never fires there, exactly as predicted.

## 2. Official slices

| slice | A | B | C |
|---|---|---|---|
| buying | HR 0.988 / MRR 0.851597 | HR 0.988 / MRR 0.851181 | HR 0.988 / MRR 0.851181 |
| browsing | HR 1.000 / MRR 0.782222 | HR 1.000 / MRR **0.809375** | HR 1.000 / MRR **0.809375** |
| boundary | HR 1.000 / MRR 0.950000 | HR 1.000 / MRR **1.000000** | HR 1.000 / MRR **1.000000** |
| intent_override | HR 1.000 / MRR 0.922222 | HR 1.000 / MRR 0.922222 | HR 1.000 / MRR 0.922222 |

Two official gains carried intact from B to C: browsing MRR **+0.0272**,
boundary MRR **+0.0500** to a perfect 1.000. R2's `intent_override` HR
regression is gone — that was the category plane, now off.

## 3. Starvation — the hypothesis under test

| scenario | A | B | C | C−A |
|---|---|---|---|---|
| `uncooperative` | 0.833266 | **0.677004** | 0.831846 | −0.0014 |
| `vague_start` | 0.917534 | **0.887845** | 0.918847 | **+0.0013** |

The mechanism, from telemetry, is unambiguous:

| scenario | arm | starved % | bypass % | starved pool | starved Recall@pool |
|---|---|---|---|---|---|
| `uncooperative` | A | 33.4 | 0 | 998.9 | 0.862 |
| `uncooperative` | B | 55.7 | 0 | **100.0** | **0.080** |
| `uncooperative` | C | 34.4 | 34.4 | **998.2** | **0.870** |
| `vague_start` | A | 3.0 | 0 | 1000.0 | 0.600 |
| `vague_start` | B | 10.0 | 0 | **100.0** | **0.000** |
| `vague_start` | C | 3.0 | 3.0 | **1000.0** | **0.600** |

B truncates the widened pool to the funnel cap and starved Recall@pool
collapses to 0.080 and 0.000 — on `vague_start` the target was in the widened
pool and the funnel discarded it **every time**. C restores the pool (998.2,
1000.0) and recall returns to baseline (0.870, 0.600).

B also *starves more often* — 55.7% of turns against A's 33.4% — because
truncation produces thin result sets, which trips the starvation detector
again the next turn. C's rate matches A's. The funnel was manufacturing the
condition it then handled badly.

## 4. Guards

| scenario | A | B | C | C−A |
|---|---|---|---|---|
| `override_genuine` | 0.922134 | 0.925800 | 0.925593 | +0.0035 |
| `override_category` | 0.928308 | 0.931767 | 0.931767 | +0.0035 |
| `contradiction` | 0.809352 | 0.789590 | 0.814207 | +0.0049 |

All three improve. `contradiction` is notable: B regresses it (−0.0198) and
the bypass recovers *and* exceeds baseline, so contradiction sessions were
starving too.

## 5. Latency and memory

Measured with `trace_candidates` **off** — storing up to 1000 asins per turn
is lab instrumentation, not the shipped path, and it dominated the first
measurement (p95 57–93 ms with it on, 29–44 ms without). The instrumented
numbers were discarded for this gate.

| | A | C | Δ |
|---|---|---|---|
| `clean` p50 | 11.5 ms | 19.4 ms | +7.9 ms |
| `clean` p95 (all non-starved) | 33.5 ms | 44.3 ms | **+10.8 ms** |
| `uncooperative` **starved** p95 | 21.9 ms | 22.2 ms | **+0.3 ms** |
| `uncooperative` unstarved p95 | 31.3 ms | 41.9 ms | +10.6 ms |

The starved path costs nothing, which follows directly from it being Phase 1's
path verbatim. The cost is on ordinary turns and is the unavoidable price of
retrieving 1200 candidates instead of 100.

Index memory, rebuilt at this commit: CategoryIndex 0.32 s / 24.6 MB,
FacetIndex 7.66 s / 29.5 MB, **54.1 MB** total against a 100 MB target and
160 MB hard limit. Both are built once per catalog and shared process-wide.
No unbounded session state: the trace log is per session and bounded by turn
count, and no candidate cache is retained between turns.

**Worth flagging:** with `category_plane` off, the FacetIndex is still built —
`_eligible_filters` reads it — and R2 measured the facet filter as inert on
this data. That is 29.5 MB and 7.66 s of startup buying nothing measurable in
the candidate default. Removing it is a real simplification, not attempted
here because it is outside R3's single hypothesis.

## 6. Gates

| gate | required | actual | |
|---|---|---|---|
| clean score | ≥ 0.928508 | 0.931967 | PASS |
| clean HR@10 | ≥ 0.990 | 0.995 | PASS |
| clean MTTC | ≤ 2.09 | 2.065 | PASS |
| browsing MRR | ≥ 0.792 | 0.809375 | PASS |
| buying MRR | ≥ 0.849 | 0.851181 | PASS |
| boundary MRR | = 1.000 | 1.000000 | PASS |
| intent_override HR@10 | = 1.000 | 1.000 | PASS |
| `uncooperative` score drop | ≤ 0.010 | +0.001420 | PASS |
| `uncooperative` HR@10 drop | ≤ 0.010 | +0.0040 | PASS |
| `vague_start` score drop | ≤ 0.010 | −0.001313 (gain) | PASS |
| starved Recall@pool ≫ B | — | 0.870 vs 0.080; 0.600 vs 0.000 | PASS |
| C starved pool not capped at 100 | — | 998.2 / 1000.0 | PASS |
| `override_genuine` regression | ≤ 0.005 | −0.003459 (gain) | PASS |
| `override_category` regression | ≤ 0.005 | −0.003459 (gain) | PASS |
| `contradiction` regression | ≤ 0.005 | −0.004855 (gain) | PASS |
| compat bit-exact | 0.928708 | 0.928708 | PASS |
| full suite | all pass | 156 tests | PASS |
| starved p95 vs baseline | < +10 ms | **+0.3 ms** | PASS |
| memory | < 100 MB | 54.1 MB | PASS |
| **non-starved latency** | no measurable significant rise | **+10.8 ms p95, +7.9 ms p50** | **FAIL** |

## 7. Recommendation

Adopt `deep_funnel` ON, `category_plane` OFF, `starvation_bypass` ON as the
candidate default, with the latency cost stated plainly.

This is a **score-oriented deep lexical retrieval improvement. It is not a
completed dual-track.** With the category plane off there is no route-specific
data plane at all: all three routes run the same deep BM25 retrieval and the
same funnel. Genuine Buying/Browsing separation is **deferred to Phase 4
(Dense Retrieval)**, and Pillar I remains unclaimed.

The one reason not to adopt is the +10.8 ms. Against it: absolute p95 is
44.3 ms, the starved path is free, and it buys +0.0035 clean, +0.0272 browsing
MRR, +0.0500 boundary MRR and improvements on all three guards. I am not
treating a qualitative gate as self-evidently met when it decides the default.

## 8. Aborted runs

Two shards were killed by a 10-minute wall clock and journalled nothing;
both are recorded in `lab/invalidations.jsonl` as `run_aborted` with zero row
keys, so the ledger shows the attempts rather than a silent gap.

* `r3-S2` — `uncooperative` + `vague_start` × 3 arms, 13:42.
* `r3-S3c` — `contradiction` × 3 arms, 14:15.

Both re-ran successfully split one scenario (or one arm) per shard. Recording
the first abort exposed a real defect: the lease's dirty check excluded
`lab/results.jsonl` but not `lab/invalidations.jsonl`, so documenting a failed
experiment blocked the re-run of that same experiment. Fixed in `e56132b`.

## 9. Row keys

| row_key | tag | scenario | arm | score |
| `69a2c399aa4d660d` | r3-clean-official | clean | A | 0.928508 |
| `52c9677d1e2d7614` | r3-clean-official | clean | B | 0.931967 |
| `82ddade1d2cec92b` | r3-clean-official | clean | C | 0.931967 |
| `3ee70053945fed28` | r3-clean-official | clean | compat | 0.928708 |
| `dbf3dfe266aef82e` | r3-guards | contradiction | A | 0.809352 |
| `63bda1b81cee9119` | r3-guards | contradiction | B | 0.789590 |
| `72fe00a0f4cecb2b` | r3-guards | contradiction | C | 0.814207 |
| `0e821f5e7c8d3e53` | r3-guards | override_category | A | 0.928308 |
| `efdbdd665c252dcd` | r3-guards | override_category | B | 0.931767 |
| `eaba54678beedf89` | r3-guards | override_category | C | 0.931767 |
| `6acb4bc5aaccad81` | r3-guards | override_genuine | A | 0.922134 |
| `c9a351dd865f58a0` | r3-guards | override_genuine | B | 0.925800 |
| `26054e94b1e4b62c` | r3-guards | override_genuine | C | 0.925593 |
| `94d7e21af1e5a6bb` | r3-latency-clean | clean | A | 0.928508 |
| `f01aa623496ea8c2` | r3-latency-clean | clean | C | 0.931967 |
| `160097fca774dd9a` | r3-latency-starved | uncooperative | A | 0.833266 |
| `78d901d661edc61c` | r3-latency-starved | uncooperative | C | 0.831846 |
| `a45ece0939ddc5d9` | r3-starvation | uncooperative | A | 0.833266 |
| `4cb05290125ea9f4` | r3-starvation | uncooperative | B | 0.677004 |
| `1f49a8604be361e1` | r3-starvation | uncooperative | C | 0.831846 |
| `265674f2758c92e5` | r3-starvation | vague_start | A | 0.917534 |
| `09e40212b2da6ce6` | r3-starvation | vague_start | B | 0.887845 |
| `9f63bd091abe53eb` | r3-starvation | vague_start | C | 0.918847 |
