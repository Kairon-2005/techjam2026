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
