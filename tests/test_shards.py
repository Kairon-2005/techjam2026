"""The named matrices, checked without running one.

A shard is cheap to get wrong in a way that is expensive to discover: a
scenario name that no longer exists, a config key the agent does not read, or a
grid that quietly stopped covering what a note says it covers. All three are
findable in milliseconds and all three have cost this project hours.
"""
from __future__ import annotations

import unittest

import starter.agent as A
from lab import scenarios as S
from lab import shards as SH


class ShardTest(unittest.TestCase):
    def test_every_shard_names_scenarios_that_exist(self) -> None:
        for name, shard in SH.SHARDS.items():
            for scenario in shard.scenarios:
                self.assertIn(scenario, S.BY_NAME, f"{name}: {scenario}")

    def test_no_config_sets_a_key_the_agent_does_not_read(self) -> None:
        # A key outside DEFAULTS is a silently void experiment: the agent warns
        # and ignores it, and the cell records a config it never applied. That
        # is exactly what `"route": false` was in lab/sweep.py.
        for name, shard in SH.SHARDS.items():
            for label, cfg in shard.configs.items():
                unknown = sorted(set(cfg) - set(A.DEFAULTS))
                self.assertEqual(unknown, [], f"{name}/{label}: {unknown}")

    def test_every_question_mode_config_is_a_real_mode(self) -> None:
        import starter.context as C
        for name, shard in SH.SHARDS.items():
            for label, cfg in shard.configs.items():
                mode = cfg.get("question_context_mode")
                if mode is not None:
                    self.assertIn(mode, C.QUESTION_MODES, f"{name}/{label}")

    def test_the_shadow_shard_covers_the_seven_scenarios_it_claims(self) -> None:
        # These seven are what produced the 8,483 turns quoted throughout
        # notes/32 and notes/33. A shard that lost one would report a smaller
        # comparison under the same heading.
        self.assertEqual(set(SH.SHARDS["p6b2r2-shadow"].scenarios),
                         {"clean", "vague_start", "uncooperative",
                          "override_genuine", "override_category",
                          "contradiction", "supplementary_dev"})

    def test_the_shadow_shard_runs_shadow_and_only_shadow(self) -> None:
        # Post-adoption shadow agreement is tautological, so this evidence
        # counts only while the legacy controller is still the live one. A
        # shard that also ran control here would invite quoting the wrong half.
        configs = SH.SHARDS["p6b2r2-shadow"].configs
        self.assertEqual(list(configs), ["shadow"])

    def test_the_official_shard_carries_the_compat_anchor(self) -> None:
        configs = SH.SHARDS["p6b2r2-official"].configs
        self.assertIn("compat_anchor", configs)
        self.assertEqual(configs["compat_anchor"], SH.COMPAT_ANCHOR)
        self.assertEqual(configs["compat_anchor"]["question_context_mode"], "control")

    def test_all_three_modes_are_compared_where_a_score_is_claimed(self) -> None:
        for name in ("p6b2r2-official", "p6b2r2-supplementary", "p6b2r2-robustness"):
            for mode in ("off", "shadow", "control"):
                self.assertIn(mode, SH.SHARDS[name].configs, f"{name}: {mode}")

    def test_cell_counts_match_the_grid(self) -> None:
        for name, shard in SH.SHARDS.items():
            self.assertEqual(shard.cells,
                             len(shard.scenarios) * len(shard.configs), name)
        self.assertEqual(sum(s.cells for s in SH.SHARDS.values()), 29)

    def test_supplementary_runs_alone(self) -> None:
        # 1,000 sessions across three arms. Chaining it behind other shards is
        # what starved the p6b2-Q7 run of wall clock and journalled zero rows.
        shard = SH.SHARDS["p6b2r2-supplementary"]
        self.assertEqual(shard.scenarios, ("supplementary_dev",))


if __name__ == "__main__":
    unittest.main()
