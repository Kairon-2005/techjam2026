"""Does the agent survive a *genuine* intent override?

The public simulator never tests this. In evaluator.behavior_for the override's
new_value is `hard_constraints[0]` and old_value is `soft_preferences[-1]` --
BOTH are derived from the target product. So the "obsolete" preference the
customer tells you to forget is still evidence FOR the right answer, and simply
keeping it is free. That is why on_override="keep" wins on the public set.

This harness replaces old_value with a constraint lifted from a DIFFERENT
product, so the superseded preference is actively misleading, and leaves
new_value pointing at the true target. Retaining the stale slot should now cost
you. Everything else -- templates, wording, scoring -- is untouched, so the only
variable is the agent's state-management policy.

Usage:  python3 -m lab.override_stress
"""
from __future__ import annotations

import random
import statistics
import sys

import evaluator.local_evaluator as E
import starter.agent as A

CATALOG, DATASET = "data/catalog.jsonl", "data/public_set.jsonl"


def run(agent_config: dict, samples, ids, cats, prods, seed: int = 7) -> dict:
    orig_mat = E.materialize_hidden_fields

    def materialize(sample, products):
        card, beh = orig_mat(sample, products)
        if not beh.get("override"):
            return card, beh
        rng = random.Random(f"{seed}\0{sample.get('sample_id','')}")
        # A constraint belonging to somebody else's product: genuinely obsolete.
        for _ in range(40):
            other = prods[rng.choice(donors)]
            if str(other.get("parent_asin")) == str(sample["ground_truth"]["parent_asin"]):
                continue
            donor_card = E.intent_card(other)
            stale = donor_card["hard_constraints"][0] if donor_card["hard_constraints"] else ""
            if stale and stale not in card["hard_constraints"] + card["soft_preferences"]:
                beh = {**beh, "override": {**beh["override"], "old_value": stale}}
                break
        return card, beh

    donors = [str(s["ground_truth"]["parent_asin"]) for s in samples]
    E.materialize_hidden_fields = materialize
    try:
        return E.evaluate(A.Agent(CATALOG, config=agent_config), samples, ids, cats, prods)
    finally:
        E.materialize_hidden_fields = orig_mat


POLICIES = {
    "keep (current default)": {"on_override": "keep"},
    "erase":                  {"on_override": "erase"},
    "decay (tail)":           {"on_override": "decay"},
    "decay_head (original)":  {"on_override": "decay_head"},
    "slot (selective)":       {"on_override": "slot"},
}


def main(seeds: tuple[int, ...] = (7, 11, 23, 42, 101)) -> None:
    """Only 30 of the 200 sessions are intent_override, so one seed is noise.

    Each seed redraws which foreign constraint becomes the stale preference;
    we average over several so the comparison between policies is readable.
    """
    samples = E.load_jsonl(DATASET)
    ids, cats, prods = E.catalog_index(CATALOG)
    print(f"seeds={list(seeds)}  (30 intent_override sessions per seed)\n")
    print(f"{'policy':<24}{'score':>9}{'sd':>7}{'hr@10':>8}{'mrr':>8}"
          f"{'| override hr':>14}{'ov mrr':>9}{'ov mttc':>9}")
    for name, cfg in POLICIES.items():
        runs = [run(cfg, samples, ids, cats, prods, seed=s) for s in seeds]
        ovs = [r["scenario_metrics"].get("intent_override", {}) for r in runs]
        f = lambda xs: statistics.fmean(xs)
        sd = statistics.pstdev([r["recommended_technical_score"] for r in runs])
        print(f"{name:<24}{f([r['recommended_technical_score'] for r in runs]):>9.4f}"
              f"{sd:>7.4f}{f([r['hit_rate_at_10'] for r in runs]):>8.3f}"
              f"{f([r['mrr'] for r in runs]):>8.3f}"
              f"{f([o.get('hit_rate_at_10', 0) for o in ovs]):>14.3f}"
              f"{f([o.get('mrr', 0) for o in ovs]):>9.3f}"
              f"{f([o.get('mttc', 0) for o in ovs]):>9.2f}", flush=True)


if __name__ == "__main__":
    main()
