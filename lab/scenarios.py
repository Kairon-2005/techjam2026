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
import statistics
from dataclasses import dataclass, field
from typing import Callable

import evaluator.local_evaluator as E
import starter.agent as A

CATALOG, DATASET = "data/catalog.jsonl", "data/public_set.jsonl"
SUPPLEMENTARY_DEV = "data/supplementary_dev.jsonl"
# data/supplementary_holdout.jsonl is deliberately absent: it is sealed until
# Phase 4, Phase 6 and the final defaults are all frozen, and the surest way
# not to run it is to give it no Scenario to be run through.


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
    # Selects a SUBSET of the public sessions instead of mutating them.
    keep: Callable | None = None
    # A different sample file. Everything else -- evaluator, catalog, metrics
    # -- is unchanged, so a supplementary result is comparable in shape while
    # staying clearly marked as not official.
    dataset: str | None = None
    source: str = "official_public_set"
    official: bool = True
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
        # The evaluator names sessions with a fresh uuid4, so a recorded trace
        # cannot be matched back to its sample by id. initial_message is
        # called exactly once per session, in session order, so the order of
        # these calls IS the pairing. Without this, recall silently scored
        # nothing and the absent keys read as 0.000 next to HR@10 of 0.995.
        seen_samples.append(str(sample["sample_id"]))
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

    seen_samples: list[str] = []
    work = load_dataset(scenario.dataset) if scenario.dataset else samples
    if scenario.keep:
        # Segmenting the public set rather than mutating it: a per-route
        # breakdown of the SAME sessions is the only way to say whether a
        # route helped the traffic it actually handles.
        work = [s for s in work if scenario.keep(s)]
    if scenario.sample_tf:
        work = [scenario.sample_tf(s, prods, _rng_for(scenario.name, s, seed)) for s in samples]

    E.initial_message, E.customer_reply, E.materialize_hidden_fields = (
        initial_message, customer_reply, materialize)
    agent = A.Agent(CATALOG, config=config)
    seen_sessions: list[str] = []
    plain_reset = agent.reset

    def reset(session_id, profile):
        seen_sessions.append(str(session_id))
        return plain_reset(session_id, profile)

    agent.reset = reset            # type: ignore[method-assign]
    try:
        result = E.evaluate(agent, work, ids, cats, prods)
        result["telemetry"] = _telemetry(agent, work,
                                         dict(zip(seen_sessions, seen_samples)))
        return result
    finally:
        E.initial_message, E.customer_reply, E.materialize_hidden_fields = (
            orig_init, orig_reply, orig_mat)


# ---------------------------------------------------------------------------
# Scenario library
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
    return round(ordered[idx], 3)


def _telemetry(agent, samples, session_to_sample: dict | None = None) -> dict:
    """Aggregate the per-turn decision traces the agent recorded.

    Recall is computed HERE, not in the agent: it needs the ground-truth asin,
    which the agent must never see. The agent only records which candidates it
    generated; whether the right one was among them is the harness's question.
    """
    by_sample = {str(s["sample_id"]): str(s["ground_truth"]["parent_asin"])
                 for s in samples}
    truth = {sid: by_sample[sample_id]
             for sid, sample_id in (session_to_sample or {}).items()
             if sample_id in by_sample}
    turns, latency = [], []
    hits = {50: 0, 100: 0, "pool": 0}
    scored = starved_scored = starved_pool_hits = 0
    for sid, state in getattr(agent, "_sessions", {}).items():
        target = truth.get(sid)
        for trace in state.get("trace_log") or []:
            turns.append(trace)
            latency.append(trace.get("retrieval_ms", 0.0))
            cands = trace.get("candidates")
            if cands is None or target is None:
                continue
            scored += 1
            if trace.get("starved"):
                starved_scored += 1
                if target in cands:
                    starved_pool_hits += 1
            if target in cands[:50]:
                hits[50] += 1
            if target in cands[:100]:
                hits[100] += 1
            if target in cands:
                hits["pool"] += 1
    if not turns:
        return {}

    def mean(key, default=0.0):
        vals = [t.get(key, default) for t in turns if t.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    covered = [t["category_coverage"] for t in turns if t.get("category_coverage")]
    rescued = [t for t in turns if t.get("rescue_carried") is not None]
    out = {
        "turns": len(turns),
        "pool_size": mean("fused_unique"),
        "category_pool": mean("category_pool"),
        "categories": round(sum(c["categories"] for c in covered) / len(covered), 3)
                      if covered else 0.0,
        "category_entropy": round(sum(c["entropy"] for c in covered) / len(covered), 4)
                            if covered else 0.0,
        "exclusion_rate": mean("exclusion_rate"),
        "rescue_carried": round(sum(t["rescue_carried"] for t in rescued) / len(rescued), 2)
                          if rescued else 0.0,
        "rescue_needed_rate": round(sum(1 for t in rescued if t.get("rescue_needed"))
                                    / len(rescued), 4) if rescued else 0.0,
        "latency_p50": _percentile(latency, 0.50),
        "latency_p95": _percentile(latency, 0.95),
    }
    # Starvation telemetry: how often the widening fires, how deep it goes,
    # and whether the funnel then threw the widened pool away.
    starved = [t for t in turns if t.get("starved")]
    bypassed = [t for t in turns if t.get("starvation_bypass")]
    out["starved_rate"] = round(len(starved) / len(turns), 4)
    out["bypass_rate"] = round(len(bypassed) / len(turns), 4)
    out["bypass_per_session"] = round(
        len(bypassed) / max(1, len(getattr(agent, "_sessions", {}) or {})), 4)
    if starved:
        depths = [t.get("retrieval_depth", 0) for t in starved]
        pools = [t.get("fused_unique", 0) for t in starved]
        out["starved_depth"] = round(sum(depths) / len(depths), 2)
        out["starved_pool"] = round(sum(pools) / len(pools), 2)
        out["starved_latency_p95"] = _percentile(
            [t.get("retrieval_ms", 0.0) for t in starved], 0.95)
    non = [t for t in turns if not t.get("starved")]
    if non:
        out["unstarved_latency_p95"] = _percentile(
            [t.get("retrieval_ms", 0.0) for t in non], 0.95)
    out.update(_question_telemetry(agent))
    if starved_scored:
        out["starved_recall_pool"] = round(starved_pool_hits / starved_scored, 4)
    if scored:
        out.update({"recall50": round(hits[50] / scored, 4),
                    "recall100": round(hits[100] / scored, 4),
                    "recall_pool": round(hits["pool"] / scored, 4),
                    "recall_turns": scored})
    return out


def _question_telemetry(agent) -> dict:
    """What the clarification policy asked, and whether it was worth asking.

    Everything here is derived by walking each session's turn traces IN ORDER:
    a question's value is only visible in the turn after it, so "did that
    question pay" cannot be answered turn-locally.
    """
    asked_total = targeted = overgeneral = structured = 0
    bits: list[float] = [];  coverage: list[float] = []
    reductions: list[float] = []
    pool_before: list[int] = [];  pool_after: list[int] = []
    dry = answered = 0
    attributes: dict[str, int] = {}
    first_constraint: list[int] = []
    INFORMATIVE = {"informative", "override"}
    for state in (getattr(agent, "_sessions", {}) or {}).values():
        log = state.get("trace_log") or []
        seen_constraint = None
        for i, trace in enumerate(log):
            if "asked" not in trace:
                continue
            asked_total += 1
            attributes[trace["asked"]] = attributes.get(trace["asked"], 0) + 1
            if trace.get("asked_targeted"):
                targeted += 1
                bits.append(trace.get("question_bits", 0.0))
                coverage.append(trace.get("question_coverage", 0.0))
            overgeneral += bool(trace.get("overgeneral"))
            structured += bool(trace.get("structured_options"))
            nxt = log[i + 1] if i + 1 < len(log) else None
            if nxt is None:
                continue
            # The NEXT turn carries the outcome of the reply to THIS question.
            if str(nxt.get("reply_outcome", "")).lower() in INFORMATIVE:
                answered += 1
                before, after = trace.get("fused_unique", 0), nxt.get("fused_unique", 0)
                pool_before.append(before)
                pool_after.append(after)
                if before:
                    reductions.append((before - after) / before)
            else:
                dry += 1
            if seen_constraint is None and nxt.get("slots_active", 0) > trace.get("slots_active", 0):
                seen_constraint = i + 1
        if seen_constraint is not None:
            first_constraint.append(seen_constraint)
    if not asked_total:
        return {}
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else 0.0   # noqa: E731
    graded = dry + answered
    return {
        "questions": asked_total,
        "targeted_question_rate": round(targeted / asked_total, 4),
        "fallback_to_other_rate": round((asked_total - targeted) / asked_total, 4),
        "overgeneral_trigger_rate": round(overgeneral / asked_total, 4),
        "structured_option_rate": round(structured / asked_total, 4),
        "estimated_information_gain": mean(bits),
        "question_facet_coverage": mean(coverage),
        "dry_question_rate": round(dry / graded, 4) if graded else 0.0,
        "answerable_reply_rate": round(answered / graded, 4) if graded else 0.0,
        "pool_size_before_question": mean(pool_before),
        "pool_size_next_turn": mean(pool_after),
        "median_pool_reduction_after_answer": round(statistics.median(reductions), 4)
                                              if reductions else 0.0,
        "turns_to_first_new_constraint": mean(first_constraint),
        "question_attribute_counts": dict(sorted(attributes.items(),
                                                 key=lambda kv: -kv[1])),
    }


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
_PRODS: dict = {}

# Values a customer might reject. Chosen so a target lacking the value gains
# information when competitors carrying it are demoted.
_REJECTABLE = ("polyester", "nylon", "wool", "silk", "leather", "spandex", "rayon")


def _reply_negative_preference(sample, attribute, disclosed, bu, rng):
    """Half the time, state a rejection of something the TARGET does not have.

    This is genuine extra information: every candidate carrying the rejected
    value is wrong, so a working negative channel should help and a broken one
    (or a mis-scoped negation) should hurt.
    """
    if rng.random() >= 0.5:
        return None
    target = _PRODS.get(str(sample["ground_truth"]["parent_asin"])) or {}
    blob = " ".join(str(target.get(f) or "") for f in
                    ("title", "features", "details", "description")).lower()
    options = [v for v in _REJECTABLE if v not in blob]
    if not options:
        return None
    value = rng.choice(options)
    return rng.choice([
        f"No {value}, please.",
        f"Please avoid {value}.",
        f"I don't want {value}.",
    ]), bu


# CONSUMED. This was the sealed scope holdout. It is no longer one, and it
# cannot become one again:
#   * it was used to measure w_neg 0 vs 2 (fb46755);
#   * it surfaced the "steer clear" defect, which has now been fixed;
#   * its wording was read and analysed by two sessions working in parallel.
# A holdout answers "does this generalise" exactly once. Kept here because
# negation_scope_holdout's historical rows must remain reproducible, and
# because these phrasings are still useful as regression material -- but no
# result from it may be quoted as evidence of generalisation again.
# The live sealed set is SEALED_NEGATION_V2.
SCOPE_PHRASINGS = (
    ("Anything except polyester works.", "negative", "polyester"),
    ("I'd rather steer clear of wool.", "negative", "wool"),
    ("Nothing besides cotton, ideally.", "positive", "cotton"),
    ("Other than nylon, I'm open.", "negative", "nylon"),
    ("Just no plastic feel.", "negative", "plastic"),
    ("Cotton and nothing else.", "positive", "cotton"),
)


def _reply_negation_scope(sample, attribute, disclosed, bu, rng):
    if rng.random() >= 0.5:
        return None
    return rng.choice([text for text, _, _ in SCOPE_PHRASINGS]), bu


# ---- negative-evidence development sets ---------------------------------
# DEVELOPMENT wording. Free to tune against; deliberately disjoint from both
# sealed sets. Each entry is (text, expected polarity for the named value).
DEV_SCOPE = (
    # restrictive positives -- "nothing but X" means ONLY x
    ("Nothing but leather.", 1, "leather"),
    ("Only cotton please.", 1, "cotton"),
    ("Not only leather.", 1, "leather"),
    ("Leather and nothing else.", 1, "leather"),
    # exceptives -- "anything but X" means EVERYTHING EXCEPT x
    ("Anything but polyester.", -1, "polyester"),
    ("Anything other than nylon.", -1, "nylon"),
    ("Everything except plastic works.", -1, "plastic"),
    # plain rejections
    ("No polyester, please.", -1, "polyester"),
    ("Avoid anything synthetic.", -1, "synthetic"),
    ("Please steer clear of wool.", -1, "wool"),
    ("Allergic to wool.", -1, "wool"),
    ("Stay away from plastic.", -1, "plastic"),
)
# Hedges: a negation token with no constraint behind it. Must extract NOTHING.
DEV_HEDGES = (
    "I'm not sure whether leather matters.",
    "No idea if cotton is better.",
    "I don't know about the wool, honestly.",
    "Hard to say whether polyester bothers me.",
)


def _reply_negative_paraphrase(sample, attribute, disclosed, bu, rng):
    """A rejection in wording the original negative scenario never used."""
    if rng.random() >= 0.5:
        return None
    target = _PRODS.get(str(sample["ground_truth"]["parent_asin"])) or {}
    blob = " ".join(str(target.get(f) or "") for f in
                    ("title", "features", "details", "description")).lower()
    options = [v for v in _REJECTABLE if v not in blob]
    if not options:
        return None
    value = rng.choice(options)
    return rng.choice([
        f"Anything but {value}.",
        f"Please steer clear of {value}.",
        f"Stay away from {value}.",
        f"Everything except {value} works.",
        f"{value.capitalize()} is one thing I'd rule out.",
    ]), bu


def _reply_negation_scope_dev(sample, attribute, disclosed, bu, rng):
    """Development scope set: restrictive positives mixed with exceptives."""
    if rng.random() >= 0.5:
        return None
    return rng.choice([text for text, _, _ in DEV_SCOPE]), bu


def _reply_false_negation(sample, attribute, disclosed, bu, rng):
    """A hedge carrying a negation token but no constraint.

    A parser that reads "I'm not sure whether leather matters" as a rejection
    of leather invents evidence, with the polarity inverted, out of an
    admission of ignorance. The cost lands on whichever candidates happen to
    be leather -- including, half the time, the right one.
    """
    if rng.random() >= 0.5:
        return None
    return rng.choice(DEV_HEDGES), bu


def _reply_mixed_polarity(sample, attribute, disclosed, bu, rng):
    """One message carrying a requirement and a rejection at once."""
    if rng.random() >= 0.5:
        return None
    target = _PRODS.get(str(sample["ground_truth"]["parent_asin"])) or {}
    blob = " ".join(str(target.get(f) or "") for f in
                    ("title", "features", "details", "description")).lower()
    wanted = next((v for v in _REJECTABLE if v in blob), None)
    rejected = [v for v in _REJECTABLE if v not in blob]
    if not wanted or not rejected:
        return None
    return f"{wanted.capitalize()}, but no {rng.choice(rejected)}.", bu


# SEALED v2, written after every known defect was fixed. Shares no wording
# family with SCOPE_PHRASINGS or DEV_SCOPE.
#
# The honest description of its status: these phrasings were AUTHORED for this
# purpose and have never been evaluated, per-phrase or end-to-end. Several
# were chosen precisely because they were expected to fail -- "barring",
# "save for", "hard pass", "off the table" are families the fixed patterns do
# not cover. Predicting a gap is not tuning against it, but it does mean the
# prediction must be written down BEFORE the set is run; see
# notes/09-phase15b-prereg.md.
#
# THE CONTRACT: one end-to-end evaluation, at the end of Phase 1.5B. No
# per-phrase diagnostics, ever -- that is what burned the first set.
# Expected polarity 0 means "extract nothing".
SEALED_NEGATION_V2 = (
    ("Barring nylon, I'm flexible.", -1, "nylon"),
    ("Save for polyester, anything goes.", -1, "polyester"),
    ("Wool is a hard pass for me.", -1, "wool"),
    ("Plastic is off the table.", -1, "plastic"),
    ("Cotton, full stop.", 1, "cotton"),
    ("Strictly leather, please.", 1, "leather"),
    ("Purely cotton.", 1, "cotton"),
    ("I'm indifferent about the wool.", 0, "wool"),
)


def _reply_sealed_negation_v2(sample, attribute, disclosed, bu, rng):
    if rng.random() >= 0.5:
        return None
    return rng.choice([text for text, _, _ in SEALED_NEGATION_V2]), bu


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


# HOLDOUT phrasing. Deliberately shares no wording with _reply_uncooperative:
# no "hard to say", no "not sure", no "show me more". If UNCERTAIN detection
# was tuned to the development phrases, these leak into the query instead and
# the holdout score collapses -- which is the point of keeping them separate.
HOLDOUT_STONEWALLS = (
    "Honestly I could go either way on that.",
    "That's a tough one, I've got no strong feelings.",
    "Whatever you'd recommend is fine by me.",
    "I haven't really thought about it that much.",
    "Could I see a few different ones?",
    "Ehh, nothing jumps out at me.",
)


def _reply_uncooperative_holdout(sample, attribute, disclosed, bu, rng):
    if rng.random() < 0.5:
        return rng.choice(HOLDOUT_STONEWALLS), bu
    return None


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
    Scenario("uncooperative_holdout",
             "HOLDOUT: stonewalling in phrasing never used during development",
             reply=_reply_uncooperative_holdout,
             notes="shares no wording with the tuning scenario; validates detection generalises"),
    Scenario("negative_preference",
             "Pillar II: does the negative-evidence channel use a stated rejection?",
             reply=_reply_negative_preference,
             notes="rejects a value the target does not have, so it is pure signal"),
    Scenario("negation_scope_holdout",
             "CONSUMED (was sealed): negation scope -- kept for reproducibility",
             reply=_reply_negation_scope,
             notes="consumed by fb46755 and by the 'steer clear' fix; "
                   "no longer evidence of generalisation -- see SEALED_NEGATION_V2"),
    # ---- supplementary, non-official ------------------------------------
    # Robustness VETO evidence only: a supplementary gain can never offset an
    # official regression, and a supplementary collapse can veto a default.
    # The sealed holdout is never registered here -- it has no Scenario, so it
    # cannot be run by accident.
    Scenario("supplementary_dev",
             "NON-OFFICIAL: 1000 catalog-synthetic sessions, robustness veto only",
             dataset=SUPPLEMENTARY_DEV, source="supplementary_catalog_synthetic",
             official=False,
             notes="targets disjoint from the public set and from the sealed holdout"),
    *[Scenario(f"supplementary_{name}",
               f"NON-OFFICIAL: supplementary {name} slice",
               dataset=SUPPLEMENTARY_DEV, source="supplementary_catalog_synthetic",
               official=False,
               keep=(lambda st: (lambda s: s.get("scenario_type") == st))(name))
       for name in ("buying", "browsing", "intent_override", "boundary")],
    Scenario("clean_buying",
             "Pillar I: the 80 public sessions the Buying route actually handles",
             keep=lambda s: s.get("scenario_type") == "buying"),
    Scenario("clean_browsing",
             "Pillar I: the 80 public sessions the Browsing route actually handles",
             keep=lambda s: s.get("scenario_type") == "browsing"),
    Scenario("negative_preference_paraphrase",
             "Pillar II: rejections worded outside the tuning scenario's vocabulary",
             reply=_reply_negative_paraphrase,
             notes="same signal as negative_preference, different surface form"),
    Scenario("negative_scope",
             "Pillar II: development scope set -- restrictive positives vs exceptives",
             reply=_reply_negation_scope_dev,
             notes="DEV set, free to tune against; disjoint from both sealed sets"),
    Scenario("false_negation",
             "Pillar II: a negation token inside a hedge must not become evidence",
             reply=_reply_false_negation,
             notes="'not sure whether leather matters' states no constraint"),
    Scenario("multiple_positive_and_negative",
             "Pillar II: one message carrying a requirement and a rejection",
             reply=_reply_mixed_polarity),
    Scenario("negation_holdout_v2",
             "SEALED v2: negation wording unused anywhere in development",
             reply=_reply_sealed_negation_v2,
             notes="SCOPE_PHRASINGS is consumed; this replaces it as the generalisation test"),
    Scenario("contradiction", "robustness: a stated constraint that is false of the target",
             sample_tf=_tf_contradiction),
    Scenario("profile_informative", "Pillar III: can personalization be exploited at all?",
             sample_tf=_tf_profile_informative),
]

BY_NAME = {s.name: s for s in LIBRARY}


_DATASETS: dict[str, list] = {}


def load_dataset(path: str) -> list:
    """Samples from an alternate file, cached. The catalog is shared."""
    if path not in _DATASETS:
        _DATASETS[path] = E.load_jsonl(path)
    return _DATASETS[path]


def load():
    """Samples + catalog index, with the donor pool primed."""
    samples = E.load_jsonl(DATASET)
    ids, cats, prods = E.catalog_index(CATALOG)
    _DONORS.clear()
    _DONORS.extend(str(s["ground_truth"]["parent_asin"]) for s in samples)
    _PRODS.clear()
    _PRODS.update(prods)
    return samples, ids, cats, prods
