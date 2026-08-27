# Phase 1.5B pre-registration

Written **before** the corresponding experiments run. The point of writing a
prediction down is that it can be wrong in public; a prediction produced after
the measurement is not a prediction.

Recorded at: `6e25b2a` (lease landed), immediately before the negation /
hardness matrix.

---

## H1 — negation scope fixes

**Change.** `RESTRICTIVE_RE` lumped `anything but` in with `nothing but`, so
every exceptive was read as a *requirement for the value the customer
refused*. `NEGATION_RE` missed `steer clear of`, `stay away from`, `allergic
to`, `skip`, `pass on`, `rule out`. Hedges (`not sure whether X matters`)
carried a negation token and became rejections.

**Prediction.**

| set | before | after |
|---|---|---|
| dev scope polarity | 8/15 | 15/15 |
| dev hedges (must extract nothing) | 0/4 | 4/4 |

- `clean` is **unchanged, bit-exact at 0.928508**. The public simulator uses
  the four templates, so `open_world_evidence` almost never fires on it.
- `negative_preference` moves by **less than 1 sd** (sd ≈ 0.019). Its
  phrasings (`No X`, `Please avoid X`, `I don't want X`) were already parsed
  correctly, so nothing about that scenario changes.
- `negative_preference_paraphrase` and `negative_scope` improve, because they
  contain the phrasings that were being inverted. This is the only place a
  real effect is expected, and it is not evidence of generalisation — those
  sets were built from the defects.

**What would falsify the change.** Any drop on `clean`, or on
`negation_scope_holdout` (consumed, but a large drop would still mean a new
defect was introduced rather than an old one removed).

## H2 — hardness read from wording

**Change.** `hardness` was `"hard" if turn == 1 else "soft"` — the turn index,
which says nothing about what the customer asked for. It is now read from
requirement vs preference language.

**Prediction.** **No metric moves at all.** With `rescue_relax=False` (the
default) hardness has no consumer, so every score is bit-identical. If any
score moves, hardness is being read somewhere I have not accounted for, and
that is a bug to find, not a result to report.

## H3 — rescue-lane relaxation

**Change.** `rescue_relax=True` surrenders unsatisfiable constraints in
`relaxation_order` (soft → low-confidence hard → older hard → latest explicit
hard) and keeps rescuing only the tail.

**Prediction.** Honestly, **≈ 0 on every current scenario**, for the same
structural reason the negative channel measured ≈ 0: it only bites when two or
more constraints are simultaneously unsatisfiable in the candidate pool, and
the current scenarios rarely produce that state. I expect to keep it **off by
default** and carry it into Phase 2B, where the safe hard-filter creates
exactly that state on purpose.

**What would change my mind.** A gain above 1 sd on `vague_start` or
`uncooperative`, where the query is thinnest.

## H4 — SEALED_NEGATION_V2

To be run **once**, end-to-end, at the end of Phase 1.5B.

**Prediction, written before it is run.** It will show a gap. Four of its
eight families (`barring X`, `save for X`, `X is a hard pass`, `X is off the
table`) are not covered by the fixed patterns, and I expect roughly **4/8 to
6/8** polarity accuracy. The end-to-end score should nonetheless sit **within
1 sd of `negative_preference`**, because a missed negation degrades to no
evidence rather than to inverted evidence — and it is inverted evidence, not
missing evidence, that actually costs ranking.

**If the end-to-end score drops by more than 1 sd**, the fixes did not remove
the inversion class, and Phase 2B's hard-negative filter must not be built.

---

# Outcome, against the predictions above

All shards leased and isolated at **`af327b4`**, every one `matrix_complete`,
24 cells, no partial or invalid rows. Rendered through `lab.report`, which
filters on `citable()`.

## H1 — negation scope fixes: CONFIRMED, and smaller than it looks

| set | dev target | result |
|---|---|---|
| dev scope polarity | 15/15 | 15/15 |
| dev hedges | 4/4 | 4/4 |
| `clean` | unchanged | **0.928508, bit-exact** |
| `clean` compat | unchanged | **0.928708, bit-exact** |

Both frozen baselines held to the last digit, as predicted.

The prediction that the negative CHANNEL would not move was also right, and
more strongly than expected:

| scenario | w_neg=2 | w_neg=0 | Δ | sd |
|---|---|---|---|---|
| `negative_preference` | 0.700683 | 0.698583 | +0.0021 | 0.020 |
| `negative_preference_paraphrase` | 0.677826 | 0.677751 | +0.000075 | 0.017 |
| `negative_scope` | 0.688255 | 0.685938 | +0.0023 | 0.020 |

Every delta is an order of magnitude inside its own sd. **The parser fixes are
correct and the channel they feed still does nothing measurable.** Those are
separate claims and only the first is supported.

## H2 — hardness from wording: CONFIRMED

No metric moved. `clean` bit-exact, every scenario bit-exact against its
`rescue_relax=False` twin. Hardness is read correctly and consumed nowhere by
default, which is what was predicted.

## H3 — rescue-lane relaxation: CONFIRMED, ≈ 0, and now explained

`rescue_relax` on vs off is **bit-identical** on both `vague_start`
(0.917534) and `uncooperative` (0.833266). Bit-identical is stronger than "no
measurable difference", so the branch was instrumented to rule out a wiring
defect:

| scenario | rerank calls | with >1 unsatisfiable constraint |
|---|---|---|
| `vague_start` | 407 | 3 (0.7%) |
| `uncooperative` | 311 | 1.0% |

The gate is `len(dead) > 1`, and two constraints are almost never
unsatisfiable at once in these scenarios. The mechanism works — a unit test
drives it directly — it simply has nothing to act on. **Stays off by default**
and carries into Phase 2B, where the safe hard-filter creates that state
deliberately.

## H4 — prospective challenge set v2

Not a blind or private holdout: the phrasings were authored for this purpose
and the outcome was predicted in advance. It can test whether the fixes
generalise past the cases they were written for; it cannot give an unbiased
estimate.

| config | score | sd |
|---|---|---|
| w_neg=2 (default) | 0.702788 | 0.0203 |
| w_neg=0 (control) | **0.702788** | 0.0203 |

Predicted "within 1 sd of `negative_preference` (0.700683)". Result: +0.0021,
well inside. **Prediction held.**

The control is the informative half, and it is bit-identical. That separates
the two failure modes without any per-phrase probing, which the contract
forbids:

* an **inverted** negation creates a slot that penalises the right product, so
  w_neg=2 would score BELOW w_neg=0;
* a **missed** negation creates no slot at all, so the two are identical.

They are identical to the last digit. Every unrecognised v2 phrasing
(`barring`, `save for`, `hard pass`, `off the table`) degraded to *no
evidence*. **Missed evidence only, zero inversions** — the failure mode the
fixes were built to eliminate is gone.

Per the standing rule: missed evidence is recorded and carried forward; only
inversion would justify further work here. v2 is now consumed and will not be
run again.

## What this does NOT establish

* That the negative channel is worth its weight. Three scenarios and a
  challenge set all say it is not measurable. `w_neg=2.0` is retained as
  saturated and harmless, never as a measured gain.
* That a Phase 2B hard-negative FILTER will pay. It faces the same scarcity:
  candidates reaching the top 10 rarely carry a rejected attribute. Build the
  category/facet planes first and re-measure before committing to it.
* Anything about `override_genuine`, where suppression on and off are
  bit-identical (0.922134). Targeted erasure pays on `override_category`
  (+0.0137 over no suppression) because a category pivot NAMES what is being
  abandoned. When the customer does not name it, the mechanism correctly does
  nothing. The headline number belongs to the pivot case only.
