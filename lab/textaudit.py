"""Submission-facing text checks: no CJK, no leftover placeholders.

The submission is read by judges in English. Chinese working notes were fine
while this was a lab; they are not fine in a tree someone else has to read, and
neither is a `<this repo>` that never got filled in.

WHAT IS EXEMPT, AND WHY. Frozen datasets, result and benchmark ledgers, the
vendored tokenizer/vocabulary, and third-party license text are all things we
must NOT rewrite: a dataset is input, a ledger row is a record that is never
edited, a vocabulary is model data, and a license is someone else's text. They
are excluded by path, and the exclusion list is short enough to read.

THIS IS NOT AN ASCII CHECK. Ordinary Unicode punctuation -- em dashes, curly
quotes, arrows, Greek letters -- is normal technical prose and is allowed.

    python3 -m lab.textaudit
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Han, Hiragana, Katakana, CJK punctuation, compatibility and fullwidth forms.
CJK_RE = re.compile(
    r"[⺀-⻿　-〿぀-ヿ㄀-ㄯ"
    r"㐀-䶿一-鿿豈-﫿︰-﹏＀-￯]")

SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".json"}

# Excluded by path, with the reason each one is not ours to rewrite.
EXEMPT_PREFIXES = (
    "data/",                       # frozen datasets -- input, never edited
    "lab/r0/artifacts/",           # vendored model: vocab, tokenizer, LICENSE
    "supplementary/__pycache__/",
)
EXEMPT_SUFFIXES = (
    ".jsonl",                      # append-only ledgers and caches
)
EXEMPT_EXACT = (
    "results.json", "results_probe.json",
    "docs/FINAL_VERIFICATION.md.json",
    "lab/a1cache.meta.json", "lab/a1weights.json", "lab/a2lambda.json",
    "lab/r1_fields.json", "lab/r1_semantic.json",
    "lab/supval_verdict.json", "lab/public_verdict.json",
)

# Placeholders that must not survive into a submission. The demo-video link is
# the ONE exception: it cannot exist until the video does.
PLACEHOLDER_RES = (
    re.compile(r"<this repo>"),
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bFIXME\b"),
    # XXX is deliberately NOT here: it is a common test-fixture string
    # ("a" * 40 + "XXX") and flagging it produced only false positives.
    # TODO/TBD/FIXME cover the marker that actually leaks into a submission.
    re.compile(r"example\.com|your-org|YOUR_|<insert|\.\.\.your"),
)
ALLOWED_PLACEHOLDER = re.compile(r"_\(link placeholder\)_")


def tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True,
                         text=True).stdout
    return [Path(p) for p in out.split("\0") if p]


def in_scope(path: Path) -> bool:
    name = str(path)
    if path.suffix not in SUFFIXES:
        return False
    if name.startswith(EXEMPT_PREFIXES) or name.endswith(EXEMPT_SUFFIXES):
        return False
    return name not in EXEMPT_EXACT


def scan(pattern_or_res, paths=None, allow=None) -> list[str]:
    """Every `path:line` whose text matches. Empty means clean."""
    patterns = (pattern_or_res if isinstance(pattern_or_res, tuple)
                else (pattern_or_res,))
    hits: list[str] = []
    for path in [p for p in (paths or tracked()) if in_scope(p)]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if allow is not None and allow.search(line):
                continue
            if any(p.search(line) for p in patterns):
                hits.append(f"{path}:{number}  {line.strip()[:80]}")
    return hits


def cjk(paths=None) -> list[str]:
    return scan(CJK_RE, paths)


# The two modules that DEFINE the placeholder patterns necessarily contain them.
# Exempt from the PLACEHOLDER check only -- both stay in scope for CJK.
PATTERN_DEFINING = ("lab/textaudit.py", "lab/audit.py")


def placeholders(paths=None) -> list[str]:
    scoped = [p for p in (paths or tracked())
              if str(p) not in PATTERN_DEFINING]
    return scan(PLACEHOLDER_RES, scoped, allow=ALLOWED_PLACEHOLDER)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.textaudit")
    ap.add_argument("--show", type=int, default=20)
    args = ap.parse_args(argv)
    paths = tracked()
    scoped = [p for p in paths if in_scope(p)]
    checks = (("CJK characters", cjk(paths)),
              ("leftover placeholders", placeholders(paths)))
    bad = 0
    for label, hits in checks:
        print(f"\n{label}: {len(hits)} in {len(scoped)} submission-facing files")
        for hit in hits[: args.show]:
            print(f"  {hit}")
        if len(hits) > args.show:
            print(f"  ... and {len(hits) - args.show} more")
        bad += len(hits)
    print(f"\n{'PASS' if not bad else 'FAIL'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
