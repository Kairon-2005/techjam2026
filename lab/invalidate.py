"""Mark recorded rows as non-citable, in place and with the reason attached.

The ledger is append-only for RESULTS: a number, once recorded, is never
edited or deleted, because the whole point of the log is that it cannot be
quietly improved after the fact. But provenance is sometimes only learned
later -- a run turns out to have been concurrent with another writer, a
dataset turns out to have been regenerated -- and a row that is known to be
untrustworthy must not keep looking trustworthy just because the field that
would have said so did not exist when it was written.

So rows gain an `invalid` block; nothing else about them changes. Readers
filter on it. Every invalidation names its reason and the evidence for it.

    python3 -m lab.invalidate --tag erasure-matrix-clean-5f75f26 \
        --reason concurrent_repository_mutation --note "..."
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

LOG = Path("lab/results.jsonl")


def invalidate(tags: list[str], reason: str, note: str,
               log: Path = LOG) -> int:
    rows = [json.loads(line) for line in log.open(encoding="utf-8") if line.strip()]
    stamp = {"reason": reason, "note": note,
             "marked_ts": dt.datetime.now().isoformat(timespec="seconds")}
    hit = 0
    for row in rows:
        if row.get("tag") in tags and "invalid" not in row:
            row["invalid"] = stamp
            hit += 1
    with log.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return hit


def citable(rows: list[dict]) -> list[dict]:
    """The rows a report is allowed to quote."""
    return [r for r in rows
            if not r.get("invalid")
            and not r.get("code_dirty")
            and r.get("schema_version", 0) >= 2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", action="append", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    n = invalidate(args.tag, args.reason, args.note)
    print(f"marked {n} rows invalid ({args.reason})")


if __name__ == "__main__":
    main()
