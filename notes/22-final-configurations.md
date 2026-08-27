# The two configurations

Phase 4 closes **architecture-complete, feature-off**. There are exactly two
supported configurations, and they are not interchangeable.

## 1. `score_default` — the submission configuration

```
deep_funnel        = ON        # Phase 2 arm C
starvation_bypass  = ON        # Phase 2 arm C
category_plane     = OFF
question_utility   = ON        # Phase 3
dense_browsing     = OFF
dense_mixed        = OFF
```

This is `starter/agent.DEFAULTS` as shipped, and the only configuration whose
score and reproducibility are claimed.

| | |
|---|---|
| clean | `0.932067` |
| HR@10 / MRR / MTTC | `0.995` / `0.852556` / `2.060` |
| buying / browsing / boundary / intent_override MRR | `0.851181` / `0.809375` / `1.000000` / `0.922222` |
| compatibility anchor `ask_policy="other"` (legacy path) | `0.928708`, bit-exact |

## 2. `showcase_dense` — architecture demonstration only

```
score_default, plus:
dense_browsing = ON
dense_mixed    = ON
dense_fusion   = "dense_only"      # pre-registered arm B
```

**For route-topology evidence and demonstration only. It is not the robust
final default and must never be presented as one.**

What it demonstrates: Browsing and Mixed draw from a genuinely independent
dense candidate source — **94.4%** of dense candidates per dense turn (283.16
of 300) are unreachable by BM25 — while Buying stays lexical by construction,
with bit-identical Buying MRR across every arm. Local CPU, zero network,
stdlib only.

## The trade-off, stated plainly

| | clean | Browsing MRR | Boundary MRR | contradiction |
|---|---|---|---|---|
| `score_default` (A) | 0.932067 | 0.809375 | **1.000000** | **0.814187** |
| arm B (dense-only) | **0.932942** | **0.834583** | **0.870000** | 0.808136 |
| arm C (RRF) | 0.930629 | 0.797396 | **1.000000** | 0.812750 |

* **B** improves clean overall (+0.000875) and Browsing MRR (+0.025208), but
  drops **Boundary MRR from 1.000 to 0.870** and **fails the contradiction
  guard** at −0.006051 against a 0.005 limit.
* **C** restores Boundary to 1.000 exactly, but **fails official MRR**
  (−0.004792 vs 0.003) and **Browsing MRR** (−0.011979 vs 0.005) — it buys
  Boundary back by diluting the slice dense was added to improve.

**Dense is therefore implemented but feature-off for the score-default
submission.** No weight, dimension, route-gating or category search was run to
resolve this; the pre-registered parameters stand.

## Operational caveats carried forward

* Dense build is **order-dependent**: 4.66 s cold, 92.17 s if `FacetIndex` is
  already resident. Route ordering happens to hit the fast case; that is not a
  guarantee.
* Dense adds **~98–122 MB** resident. R0's 39.4 MB figure is withdrawn as
  unreproducible.
* **No offline prebuilt artefact path exists** — a hard prerequisite for any
  future default-ON.
* `score_default` itself still pays **~6.19 s** building `FacetIndex` on the
  first constraint turn. This is a defect in the shipped default and is the
  subject of Phase 5A.
