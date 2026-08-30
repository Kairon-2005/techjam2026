"""The submission tree stays English, and free of leftover placeholders.

This is a regression test for a property that is easy to lose: the project's
working notes were originally written in Chinese, and a single re-added
paragraph would put them back into a tree judges read in English. A placeholder
like `<this repo>` is the same class of problem -- invisible to the author,
obvious to a reader.

It is NOT an ASCII check. Em dashes, curly quotes, arrows and Greek letters are
ordinary technical prose and are allowed.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from lab import textaudit as T


class NoCJKTest(unittest.TestCase):
    def test_no_submission_facing_file_contains_cjk(self) -> None:
        hits = T.cjk()
        self.assertEqual(hits, [], "CJK text reappeared in:\n  "
                                   + "\n  ".join(hits[:10]))

    def test_the_scanner_actually_detects_cjk(self) -> None:
        # A scanner that cannot fire is a comment.
        # Written as ESCAPES so this file carries no literal CJK of its own --
        # the same reason lab/textaudit.py does. The codepoints fed to the
        # regex are identical either way.
        for sample in ("\u51b3\u7b56\u65e5\u5fd7", "\u30c6\u30b9\u30c8", "\u3002", "\uff08\u6ce8\uff09"):
            self.assertTrue(T.CJK_RE.search(sample), repr(sample))

    def test_ordinary_unicode_punctuation_is_allowed(self) -> None:
        # These appear throughout the documentation and must not be flagged.
        for sample in ("em — dash", "curly ’quote’", "λ = 1.0", "→ arrow",
                       "±0.005", "0.9280 · 8.70×", "café"):
            self.assertIsNone(T.CJK_RE.search(sample), sample)


class NoPlaceholderTest(unittest.TestCase):
    def test_no_leftover_placeholder_survives(self) -> None:
        hits = T.placeholders()
        self.assertEqual(hits, [], "placeholders remain in:\n  "
                                   + "\n  ".join(hits[:10]))

    def test_the_demo_video_link_is_the_one_allowed_placeholder(self) -> None:
        # It cannot exist until the video does, so it is allowed by name.
        self.assertIsNotNone(
            T.ALLOWED_PLACEHOLDER.search("**Demo video:** _(link placeholder)_"))
        self.assertIn("_(link placeholder)_",
                      Path("README.md").read_text(encoding="utf-8"))

    def test_the_scanner_actually_detects_placeholders(self) -> None:
        for sample in ("git clone <this repo>", "# TODO: fix", "TBD",
                       "# FIXME", "https://example.com/x"):
            self.assertTrue(any(p.search(sample) for p in T.PLACEHOLDER_RES),
                            sample)


class ScopeTest(unittest.TestCase):
    """What is exempt, and why each exemption is not a loophole."""

    def test_frozen_data_and_ledgers_are_out_of_scope(self) -> None:
        for path in ("data/public_set.jsonl", "lab/results.jsonl",
                     "lab/r0/artifacts/ms-marco-TinyBERT-L2-v2/vocab.txt",
                     "lab/r0/artifacts/ms-marco-TinyBERT-L2-v2/LICENSE"):
            self.assertFalse(T.in_scope(Path(path)), path)

    def test_source_and_documentation_are_in_scope(self) -> None:
        for path in ("README.md", "NOTES.md", "PROJECT.md", "starter/agent.py",
                     "lab/tune.py", "notes/00-problem-spec.md",
                     "notes/08-review-response.md", "docs/MODEL_CARD.md"):
            self.assertTrue(T.in_scope(Path(path)), path)

    def test_the_files_the_review_named_are_all_covered(self) -> None:
        named = ["NOTES.md", "PROJECT.md", "lab/tune.py"]
        named += [f"notes/0{i}-" for i in range(9)]
        tracked = {str(p) for p in T.tracked() if T.in_scope(p)}
        for name in named:
            self.assertTrue(any(t.startswith(name) for t in tracked),
                            f"{name} is not covered by the scanner")

    def test_only_the_pattern_defining_files_are_placeholder_exempt(self) -> None:
        # Three files, and the exemption covers the PLACEHOLDER check alone.
        self.assertEqual(T.PATTERN_DEFINING,
                         ("lab/textaudit.py", "lab/audit.py",
                          "tests/test_textaudit.py"))
        for path in T.PATTERN_DEFINING:
            self.assertTrue(T.in_scope(Path(path)),
                            f"{path} must still be scanned for CJK")

    def test_the_scanner_source_contains_no_cjk_of_its_own(self) -> None:
        # The ranges are written as escapes precisely so that this holds: a
        # scanner whose source carried the alphabet it looks for would report
        # itself forever, and exempting it would blind the check.
        source = Path("lab/textaudit.py").read_text(encoding="utf-8")
        self.assertIsNone(T.CJK_RE.search(source))


if __name__ == "__main__":
    unittest.main()
