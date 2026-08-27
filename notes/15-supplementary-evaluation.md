# Supplementary Evaluation Protocol

## Purpose

The public 200 remains the primary optimization and reporting set. Repeated seeds
vary simulator interaction, but they do not add independent target products. This
catalog-grounded challenge set expands target and attribute coverage without
claiming access to real shopping conversations or the organizer's private labels.

Every supplementary result must be labeled:

- `source=supplementary_catalog_synthetic`
- `official=false`
- `split=supplementary_dev` or `supplementary_holdout`

It must never be described as an official score, a reconstruction of the private
800, or evidence of the private-set distribution.

## Data boundary

Permitted inputs are only the frozen catalog's participant-visible metadata and
the public target IDs used solely as an exclusion list. The generator does not use
Amazon reviews, purchase history, upstream interaction records, agent rankings,
experimental outcomes, or ground-truth retrieval performance. Targets are unique,
catalog-valid, disjoint between splits, and disjoint from the public 200.

Constraints are deterministic generic facts (material, color, budget bucket,
use-case, fit, and a small allowlist of features). Free-form titles, stores,
descriptions, and feature sentences are never copied into user-visible messages.

## Frozen splits

- `supplementary_dev`: 1,000 targets, available for development diagnostics.
- `supplementary_holdout`: 1,000 different targets, sealed and unrun.

Both contain exactly 400 Buying, 400 Browsing, 150 Intent Override, and 50
Boundary sessions. Overrides occur only on turns 3 or 4. The official 10-turn
protocol and scoring implementation can be reused without modifying it.

## Pre-registered decision rule

Supplementary results are a **robustness veto**, not a second optimization target:

1. An official improvement may be accepted only if supplementary development does
   not show a material collapse in overall score, HR@10, or a scenario slice.
2. A supplementary improvement can never compensate for an official regression.
3. Small supplementary score differences are not tuning signals. Do not optimize
   thresholds or weights to chase them.
4. A material collapse must be defined before each experiment relative to that
   experiment's paired baseline; use the same seeds and configuration provenance.
5. The sealed holdout receives one pre-registered run only after architecture and
   defaults are frozen. Afterwards it is consumed permanently.

## Limitations

These are synthetic intent cards derived from catalog metadata, not real purchase
sessions. Category-stratified selection intentionally broadens coverage and does
not mirror an unknown official distribution. Metadata can be noisy. Neutral
profiles deliberately avoid fabricating purchase-history evidence, so this set
does not validate personalization. It is strongest as a brittleness detector and
weak as a performance estimator.

