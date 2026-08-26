"""Paraphrase stress test.

REGRESSION SUITE for our paraphrase handling — NOT an unbiased robustness estimate:
the agent's cue/noise regexes were developed against these same three styles, so
post-fix scores are training performance on this suite. The honest unseen-style
estimate is the PRE-fix scores (0.776 / 0.807 / 0.671).

ASSUMPTION (ours, not the spec's): constraint payload strings stay verbatim and only
surrounding chrome is paraphrased. The spec sentence "If natural-language paraphrasing
is added by the organizer, it cannot decide correctness" is a statement about scoring
(hits are exact ASIN matches), not a promise about message wording. If the organizer
paraphrases the payloads themselves, the exact-match features (w_phrase/w_exact/
w_field) degrade to zero and the popularity+category+IDF floor (~0.67-0.87) applies.
"""
from __future__ import annotations

import statistics
import sys

import evaluator.local_evaluator as E
import starter.agent as A


_IS_V2 = __import__('sys').argv[1:2] == ['v2']


def make_styles():
    def s0_identity(kind, **kw):
        return None  # keep original

    def s1_casual(kind, category=None, constraint=None, attribute=None, matches=None, new_value=None):
        if kind == "init_buying":
            return f"Hi! I need {category}. It really must have {constraint}."
        if kind == "init_browsing":
            return f"Just browsing for {category} right now, nothing specific."
        if kind == "init_override":
            return f"Hi, show me {category}. {constraint}"
        if kind == "reply_match":
            return f"Hmm, I'd say what I care about is {'; '.join(matches)}."
        if kind == "reply_none":
            return f"No real preference on {attribute} to be honest."
        if kind == "reply_noattr":
            return "Not quite what I want. Maybe ask me something specific?"
        if kind == "boundary":
            return f"Whatever you think on {attribute}, you pick."
        if kind == "override":
            return f"Change of plans - forget what I said before. Now I want: {new_value}."
        return None

    def s2_terse(kind, category=None, constraint=None, attribute=None, matches=None, new_value=None):
        if kind == "init_buying":
            return f"{category}. Requirement: {constraint}"
        if kind == "init_browsing":
            return f"{category}?"
        if kind == "init_override":
            return f"{category}. {constraint}"
        if kind == "reply_match":
            return f"Important: {'; '.join(matches)}"
        if kind == "reply_none":
            return f"no {attribute} preference"
        if kind == "reply_noattr":
            return "wrong direction. ask something."
        if kind == "boundary":
            return f"any {attribute} is fine"
        if kind == "override":
            return f"scratch that. new requirement: {new_value}"
        return None

    def s3_verbose(kind, category=None, constraint=None, attribute=None, matches=None, new_value=None):
        if kind == "init_buying":
            return (f"Hello there, I have been searching everywhere for {category} and the one "
                    f"thing that is absolutely essential for me is {constraint}, hope you can help.")
        if kind == "init_browsing":
            return (f"Hello, I am just starting to shop around for {category} and honestly I have "
                    f"not made up my mind about anything yet.")
        if kind == "init_override":
            return f"Hello, I am shopping for {category} today. {constraint}"
        if kind == "reply_match":
            return (f"That is a good question, let me think... I suppose the things that really "
                    f"matter to me would be {'; '.join(matches)}, if that helps.")
        if kind == "reply_none":
            return f"You know, I honestly could not tell you my {attribute} preference, I am easy."
        if kind == "reply_noattr":
            return "These are not really working for me so far. Could you ask me about one particular aspect?"
        if kind == "boundary":
            return f"I really do not mind about {attribute} at all, I will trust your judgment on this one."
        if kind == "override":
            return (f"Actually, you know what, please disregard my earlier preference entirely. "
                    f"What I truly need is: {new_value}.")
        return None

    return {"identity": s0_identity, "casual": s1_casual, "terse": s2_terse, "verbose": s3_verbose}


def run_style(style_fn, agent_config):
    orig_initial, orig_reply = E.initial_message, E.customer_reply

    def initial_message(sample, category, disclosed):
        scenario = sample["scenario_type"]
        if scenario == "buying" and sample["intent_card"].get("hard_constraints"):
            constraint = str(sample["intent_card"]["hard_constraints"][0])
            out = style_fn("init_buying", category=category, constraint=constraint)
            if out is not None:
                disclosed.add(constraint)
                return out
        elif scenario == "intent_override":
            old = str(sample["behavior"]["override"]["old_value"])
            out = style_fn("init_override", category=category, constraint=old)
            if out is not None:
                return out
        else:
            out = style_fn("init_browsing", category=category)
            if out is not None:
                return out
        return orig_initial(sample, category, disclosed)

    def customer_reply(sample, ask_attribute, disclosed, boundary_used):
        attribute = ask_attribute if isinstance(ask_attribute, str) else None
        if sample["scenario_type"] == "boundary" and not boundary_used and attribute:
            out = style_fn("boundary", attribute=attribute)
            return (out, True) if out is not None else orig_reply(sample, ask_attribute, disclosed, boundary_used)
        if not attribute:
            out = style_fn("reply_noattr")
            return (out, boundary_used) if out is not None else orig_reply(sample, ask_attribute, disclosed, boundary_used)
        if attribute not in E.ALLOWED_ATTRIBUTES:
            attribute = "other"
        constraints = [*map(str, sample["intent_card"].get("hard_constraints", [])),
                       *map(str, sample["intent_card"].get("soft_preferences", []))]
        matches = [v for v in constraints if v not in disclosed
                   and (attribute == "other" or E.classify_constraint(v) == attribute)][:2]
        if not matches:
            out = style_fn("reply_none", attribute=attribute)
            return (out, boundary_used) if out is not None else orig_reply(sample, ask_attribute, disclosed, boundary_used)
        out = style_fn("reply_match", matches=matches)
        if out is None:
            return orig_reply(sample, ask_attribute, disclosed, boundary_used)
        disclosed.update(matches)
        return out, boundary_used

    samples = E.load_jsonl("data/public_set.jsonl")
    ids, cats, prods = E.catalog_index("data/catalog.jsonl")

    # Patch the override message too (it lives inside behavior_for output at materialize time).
    orig_mat = E.materialize_hidden_fields
    def materialize(sample, products):
        card, beh = orig_mat(sample, products)
        if beh.get("override"):
            new_value = beh["override"]["new_value"]
            out = style_fn("override", new_value=new_value)
            if out is not None:
                beh = {**beh, "override": {**beh["override"], "message": out}}
        return card, beh

    E.initial_message, E.customer_reply, E.materialize_hidden_fields = initial_message, customer_reply, materialize
    try:
        res = E.evaluate(A.Agent("data/catalog.jsonl", config=agent_config), samples, ids, cats, prods)
    finally:
        E.initial_message, E.customer_reply, E.materialize_hidden_fields = orig_initial, orig_reply, orig_mat
    return res


def main():
    cfg = {}  # defaults = tuned best
    print(f"{'style':<10}{'score':>8}{'HR@10':>8}{'MRR':>8}{'MTTC':>7}")
    print("-" * 41)
    for name, fn in make_styles().items():
        r = run_style(fn, cfg)
        print(f"{name:<10}{r['recommended_technical_score']:>8.4f}{r['hit_rate_at_10']:>8.3f}"
              f"{r['mrr']:>8.3f}{r['mttc']:>7.2f}", flush=True)


if __name__ == "__main__" and not _IS_V2:
    main()


# ---------------------------------------------------------------------------
# Stress v2: PAYLOAD REWORDING. Chrome templates stay original (parser extracts
# fine); the constraint strings themselves are mechanically reworded, so the
# exact-match features (w_phrase / w_exact / w_field) die BY CONSTRUCTION.
# These transforms were written before any agent change that targets them and
# never inform regex/stopword tuning — they test ranking robustness only.
# ---------------------------------------------------------------------------
import re as _re

_WORD = _re.compile(r"[A-Za-z0-9]+")
_PREFIX = _re.compile(r"^(?:material|color|size|style)\s*:\s*", _re.I)
_PCT = _re.compile(r"\b100%\s*", _re.I)


def _rw_soft(value: str) -> str:
    """'Material:alloy' -> 'made of alloy'; strip boilerplate, lowercase."""
    v = _PREFIX.sub("", value)
    v = _PCT.sub("", v).strip().lower()
    return f"made of {v}" if len(v.split()) <= 3 else v


def _rw_shuffle(value: str) -> str:
    """Deterministic token reversal, punctuation dropped: 'Stainless Steel Band' -> 'band steel stainless'."""
    toks = _WORD.findall(value.lower())
    return " ".join(reversed(toks)) if toks else value


def _rw_drop(value: str) -> str:
    """Keep only the two longest tokens: aggressive truncation."""
    toks = sorted(_WORD.findall(value.lower()), key=len, reverse=True)[:2]
    return " ".join(toks) if toks else value


def make_payload_styles():
    def wrap(fn):
        def style(kind, category=None, constraint=None, attribute=None, matches=None, new_value=None):
            if kind == "init_buying":
                return f"I'm looking for {category}. A key requirement is: {fn(constraint)}."
            if kind == "reply_match":
                return "For that, what matters is: " + "; ".join(fn(m) for m in matches) + "."
            if kind == "override":
                return f"Actually, ignore my earlier preference. What I need is: {fn(new_value)}."
            return None  # everything else keeps the original template
        return style
    return {"payload_soft": wrap(_rw_soft),
            "payload_shuffle": wrap(_rw_shuffle),
            "payload_drop": wrap(_rw_drop)}


def main_v2(extra_cfg=None):
    cfg = extra_cfg or {}
    print(f"{'style':<16}{'score':>8}{'HR@10':>8}{'MRR':>8}{'MTTC':>7}")
    print("-" * 47)
    for name, fn in make_payload_styles().items():
        r = run_style(fn, cfg)
        print(f"{name:<16}{r['recommended_technical_score']:>8.4f}{r['hit_rate_at_10']:>8.3f}"
              f"{r['mrr']:>8.3f}{r['mttc']:>7.2f}", flush=True)


if __name__ == "__main__" and _IS_V2:
    import json as _json
    import sys as _sys
    main_v2(_json.loads(_sys.argv[2]) if len(_sys.argv) > 2 else None)
