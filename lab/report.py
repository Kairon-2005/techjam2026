"""The one path from the ledger to a table.

Numbers reach a report through here and nowhere else, so that the citability
rule is enforced by construction rather than by remembering to apply it. A
non-citable row is not quietly dropped: it is listed, with the reason, under
the table it was excluded from -- silent filtering would hide exactly the
thing the filter exists to surface.

    python3 -m lab.report --tag phase15b-erasure
    python3 -m lab.report --tag phase15b-erasure --show-excluded
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from lab import provenance as P

METRICS = ("score", "score_sd", "hr10", "mrr", "mttc")


def _label(row: dict) -> str:
    cfg = row.get("config") or {}
    return row.get("config_label") or (", ".join(f"{k}={v}" for k, v in sorted(cfg.items()))
                                       or "default")


def table(rows: list[dict], title: str = "") -> str:
    """Render citable rows only. Never call print on raw ledger rows."""
    ok = P.citable(rows)
    out: list[str] = []
    if title:
        out.append(f"\n=== {title} ===")
    if not ok:
        out.append("  no citable rows")
        return "\n".join(out)
    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for row in ok:
        by_scenario[row.get("scenario", "?")].append(row)
    for scenario, group in sorted(by_scenario.items()):
        seeds = sorted({len(r.get("seeds") or []) for r in group})
        out.append(f"\n  {scenario}  (n_seeds={seeds[0] if len(seeds) == 1 else seeds})")
        out.append(f"    {'config':<30}{'score':>10}{'sd':>8}{'HR@10':>8}"
                   f"{'MRR':>8}{'MTTC':>7}  {'commit':>8}")
        for row in sorted(group, key=lambda r: -r["score"]):
            out.append(f"    {_label(row):<30}{row['score']:>10.6f}"
                       f"{row.get('score_sd', 0):>8.4f}{row['hr10']:>8.3f}"
                       f"{row['mrr']:>8.3f}{row['mttc']:>7.2f}"
                       f"  {row.get('agent_commit', '?'):>8}")
    return "\n".join(out)


def excluded(rows: list[dict]) -> str:
    why = P.reasons(rows)
    bad = [(r, why[P.row_key(r)]) for r in rows if why[P.row_key(r)]]
    if not bad:
        return ""
    lines = [f"\n  excluded ({len(bad)} rows, not citable):"]
    seen: set[tuple] = set()
    for row, reason in bad:
        key = (row.get("tag"), reason)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"    {row.get('tag', '?'):<34}{reason}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--scenario", action="append", default=[])
    ap.add_argument("--show-excluded", action="store_true")
    args = ap.parse_args()
    rows = P.load()
    if args.tag:
        rows = [r for r in rows if r.get("tag") in args.tag]
    if args.scenario:
        rows = [r for r in rows if r.get("scenario") in args.scenario]
    print(table(rows, ", ".join(args.tag) or "all tags"))
    if args.show_excluded:
        print(excluded(rows))


if __name__ == "__main__":
    main()
