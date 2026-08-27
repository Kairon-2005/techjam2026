"""CLI over lab.provenance: append an invalidation record for matching rows.

Results are never rewritten. An earlier version of this tool opened
lab/results.jsonl, edited rows in memory and wrote the whole file back -- with
no lock, while a leased run was appending to it. That is the same race the
lease exists to prevent, committed by the tool built to record that race.

    python3 -m lab.invalidate --tag phase15b-erasure \
        --reason concurrent_repository_mutation --note "..."
"""
from __future__ import annotations

import argparse

from lab import provenance as P


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--scenario", action="append", default=[])
    ap.add_argument("--reason", required=True)
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    if not args.tag and not args.scenario:
        ap.error("give at least one --tag or --scenario")
    rows = [r for r in P.load()
            if (not args.tag or r.get("tag") in args.tag)
            and (not args.scenario or r.get("scenario") in args.scenario)]
    if not rows:
        print("no rows matched; nothing appended")
        return
    rec = P.mark(rows, args.reason, args.note)
    print(f"appended 1 invalidation covering {len(rec['row_keys'])} rows "
          f"({args.reason}) -> {P.INVALIDATIONS}")


if __name__ == "__main__":
    main()
