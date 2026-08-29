"""The ONE operative Phase 7A split, and the guard that refuses every other one.

`notes/40` registered a scenario-stratified 800/200 split and SUPERSEDED an
earlier global `sha256 % 5` split that happened to give **806/194**. Both
numbers appear in the notes, only one of them is operative, and a driver that
silently ran the superseded one would train on 806 sessions, validate on 194,
and produce numbers that no pre-registration covers.

So the split is not recomputed ad hoc by whoever needs it. It is built here,
from the corpus and the ids alone -- no RNG, no seed -- and every consumer runs
`assert_operative()` before its first trial. The assertions are hard: a
mismatch raises, and nothing downstream gets to decide it is close enough.

    train  800 = buying 320 + browsing 320 + intent_override 120 + boundary 40
    val    200 = buying  80 + browsing  80 + intent_override  30 + boundary 10

The two id hashes are the pre-registered ones from `notes/40`. They are checked,
not printed for approval: an id hash that has to be eyeballed is an id hash that
will eventually be eyeballed wrong.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

DEV = Path("data/supplementary_dev.jsonl")

# Exact counts, frozen in notes/40 before any trial. A hash split does not
# preserve strata -- `boundary` is 50 samples in the whole corpus and could have
# landed anywhere -- which is why the operative split is stratified.
SCENARIO_TOTAL = {"buying": 400, "browsing": 400, "intent_override": 150, "boundary": 50}
SCENARIO_TRAIN = {"buying": 320, "browsing": 320, "intent_override": 120, "boundary": 40}
SCENARIO_VAL = {k: SCENARIO_TOTAL[k] - v for k, v in SCENARIO_TRAIN.items()}
TRAIN_N = sum(SCENARIO_TRAIN.values())          # 800
VAL_N = sum(SCENARIO_VAL.values())              # 200
UNION_N = TRAIN_N + VAL_N                       # 1000

TRAIN_HASH = "48d14de25a4adf90adbcd9ad621ea2e1d143bd5632a8be67fed239ff4822290d"
VAL_HASH = "82e0470ee83d2cf8883399ededda11b5ddb4fa762685196b36a9fe521a105a73"

# The SUPERSEDED split, recorded so the change stays visible and so the guard
# can name what it rejected. notes/40 keeps its short hash 211be164cec5ff4f.
# These two integers are the tripwire: any split presenting 806 train rows or
# 194 val rows is the old global-hash one and is refused by count alone, before
# any id hash is computed.
SUPERSEDED_TRAIN_N = 806
SUPERSEDED_VAL_N = 194
SUPERSEDED_NOTE = ("the superseded global sha256 %% 5 split (%d/%d, notes/40 "
                   "short hash 211be164cec5ff4f). It is NOT operative and its "
                   "scenario proportions are accidental."
                   % (SUPERSEDED_TRAIN_N, SUPERSEDED_VAL_N))

# Id namespaces. The corpora are disjoint by construction -- `public_0001`,
# `supplementary_dev_0001`, `supplementary_holdout_0001` -- so membership is a
# prefix test and the sealed holdout file is never opened to run it. A guard
# that had to read the sealed corpus in order to prove it was untouched would
# be touching it.
DEV_PREFIX = "supplementary_dev_"
FORBIDDEN_PREFIXES = ("public_", "supplementary_holdout_")


class SplitError(AssertionError):
    """A split that is not the one operative split. Never recoverable."""


def id_hash(ids) -> str:
    """sha256 over newline-delimited canonical ids, SORTED, trailing newline."""
    return hashlib.sha256(
        ("\n".join(sorted(str(i) for i in ids)) + "\n").encode()).hexdigest()


@dataclasses.dataclass(frozen=True, slots=True)
class Split:
    train: tuple[str, ...]
    val: tuple[str, ...]

    @property
    def train_hash(self) -> str:
        return id_hash(self.train)

    @property
    def val_hash(self) -> str:
        return id_hash(self.val)

    def summary(self) -> dict:
        return {"train_n": len(self.train), "val_n": len(self.val),
                "train_hash": self.train_hash, "val_hash": self.val_hash}


def load_rows(path: Path | None = None) -> list[dict]:
    # Resolved at call time, not bound as a default: a test that points DEV at
    # a doctored corpus must actually reach these functions, or the guard is
    # only ever exercised against the corpus that satisfies it.
    return [json.loads(line) for line in
            Path(path or DEV).read_text(encoding="utf-8").splitlines() if line.strip()]


def build(rows) -> Split:
    """The operative split, as a pure function of the ids and the corpus.

    Within each `scenario_type`, ids are ranked by canonical `sha256(id)` hex
    ascending and the first N taken as train. No RNG and no seed.
    """
    by: dict[str, list[str]] = {}
    for row in rows:
        by.setdefault(str(row["scenario_type"]), []).append(str(row["sample_id"]))
    train: list[str] = []
    val: list[str] = []
    for scenario in sorted(by):
        ids = sorted(by[scenario],
                     key=lambda s: hashlib.sha256(s.encode()).hexdigest())
        n = SCENARIO_TRAIN.get(scenario)
        if n is None:
            raise SplitError(f"unknown scenario_type {scenario!r}: the split is "
                             f"stratified over exactly {sorted(SCENARIO_TRAIN)}")
        train += ids[:n]
        val += ids[n:]
    return Split(tuple(train), tuple(val))


def _counts(ids, rows) -> dict[str, int]:
    scenario_of = {str(r["sample_id"]): str(r["scenario_type"]) for r in rows}
    out: dict[str, int] = {}
    for i in ids:
        out[scenario_of.get(str(i), "<unknown>")] = \
            out.get(scenario_of.get(str(i), "<unknown>"), 0) + 1
    return out


def problems(split: Split, rows) -> list[str]:
    """Every reason this is not the operative split. Empty means it is.

    Returned as a list rather than raised one at a time so a caller sees the
    whole picture: a split that is wrong in three ways should not be fixed
    three times.
    """
    bad: list[str] = []
    train, val = list(split.train), list(split.val)

    # The superseded split, by count, first and loudest. 806/194 is not a near
    # miss to be reported alongside a hash mismatch -- it is a named, retired
    # split, and seeing it means the caller ran the wrong code path.
    if len(train) == SUPERSEDED_TRAIN_N or len(val) == SUPERSEDED_VAL_N:
        bad.append("SUPERSEDED SPLIT: " + SUPERSEDED_NOTE)

    if len(train) != TRAIN_N:
        bad.append(f"train row count {len(train)}, expected {TRAIN_N}")
    if len(val) != VAL_N:
        bad.append(f"val row count {len(val)}, expected {VAL_N}")

    tc, vc = _counts(train, rows), _counts(val, rows)
    for scenario in sorted(SCENARIO_TRAIN):
        if tc.get(scenario, 0) != SCENARIO_TRAIN[scenario]:
            bad.append(f"train {scenario} count {tc.get(scenario, 0)}, "
                       f"expected {SCENARIO_TRAIN[scenario]}")
        if vc.get(scenario, 0) != SCENARIO_VAL[scenario]:
            bad.append(f"val {scenario} count {vc.get(scenario, 0)}, "
                       f"expected {SCENARIO_VAL[scenario]}")
    for extra in sorted((set(tc) | set(vc)) - set(SCENARIO_TRAIN)):
        bad.append(f"unregistered scenario_type {extra!r} in the split")

    if split.train_hash != TRAIN_HASH:
        bad.append(f"train id hash {split.train_hash} != pre-registered {TRAIN_HASH}")
    if split.val_hash != VAL_HASH:
        bad.append(f"val id hash {split.val_hash} != pre-registered {VAL_HASH}")

    overlap = set(train) & set(val)
    if overlap:
        bad.append(f"train/val overlap is {len(overlap)}, expected 0: "
                   f"{sorted(overlap)[:5]}")
    union = set(train) | set(val)
    if len(union) != UNION_N:
        bad.append(f"union is {len(union)} distinct ids, expected {UNION_N}")
    if len(set(train)) != len(train):
        bad.append("duplicate id within train")
    if len(set(val)) != len(val):
        bad.append("duplicate id within val")

    corpus = {str(r["sample_id"]) for r in rows}
    stray = union - corpus
    if stray:
        bad.append(f"{len(stray)} ids are not in the corpus: {sorted(stray)[:5]}")

    # No public and no sealed-holdout sample ever enters selection. Checked by
    # namespace, so the sealed file stays closed.
    for i in sorted(union):
        if i.startswith(FORBIDDEN_PREFIXES):
            bad.append(f"forbidden corpus id in the split: {i}")
            break
        if not i.startswith(DEV_PREFIX):
            bad.append(f"id outside {DEV_PREFIX!r}: {i}")
            break
    return bad


def assert_operative(split: Split, rows) -> Split:
    """Hard gate. Returns the split, or raises with every problem listed."""
    bad = problems(split, rows)
    if bad:
        raise SplitError("this is not the operative Phase 7A split:\n  - "
                         + "\n  - ".join(bad))
    return split


def operative(path: Path | None = None) -> tuple[Split, list[dict]]:
    """Build the split from the corpus and assert it before returning it."""
    rows = load_rows(path)
    return assert_operative(build(rows), rows), rows


def train_rows(path: Path | None = None) -> list[dict]:
    split, rows = operative(path)
    by_id = {str(r["sample_id"]): r for r in rows}
    return [by_id[i] for i in split.train]


def val_rows(path: Path | None = None) -> list[dict]:
    split, rows = operative(path)
    by_id = {str(r["sample_id"]): r for r in rows}
    return [by_id[i] for i in split.val]


def main() -> int:
    split, rows = operative()
    print(json.dumps({**split.summary(),
                      "train_by_scenario": _counts(split.train, rows),
                      "val_by_scenario": _counts(split.val, rows),
                      "overlap": len(set(split.train) & set(split.val)),
                      "union": len(set(split.train) | set(split.val))},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
