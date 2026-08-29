"""Phase 6C1: the pure profile primitives.

`user_profile` arrives from outside the agent and nothing in this repository
guarantees its shape, so these are bounded against the input they are GIVEN,
not the input the public set happens to contain. Every case here is a rule from
notes/37 revision 3, and several are the specific ways a matcher can look
correct while measuring something else.
"""
from __future__ import annotations

import re
import unittest

import starter.context as C


class NormalizationTest(unittest.TestCase):
    """normalize -> truncate -> drop empties -> stable-dedupe -> cap, in order."""

    def test_tags_are_normalized(self) -> None:
        self.assertEqual(C.normalize_profile_tags(["  Cotton  ", "BLUE"]),
                         ("cotton", "blue"))

    def test_internal_whitespace_is_collapsed(self) -> None:
        self.assertEqual(C.normalize_profile_tags(["long   sleeve"]),
                         ("long sleeve",))

    def test_non_strings_are_coerced_not_dropped(self) -> None:
        # A profile is external input; a number is a value someone meant.
        self.assertEqual(C.normalize_profile_tags([42, "x"]), ("42", "x"))

    def test_tags_are_truncated_to_the_cap(self) -> None:
        long = "a" * 200
        got, = C.normalize_profile_tags([long])
        self.assertEqual(len(got), C.MAX_PROFILE_TAG_CHARS)
        self.assertEqual(C.MAX_PROFILE_TAG_CHARS, 40)

    def test_truncation_happens_before_deduplication(self) -> None:
        # Two tags differing only past character 40 collapse into one -- the
        # right outcome, because the kernel could not tell them apart anyway.
        a, b = "a" * 40 + "XXX", "a" * 40 + "YYY"
        self.assertEqual(C.normalize_profile_tags([a, b]), ("a" * 40,))

    def test_empties_are_dropped_before_deduplication(self) -> None:
        self.assertEqual(C.normalize_profile_tags(["", "   ", None, "x"]), ("x",))

    def test_deduplication_is_stable_and_keeps_the_first(self) -> None:
        # set() would make classification depend on hash randomization, and
        # "deterministic" would be false in the hardest way to reproduce.
        self.assertEqual(C.normalize_profile_tags(["b", "a", "b", "c", "a"]),
                         ("b", "a", "c"))

    def test_deduplication_happens_before_the_cap(self) -> None:
        # The cap must admit eight DISTINCT tags, not eight slots a repeat
        # could fill. Capping first would discard real signal.
        tags = ["dup"] * 6 + [f"t{i}" for i in range(6)]
        got = C.normalize_profile_tags(tags)
        self.assertEqual(got[0], "dup")
        self.assertEqual(len(got), 7)
        self.assertEqual(len(set(got)), len(got))

    def test_the_cap_is_eight(self) -> None:
        got = C.normalize_profile_tags([f"tag{i}" for i in range(50)])
        self.assertEqual(len(got), C.MAX_PROFILE_TAGS)
        self.assertEqual(C.MAX_PROFILE_TAGS, 8)
        self.assertEqual(got[-1], "tag7", "the cap must keep the FIRST eight")

    def test_the_result_is_an_immutable_tuple(self) -> None:
        self.assertIsInstance(C.normalize_profile_tags(["a"]), tuple)

    def test_missing_and_empty_input(self) -> None:
        for bad in (None, [], (), ["", None]):
            self.assertEqual(C.normalize_profile_tags(bad), ())

    def test_normalization_is_idempotent(self) -> None:
        once = C.normalize_profile_tags(["  Cotton ", "blue", "cotton"])
        self.assertEqual(C.normalize_profile_tags(once), once)


class KernelTest(unittest.TestCase):
    """One shared word-boundary matcher, escaping untrusted input."""

    def test_a_whole_word_matches(self) -> None:
        self.assertTrue(C.profile_match("cotton", "a cotton shirt"))

    def test_a_substring_does_not_match(self) -> None:
        # The defect this replaces: `tag in blob` makes `fit` match outfit,
        # fitted and benefit, inflating coverage for exactly the tags most in
        # question and rejecting them as generic FOR THE WRONG REASON.
        for text in ("a great outfit", "fitted waist", "the benefit of wool"):
            self.assertFalse(C.profile_match("fit", text), text)
        self.assertTrue(C.profile_match("fit", "a relaxed fit"))

    def test_matching_is_case_insensitive_via_normalized_input(self) -> None:
        self.assertTrue(C.profile_match("cotton", "COTTON blend"))

    def test_hyphen_and_punctuation_are_word_boundaries(self) -> None:
        self.assertTrue(C.profile_match("slim", "slim-fit jean"))
        self.assertTrue(C.profile_match("wool", "100% wool."))

    def test_a_multiword_tag_matches_as_a_phrase(self) -> None:
        self.assertTrue(C.profile_match("long sleeve", "a long sleeve top"))
        self.assertFalse(C.profile_match("long sleeve", "long ribbed sleeve"))

    def test_regex_metacharacters_are_escaped_not_interpreted(self) -> None:
        # An unescaped ".*" would match everything, classify itself generic,
        # and quietly change what every other measurement means.
        self.assertFalse(C.profile_match(".*", "anything at all"))
        self.assertTrue(C.profile_match("size (m)", "size (m) available"))

    def test_an_unbalanced_bracket_does_not_raise(self) -> None:
        for tag in ("(", "[", "*", "+", "a(b", "?"):
            self.assertIsInstance(C.profile_match(tag, "text"), bool)

    def test_an_empty_tag_never_matches(self) -> None:
        self.assertFalse(C.profile_match("", "anything"))


class PatternReuseTest(unittest.TestCase):
    def test_at_most_one_pattern_is_compiled_per_normalized_tag(self) -> None:
        # 8 tags x 30 candidates is 240 match checks; compiling per check would
        # be 240 compiles for 8 distinct patterns.
        compiles = []
        original = re.compile

        def counting(pattern, *a, **k):
            compiles.append(pattern)
            return original(pattern, *a, **k)

        window = [f"text {i}" for i in range(30)]
        C.clear_profile_pattern_cache()
        re.compile = counting
        try:
            for tag in ("cotton", "wool"):
                C.match_count(tag, window)
                C.match_count(tag, window)
        finally:
            re.compile = original
        self.assertEqual(len(compiles), 2, f"compiled {len(compiles)} times")


class MatchCountAndCoverageTest(unittest.TestCase):
    """One pass yields both; coverage is match_count / |window|."""

    def window(self, hits: int, size: int = 30) -> list[str]:
        return ["a cotton shirt"] * hits + ["a wool coat"] * (size - hits)

    def test_match_count_is_the_number_of_matching_candidates(self) -> None:
        self.assertEqual(C.match_count("cotton", self.window(7)), 7)

    def test_coverage_is_the_share_rounded_to_four_places(self) -> None:
        got = C.profile_support("cotton", self.window(10))
        self.assertEqual(got.match_count, 10)
        self.assertEqual(got.coverage, 0.3333)

    def test_the_two_come_from_one_pass(self) -> None:
        got = C.profile_support("cotton", self.window(15))
        self.assertEqual(got.coverage, round(got.match_count / 30, 4))

    def test_an_empty_window_is_zero_support_not_a_division_error(self) -> None:
        got = C.profile_support("cotton", [])
        self.assertEqual((got.match_count, got.coverage), (0, 0.0))

    def test_no_match_is_zero_and_not_absent(self) -> None:
        got = C.profile_support("silk", self.window(0))
        self.assertEqual((got.match_count, got.coverage), (0, 0.0))

    def test_every_candidate_matching_is_full_coverage(self) -> None:
        got = C.profile_support("cotton", self.window(30))
        self.assertEqual((got.match_count, got.coverage), (30, 1.0))


class PurityTest(unittest.TestCase):
    def test_the_primitives_take_no_agent_catalog_or_callback(self) -> None:
        import inspect
        for fn in (C.normalize_profile_tags, C.profile_match, C.match_count,
                   C.profile_support):
            names = set(inspect.signature(fn).parameters)
            for banned in ("agent", "catalog", "cat", "state", "index", "callback"):
                self.assertNotIn(banned, names, f"{fn.__name__}({banned})")

    def test_the_input_sequence_is_not_mutated(self) -> None:
        tags = ["  B ", "a", "a"]
        before = list(tags)
        C.normalize_profile_tags(tags)
        self.assertEqual(tags, before)


if __name__ == "__main__":
    unittest.main()
