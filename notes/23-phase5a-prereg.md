# Phase 5A pre-registration — remove the FacetIndex cold cost from `score_default`

Written before implementation. Recorded at `b10cc1a`, 187 tests passing.

## The stated premise is wrong, and the correction changes the work

The task assumed question utility reuses `FacetIndex`. **It does not.**
`_facet_coverage()` reads `ATTR_VOCAB` against `self.cat.text` over the
**bounded live window** (`pool_depth = 30`) and never touches the index. The
hypothesis "coverage can be computed from the bounded window rather than a
global FacetIndex" is therefore **already true in the shipped code**.

The 6.19 s build is caused entirely by the *safe filter* path:
`_eligible_filters()` (line ~1688) and `_safe_pool()` (line ~1727). Those are
what Phase 5A has to address.

## Why the literal bounded-window replacement cannot be equivalent

Recomputing facet consistency over the retrieved candidates instead of the
catalog changes two things that are defined globally:

1. **The relaxation threshold.** `_safe_pool` surrenders constraints while
   `len(pool) < buying_min_candidates` (60), where `pool` is a subset of all
   50,000 products. Measured over ≤1200 retrieved candidates instead, the count
   is a different quantity and relaxation would fire far more often.
2. **Eligibility.** `hard_ok()` compares a facet's **catalog-wide** coverage
   against `facet_min_coverage`, and `match()` asks whether the catalog uses a
   value at all. Neither is answerable from a window.

The facet filter is also **not inert** on `score_default` — disabling it moves
clean from `0.932067` to `0.933042` — so it cannot simply be skipped.

## Arm B, as it will actually be implemented

> `FacetIndex` is built **per attribute, on demand**. An attribute's postings
> and presence set are constructed only when a slot of that attribute reaches
> the final eligibility gate. No global build occurs on the `score_default`
> path.

Same data, same vocabularies, same semantics — only the construction is
deferred. This is **exactly behaviour-preserving by construction**: every
value read is identical, it is merely computed later and only if needed.

Cost model: the present build is one pass over 50,000 documents for **seven**
facets. Per-attribute construction pays roughly one seventh per attribute
actually used, so a session naming one material should pay ~0.9 s rather than
6.19 s, and a session naming none should pay **zero**.

Arm C (a different deferral scheme) is implemented **only if B exposes a
correctness issue**.

## Constraints

Category plane OFF, dense OFF, unchanged. No change to retrieval ranking, slot
semantics, routes, question policy or supplementary data. No new model,
dependency, index, cache or network dependency — this reorganises when
existing structures are built, and adds none.

## Predictions

1. **Bit-exact.** Clean, all four official slices, every robustness scenario
   and `supplementary_dev` reproduce arm A to the last digit, because the same
   values are computed from the same inputs.
2. **The second-turn stall disappears** when no eligible hard slot names an
   indexed facet, and shrinks to roughly one seventh when one does.
3. **No new memory cost**; peak falls, because unused facets are never built.
4. Least confident: that *no* public session triggers a build at all. Template
   constraints are hard and confident, so many will name `material` or
   `color`. The claim is proportional reduction, not elimination.

## Acceptance

Adopt B only if: official score, HR@10, MRR and MTTC are **bit-exact** to A ·
`ask_policy="other"` anchor exact · no robustness or supplementary veto · no
new memory cost · **global `FacetIndex` never built** in a `score_default`
process · the ~6.19 s second-turn stall removed or proportionally reduced,
with warm post-first-turn p95 < 100 ms · all tests pass.

If exact equivalence cannot be demonstrated, **retain the current
implementation and record the negative result.** No tuning.
