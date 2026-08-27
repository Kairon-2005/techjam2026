# Supplementary Catalog-Grounded Challenge Set

This module deterministically builds two synthetic datasets from participant-visible
fields in the frozen 50,000-product catalog. It never reads reviews, purchases,
agent outputs, rankings, experiment results, or organizer-private data.

The datasets are **not official evaluation artifacts** and do not estimate the
organizer's private distribution. Their only role is to veto defaults that improve
the official public 200 while collapsing across a wider set of catalog targets.

## Generate

Download the official catalog as described in the repository README, then run from
the repository root:

```bash
python3 -m supplementary.generate \
  --generator-commit "$(git rev-parse HEAD)"
python3 -m supplementary.validate
```

The committed manifest records the exact generator version and commit, config,
catalog/public-set hashes, split hashes, counts, and limitations.

## Holdout contract

`supplementary_holdout` is sealed immediately after generation. Before a single
pre-registered final robustness run, only automated schema, hash, aggregate-count,
catalog-membership, leakage, and disjointness checks are allowed. Do not inspect
individual holdout rows, tune against it, or run it repeatedly.

The safe development adapter refuses sealed rows:

```bash
python3 -m supplementary.evaluate_dev
```

It delegates session simulation and metrics to the unmodified official evaluator,
then labels the output `source=supplementary_catalog_synthetic` and `official=false`.

