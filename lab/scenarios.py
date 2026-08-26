"""Capability scenarios: evaluate what the public set structurally cannot test.

The public simulator is a weak proxy. Concretely, it CANNOT test:

  * intent override -- `behavior_for` draws BOTH old_value and new_value from
    the target product, so the "obsolete" preference is still evidence for the
    right answer and never forgetting is free;
  * personalization -- `purchase_frequency` is identical across all 200
    sessions and `preference_tags` is 9 generic words;
  * vague browsing -- every opening message names a category;
  * an uncooperative customer -- replies are always well-formed;
  * contradiction -- every stated constraint is true of the target.

Tuning only against that proxy optimises a system for conditions the real task
will not have. This module defines each missing condition as a Scenario: a set
of hooks over the official evaluation loop that changes ONE thing, leaving the
harness, metrics and scoring untouched so results stay comparable.

A Scenario is deliberately not a fork of the evaluator. Hooks return None to
mean "use the official behaviour", so every scenario is a diff against the real
thing rather than a re-implementation that could drift.

Usage:  python3 -m lab.capability          # the full scorecard
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable

import evaluator.local_evaluator as E
import starter.agent as A

CATALOG, DATASET = "data/catalog.jsonl", "data/public_set.jsonl"


@dataclass
class Scenario:
    """One capability probe.

    sample_tf  (sample, prods, rng) -> sample      mutate the session up front
    mutate     (sample, card, beh, prods, rng) -> (card, beh)
    init       (sample, category, disclosed, rng) -> str | None
    reply      (sample, attribute, disclosed, bu, rng) -> tuple[str, bool] | None
    """
    name: str
    probes: str
    sample_tf: Callable | None = None
    mutate: Callable | None = None
    init: Callable | None = None
    reply: Callable | None = None
    notes: str = ""


def _rng_for(scenario: str, sample: dict, seed: int) -> random.Random:
    return random.Random(f"{seed}\0{scenario}\0{sample.get('sample_id', '')}")


def run(scenario: Scenario, config: dict, samples, ids, cats, prods, seed: int = 7) -> dict:
    """Run one scenario against one agent config. Restores the evaluator after."""
    orig_init, orig_reply = E.initial_message, E.customer_reply
    orig_mat = E.materialize_hidden_fields

    def materialize(sample, products):
        card, beh = orig_mat(sample, products)
        if scenario.mutate:
            card, beh = scenario.mutate(sample, card, beh, products,
                                        _rng_for(scenario.name, sample, seed))
        return card, beh

    def initial_message(sample, category, disclosed):
        if scenario.init:
            out = scenario.init(sample, category, disclosed,
                                _rng_for(scenario.name, sample, seed))
            if out is not None:
                return out
        return orig_init(sample, category, disclosed)

    def customer_reply(sample, ask_attribute, disclosed, boundary_used):
        if scenario.reply:
            out = scenario.reply(sample, ask_attribute, disclosed, boundary_used,
                                 _rng_for(scenario.name, sample, seed))
            if out is not None:
                return out
        return orig_reply(sample, ask_attribute, disclosed, boundary_used)

    work = samples
    if scenario.sample_tf:
        work = [scenario.sample_tf(s, prods, _rng_for(scenario.name, s, seed)) for s in samples]

    E.initial_message, E.customer_reply, E.materialize_hidden_fields = (
        initial_message, customer_reply, materialize)
    try:
        return E.evaluate(A.Agent(CATALOG, config=config), work, ids, cats, prods)
    finally:
        E.initial_message, E.customer_reply, E.materialize_hidden_fields = (
            orig_init, orig_reply, orig_mat)


# ---------------------------------------------------------------------------
# Scenario library
# ---------------------------------------------------------------------------

def _foreign_constraint(sample, prods, rng, donors, card) -> str:
    """A hard constraint belonging to somebody else's product."""
    target = str(sample["ground_truth"]["parent_asin"])
    owned = card["hard_constraints"] + card["soft_preferences"]
    for _ in range(40):
        other = prods[rng.choice(donors)]
        if str(other.get("parent_asin")) == target:
            continue
        donor = E.intent_card(other)
        value = donor["hard_constraints"][0] if donor["hard_constraints"] else ""
        if value and value not in owned:
            return value
    return ""


_DONORS: list[str] = []


def _mut_override_genuine(sample, card, beh, prods, rng):
    """The superseded preference becomes genuinely obsolete (another product's)."""
    if not beh.get("override"):
        return card, beh
    stale = _foreign_constraint(sample, prods, rng, _DONORS, card)
    if stale:
        beh = {**beh, "override": {**beh["override"], "old_value": stale}}
    return card, beh


def _mut_override_category(sample, card, beh, prods, rng):
    """A category-level pivot: 'forget boots, I want running shoes'."""
    if not beh.get("override"):
        return card, beh
    target = str(sample["ground_truth"]["parent_asin"])
    for _ in range(40):
        other = prods[rng.choice(_DONORS)]
        if str(other.get("parent_asin")) == target:
            continue
        stale_cat = E.coarse_category([str(v) for v in other.get("categories") or []])
        if stale_cat and stale_cat != "clothing item":
            new_value = beh["override"]["new_value"]
            return card, {**beh, "override": {
                **beh["override"],
                "old_value": f"I want {stale_cat}.",
                "message": (f"Actually, forget {stale_cat} entirely - that was the wrong "
                            f"direction. What I need is: {new_value}."),
            }}
    return card, beh


def _init_vague(sample, category, disclosed, rng):
    """Maximally under-specified opening: no category at all."""
    if sample["scenario_type"] in ("browsing", "boundary"):
        return rng.choice([
            "I'm just looking around, nothing specific in mind yet.",
            "Hi, I need to buy a gift but I'm not sure what.",
            "Show me something good.",
        ])
    return None


def _reply_uncooperative(sample, attribute, disclosed, bu, rng):
    """Half the time the customer stonewalls WITHOUT consuming a constraint."""
    if rng.random() < 0.5:
        return rng.choice([
            "Hmm, hard to say really.",
            "I'm not sure, what do you think?",
            "Can you just show me more options?",
        ]), bu
    return None


def _tf_contradiction(sample, prods, rng):
    """Prepend a constraint that is FALSE of the target, stated as a hard one."""
    card = E.intent_card(prods[str(sample["ground_truth"]["parent_asin"])])
    bogus = _foreign_constraint(sample, prods, rng, _DONORS, card)
    if not bogus:
        return sample
    poisoned = {**card, "hard_constraints": [bogus] + card["hard_constraints"][:1]}
    return {**sample, "intent_card": poisoned,
            "behavior": E.behavior_for(str(sample["scenario_type"]), poisoned,
                                       random.Random(str(sample.get("sample_id"))))}


_TOKEN = re.compile(r"[a-z]{4,}", re.I)


def _tf_profile_informative(sample, prods, rng):
    """Give the profile REAL signal: distinctive tokens from the target itself.

    Not a claim about the private set -- a test of whether the architecture can
    exploit personalization at all when the signal exists, which separates
    "we did not build it" from "the data has none".
    """
    product = prods[str(sample["ground_truth"]["parent_asin"])]
    blob = " ".join(str(product.get(f) or "") for f in ("title", "features", "details"))
    tokens = [t.lower() for t in _TOKEN.findall(blob)]
    seen, tags = set(), []
    for tok in tokens:
        if tok not in seen and tok not in ("with", "that", "this", "from", "your"):
            seen.add(tok)
            tags.append(tok)
        if len(tags) >= 5:
            break
    profile = {**sample["user_profile"], "preference_tags": tags or ["fit"],
               "summary": "Prior purchases emphasize " + ", ".join(tags) + "."}
    return {**sample, "user_profile": profile}


LIBRARY: list[Scenario] = [
    Scenario("clean", "control -- the official public set, unmodified"),
    Scenario("override_genuine", "Pillar II: slot erasure when the old value is truly obsolete",
             mutate=_mut_override_genuine,
             notes="public set draws old_value from the target, so it cannot test this"),
    Scenario("override_category", "Pillar II: a category-level pivot mid-conversation",
             mutate=_mut_override_category),
    Scenario("vague_start", "Pillar II: over-generality -- opening names no category",
             init=_init_vague),
    Scenario("uncooperative", "Pillar II: fallback when the customer will not answer",
             reply=_reply_uncooperative),
    Scenario("contradiction", "robustness: a stated constraint that is false of the target",
             sample_tf=_tf_contradiction),
    Scenario("profile_informative", "Pillar III: can personalization be exploited at all?",
             sample_tf=_tf_profile_informative),
]

BY_NAME = {s.name: s for s in LIBRARY}


def load():
    """Samples + catalog index, with the donor pool primed."""
    samples = E.load_jsonl(DATASET)
    ids, cats, prods = E.catalog_index(CATALOG)
    _DONORS.clear()
    _DONORS.extend(str(s["ground_truth"]["parent_asin"]) for s in samples)
    return samples, ids, cats, prods
