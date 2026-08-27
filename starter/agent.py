"""Conversational product-search agent -- the public coordination layer.

The package is split for readability, not for isolation:

    evidence.py   what the customer said, and how much of it to believe
    catalog.py    the corpus and every index built over it
    dialogue.py   session state, override, starvation, question utility
    retrieval.py  safe pool, route planes, dense/RRF, funnel, rerank
    agent.py      DEFAULTS, PROFILES, and the Agent that composes them

DialogueMixin and RetrievalMixin do not import each other; they call across
through `self`, and each module's header lists exactly which host capabilities
it relies on. That keeps the IMPORT graph acyclic. It does NOT make the DOMAIN
graph acyclic -- retrieval still asks dialogue what is credible, and dialogue
still asks retrieval for route configuration. This split makes those edges
visible and cheap to audit; it does not remove them. Strict interfaces,
dependency inversion, and actually eliminating the bidirectional calls are a
separate phase with its own budget. See notes/25-phase5b-design.md.

Original notes follow.

Pipeline:  constraint-state tracking -> FTS5/BM25 retrieval of N candidates

Pipeline:  constraint-state tracking -> FTS5/BM25 retrieval of N candidates
           -> feature-based reranking -> top-10.

Everything runs locally: Python standard library only, no network, no GPU,
no model weights. Every behavioural choice is a config knob so that
lab/sweep.py can run controlled ablations.

Config resolution: explicit arg > TJ_CONFIG env var (JSON) > DEFAULTS.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import array
import random
import sqlite3
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
from starter.dialogue import DialogueMixin  # noqa: E402
from starter.retrieval import RetrievalMixin  # noqa: E402
from starter.catalog import (  # noqa: F401,E402
    _CATALOG_CACHE, _Catalog, _CategoryIndex, _FacetCoverage, _FacetIndex,
    _DenseIndex, _catalog, _card4, _clean, _flatten, _text,
    clear_catalog_cache,
)
# Evidence primitives live in starter/evidence.py. Re-exported here because
# every caller outside this package reaches them as `starter.agent.<name>`,
# and that contract predates the split.
# --------------------------------------------------------------------------
from starter.evidence import (  # noqa: F401,E402
    TOKEN_RE, WS_RE, BASE_STOP, CHROME_STOP, NOISE_REPLIES, OVERRIDE_MARK,
    PROBE_ORDER, LOOKING, EXPLORING, KEY_REQ, MATTERS, MATERIAL_RE, COLOR_RE,
    NEED_IS, CUE_RE, TRAILER_RE, NOISE_RE, Outcome, MORE_RE, UNCERTAIN_RE,
    REFUSAL_RE, CORRECTION_RE, FEATURE_LEX, NEGATION_RE, RESTRICTIVE_RE,
    EXCEPTIVE_RE, HEDGE_RE, NEGATIVE_LEX, HARD_RE, SOFT_RE, SINGLE_VALUED,
    OVERRIDE_RE, ABANDON_RE, REPLACEMENT_RE, NOT_OVERRIDE_RE, SLOT_RES,
    ANSWERABILITY, ATTR_VOCAB, SlotValue, _norm, _distinguishing, slot_of,
    open_world_evidence, hardness_of, relaxation_order, classify_reply,
    is_override, abandoned_span, _fallback_phrases, parse_message, terms_of,
)



DEFAULTS = {
    # --- dialogue policy ---
    # Harvest the simulator's bulk disclosure for two turns, then ask questions
    # chosen by how well they split the live candidate pool. Costs 0.0002
    # against pure "other" and makes 17% of turns a real information-gain
    # question. See notes/08-review-response.md.
    "ask_policy": "other_then_pool",   # other | probe_cycle | other_then_cycle | pool | other_then_pool
    "ask_fallback_after": 2,      # consecutive uninformative replies to "other" before cycling
    "pool_depth": 30,             # candidates inspected by the pool-aware asker
    "pool_give_up_after": 1,      # dry targeted questions before reverting to "other"
    # Over-generality guidance: when the surviving pool spans this many distinct
    # coarse categories it is not a ranking problem, it is an under-specified
    # request. Detecting it forces a pool-derived question and switches the
    # prompt from open-ended to a structured choice. 0 disables.
    "overgeneral_cats": 6,
    # Phase 1C: recovery when the customer stops supplying information.
    # Pool entropy says which question SPLITS the pool; answerability says
    # which question a person can actually answer. After a dry turn, weight by
    # both instead of chasing the most discriminative-but-unanswerable facet.
    "answerability_after": 1,     # dry turns before answerability is weighted in
    # Phase 3B. Raw Shannon entropy counts "this product mentions no colour"
    # as a colour, so an attribute most candidates are SILENT about scores as
    # one they disagree about. Measured on the shipped policy: the attribute
    # actually asked about has a value on only 56% of the window on clean and
    # 26% on uncooperative. Utility scores coverage and answerability
    # explicitly instead, and excludes the missing bucket from the entropy.
    # PHASE 3 DEFAULT. Selected as a near-score-neutral product/robustness
    # trade-off, NOT as the highest public-score configuration: ask_policy=
    # "other" still scores highest at 0.932167 against this at 0.932067. The
    # 0.0001 is spent deliberately, to keep candidate-aware proactive
    # guidance, a lower dry-question rate and coverage-aware behaviour.
    "question_utility": True,
    "question_dry_cost": 0.35,    # penalty for a question likely to go dry
    "rotate_on_request": True,    # "show me more" rotates unseen candidates up
    "rotate_keep_top": 3,         # protect the confident head so MRR survives
    # Starved-evidence broadening. A customer who will not answer leaves a thin
    # query, and a thin query has thin RECALL -- the target stops reaching the
    # candidate pool at all. Deeper pools hurt when evidence is good (MRR falls
    # as popular near-misses crowd in) but recall is the binding constraint when
    # it is not, so widen only while starved. This replaces, deliberately, the
    # query expansion that filler tokens used to provide by accident.
    # "No new information for N turns" is NOT the same as "the query is thin".
    # Measured on the clean set, stalled turns there carry a median of 17 query
    # terms and 7 active constraints -- widening those is exactly the risk of
    # blowing a strong query up to depth 1000. Starvation therefore requires a
    # stall AND genuine thinness (or an explicit request for more options).
    "starved_after": 2,           # consecutive uninformative turns before widening
    "starved_max_terms": 8,       # query is thin at or below this many terms
    "starved_max_slots": 1,       # ...or this many active constraints
    "starved_candidates": 1000,   # candidate depth while starved (0 = never widen)
    "on_override": "keep",        # keep | erase | decay | slot
    "filter_noise": True,
    # Phase 1A: build the retrieval query from accepted evidence only, instead
    # of from every token of every message the noise filter let through.
    "evidence_query": True,
    # Fall back to raw-message slot/feature extraction when no template matches,
    # so natural phrasing ("Leather would be ideal") is not discarded like
    # filler. Extracted evidence is soft and lower-confidence than template
    # evidence, never equal to it.
    "open_world": True,
    "open_world_confidence": 0.6,
    "chrome_stop": True,
    "term_cap": 60,
    # --- retrieval ---
    "bm25": [0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0],
    "candidates": 100,
    # --- reranking (weights from lab/tune.py, 5-fold CV) ---
    "rerank": True,
    "w_bm25": 0.3,
    "w_phrase": 5.0,
    "w_idf": 0.25,
    "w_cat": 1.0,
    "w_pop": 4.0,
    "w_exact": 1.5,
    "w_field": 2.0,
    "phrase_idf": False,
    "w_pos": 0.0,
    # Simulator-inversion feature: measured at only +0.0033 and brittle to
    # paraphrasing. Deliberately left OFF. See notes/04-results.md.
    "w_card": 0.0,
    "w_soft": 0.0,       # static soft-overlap weight (always on if > 0)
    # Pillar III personalization: boost candidates matching the profile's
    # preference_tags. Measured, not assumed -- see notes/08.
    # Penalty for a candidate that matches something the customer rejected
    # ("nothing too formal"). Scored, not filtered: a mis-extracted negative
    # must never be able to empty the candidate pool.
    "w_neg": 2.0,
    "w_profile": 0.0,
    # Adaptive personalization (Pillar III). A profile tag is only worth using
    # if it DISCRIMINATES: "comfort" matches most of the catalog and is pure
    # noise, while a distinctive tag narrows the pool. Measure that against the
    # live candidate pool and weight accordingly, instead of trusting or
    # ignoring the profile wholesale. Costs nothing when the profile is generic.
    # OFF by default: on the public set the tags are generic, and no discriminator
    # tried so far (pool coverage, global IDF) cleanly separates a useful tag
    # from a useless one -- their ranges overlap. Flip this on only with evidence
    # that the profiles carry signal; lab/capability.py measures what it unlocks
    # when they do (profile_informative: 0.9285 -> 0.9703).
    "w_profile_adaptive": 0.0,   # weight applied to discriminative tags only
    "profile_max_coverage": 0.5,  # a tag matching more of the pool than this is noise
    # Adaptive soft matching (Pillar III runtime adaptation): before scoring,
    # probe whether ANY candidate contains ANY disclosed phrase verbatim. If
    # exact matching is alive, use w_soft_lo; if it is dead (constraints were
    # paraphrased), fall back to w_soft_hi.
    # Per-slot graceful degradation: a phrase with at least one verbatim match
    # anywhere in the candidate pool keeps using exact features; a "dead" phrase
    # (zero verbatim matches pool-wide, i.e. it was paraphrased) falls back to
    # IDF-weighted soft token overlap. On a verbatim simulator the dead set is
    # empty, so this feature provably costs nothing.
    "soft_adaptive": False,   # legacy session-level gate (kept for ablation)
    "w_soft_lo": 0.0,
    "w_soft_hi": 2.5,
    "slot_soft": 4.0,         # weight for dead-phrase soft overlap
    "soft_min_idf": 1.5,      # ignore near-ubiquitous tokens in soft overlap
    # Phase 1D: soft matching is EXTRAPOLATION from a constraint, so it is only
    # safe on evidence we still trust. An exact match is the customer's own
    # words and always counts; a fuzzy match invented for a stale or contested
    # constraint is how a superseded category climbs back up the ranking.
    # Measured and left OFF: gating soft rescue on "stated before the pivot, or
    # contested by a newer single-valued answer" costs ~0.0007 everywhere and
    # recovers only 0.0008 of the 0.0100 that slot_soft=0 recovers on
    # override_category -- so that harm has a different cause than this gate
    # models. Kept as an ablation; the mechanism is still open. See notes/08.
    "soft_needs_credible": False,
    # Fine-grained alternative to the blanket gate: block soft-rescue only for
    # the span the customer explicitly abandoned ("forget shoes slippers"),
    # leaving unrelated colour/material evidence soft-matchable.
    "suppress_abandoned": True,
    # ---- Phase 2B: route-specific retrieval data planes -----------------
    # OFF until measured. With this false the retrieval path is exactly the
    # Phase 2A one, which is what keeps the compatibility score bit-exact.
    # dual_plane conflated three independent things, which is how "Phase 2B
    # on" came to mean a package deal that could only be accepted or rejected
    # whole. They are separate switches now. dual_plane is retained ONLY so
    # the R1/R2 rows already in the ledger stay reproducible: it means
    # deep_funnel AND category_plane.
    "dual_plane": False,
    # PHASE 2 CLOSURE, arm C. deep retrieval + funnel is ON; the category
    # plane stays OFF because R2 measured it as a net cost at BOTH retrieval
    # depths. Accepted with one gate failing on human review: non-starved p95
    # rises 33.5 -> 44.3 ms. That is a trade-off, not a pass.
    "deep_funnel": True,       # deep retrieval + deterministic Top-100 preselect
    "category_plane": False,   # category constraint + category candidate source
    # On a starved turn, bypass the fixed funnel and restore the Phase 1
    # widened pool. A constant funnel_top otherwise discards exactly the
    # widening that _starved() just asked for: measured on uncooperative as
    # pool 400.4 without the funnel against 100.0 with it, costing 0.158.
    "starvation_bypass": True,
    # Pins the route regardless of evidence. Used ONLY for the controlled
    # same-query Buying/Browsing/Mixed comparison, where the point is to hold
    # the query fixed and vary nothing but the plane.
    "force_route": "",
    "trace": True,                # telemetry; must never change a ranking
    # Records the candidate list itself. LAB ONLY: the harness uses it to
    # compute Recall@N against ground truth, which the agent must never see.
    "trace_candidates": False,
    # A facet may narrow the pool only if the catalog describes it often
    # enough for silence to be informative. Below this it can still score.
    "facet_min_coverage": 0.30,
    "filter_min_confidence": 0.9,  # only near-certain evidence may filter
    "filter_negative": False,      # hard-negative filter: off until coverage
                                   # in the DEEP pool has been measured
    "buying_depth": 1200,          # BM25 depth before safe filtering
    "buying_min_candidates": 60,   # relax rather than starve below this
    # Unfiltered candidates carried alongside the filtered ones on EVERY turn,
    # not only when the filter starves. Without a standing budget a filter
    # that wrongly excluded the target removed it permanently whenever the
    # surviving pool alone was large enough to fill the page.
    "buying_rescue_budget": 200,
    # ---- the funnel ------------------------------------------------------
    # Deep retrieval feeds a DETERMINISTIC preselection, and only funnel_top
    # candidates reach the reranker. R1 handed the ranker everything it
    # generated -- up to 1274 candidates against a ranker whose operating
    # point is 100 -- and recall rose while ranking fell. Quotas are shares of
    # funnel_top, so widening a source cannot widen the ranker's budget.
    "funnel_top": 100,
    "funnel_quota_primary": 0.70,
    "funnel_quota_expansion": 0.20,
    "funnel_quota_rescue": 0.10,
    # A category match narrows the pool only when it is unambiguous. With
    # several equally good shelves it contributes candidates and ranking
    # weight instead: an ambiguous reading is not grounds for exclusion.
    "category_hard_max_shelves": 1,
    "browsing_depth": 1200,
    "browsing_expand_up": 1,       # one level up reaches sibling shelves
    "browsing_expand_down": 2,
    "browsing_category_cap": 15,   # per-shelf cap, exploration only
    "browsing_neighbour_budget": 120,
    "mixed_depth": 900,
    "mixed_category_budget": 60,
    # ---- Phase 4: dense candidate source (Browsing / Mixed only) --------
    # Buying stays lexical in every arm: a typed constraint is not something a
    # co-occurrence neighbourhood should be allowed to override.
    "dense_browsing": False,      # dense source on the browsing plane
    "dense_mixed": False,         # ...and on the mixed plane
    "dense_fusion": "rrf",        # "rrf" | "dense_only"
    "dense_depth": 300,
    "dense_dim": 32,
    "dense_seed": 20260827,
    "rrf_k": 60,
    "rrf_weight_lexical": 1.0,
    "rrf_weight_dense": 1.0,
    # Off until measured: hardness has never had a live consumer, so switching
    # this on by default would be a claim without an experiment behind it.
    "rescue_relax": False,        # surrender unsatisfiable constraints in order
    "rescue_keep": 1,             # ...and keep rescuing this many at the tail
    # What happens to evidence the customer explicitly abandoned:
    #   "soft_only"  -- keep it scoring, only bar soft-rescue
    #   "deactivate" -- targeted slot erasure: drop it from query and scoring
    # Measured: deactivate wins (override_category 0.928308 vs 0.924458) and is
    # free on clean and on all three payload-rewording styles. Note this is
    # SPAN-TARGETED erasure -- erasing everything on an override
    # (on_override="slot") still loses at 0.9010.
    "abandoned_policy": "deactivate",
    "pop_mode": "log",           # log | pct | pct2 | pct4 | pct_rating
    # Per-route weight overrides, e.g. {"browsing": {"w_pop": 6.0}}.
    "route_overrides": {},
    # Build order/card index structures only when their weights are non-zero.
    # Both default to 0.0, so the submission default skips ~80 MB of dead index.
    "build_extras": None,         # None = infer from w_pos / w_card
}




# Named snapshots of the two supported configurations. These are LABELS over
# DEFAULTS, nothing more: no key here changes DEFAULTS, config resolution, the
# score default, or the fact that the dense route ships off. See
# notes/22-final-configurations.md.
PROFILES: dict[str, dict] = {
    # The submission configuration, and the only one whose score is claimed.
    "score_default": {},
    # Architecture demonstration only -- pre-registered Phase 4 arm B. It
    # improves clean and Browsing MRR but drops Boundary MRR 1.000 -> 0.870
    # and fails the contradiction guard, so it is never the robust default.
    "showcase_dense": {"dense_browsing": True,
                       "dense_mixed": True,
                       "dense_fusion": "dense_only"},
}


def _load_config(config: dict | None) -> dict:
    resolved = dict(DEFAULTS)
    unknown: set[str] = set()
    raw = os.environ.get("TJ_CONFIG")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                unknown |= set(parsed) - set(DEFAULTS)
                resolved.update(parsed)
        except ValueError:
            pass  # a stray env var on the judging host must never kill the run
    if config:
        unknown |= set(config) - set(DEFAULTS)
        resolved.update(config)
    if unknown:
        # Loud in the lab, never fatal at judging time. An ablation that sets a
        # key the agent does not read is a silently void experiment -- which is
        # exactly what `"route": false` was in lab/sweep.py.
        print(f"[agent] warning: ignoring unknown config keys: {sorted(unknown)}",
              file=sys.stderr)
    return resolved


def _resolve_catalog(path: str | Path) -> Path:
    """Make the default catalog path independent of the caller's cwd."""
    candidate = Path(path)
    if candidate.exists():
        return candidate
    fallback = Path(__file__).resolve().parent.parent / candidate
    return fallback if fallback.exists() else candidate



class Agent(RetrievalMixin, DialogueMixin):
    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl",
                 config: dict | None = None) -> None:
        self.cfg = _load_config(config)
        extras = self.cfg.get("build_extras")
        if extras is None:
            extras = bool(self.cfg["w_pos"]) or bool(self.cfg["w_card"])
        self.cat = _catalog(_resolve_catalog(catalog_path), extras=bool(extras))
        self.conn = self.cat.conn
        self.stop = set(BASE_STOP)
        if self.cfg["chrome_stop"]:
            self.stop |= CHROME_STOP
        self._sessions: dict[str, dict] = {}

    # ---- helpers -----------------------------------------------------
    def _terms(self, text: str) -> list[str]:
        """Thin wrapper over evidence.terms_of; the stopword set is per-agent."""
        return terms_of(text, self.stop)



    def close(self) -> None:
        """Drop this agent's session state.

        Deliberately does NOT touch _CATALOG_CACHE: the catalog is shared
        process-wide, so closing one agent must not invalidate the SQLite
        connection another agent is still using. Process teardown calls
        clear_catalog_cache() explicitly.
        """
        self._sessions.clear()

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


    def reset(self, session_id: str, user_profile: dict) -> None:
        self._sessions[session_id] = self._blank_state(user_profile)

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        state = self._sessions.setdefault(session_id, self._blank_state())
        message = user_message if isinstance(user_message, str) else str(user_message or "")
        low = message.lower()

        opening_cat, opening_phrases = (parse_message(message) if turn == 1 else (None, None))
        if turn == 1:
            state["route"] = self._route(message, opening_phrases, opening_cat)
            state["route_history"] = [state["route"]]
        turn_cfg_noise = self._route_cfg(state)

        if is_override(message):
            mode = self._route_cfg(state)["on_override"]
            state["overrides"] += 1
            if mode == "erase":
                state["terms"] = []
                state["phrases"] = []
                state["provenance"] = {}
                state["slots"] = []
            elif mode == "decay":
                # Keep the MOST RECENT evidence: the tail, not the head.
                state["terms"] = state["terms"][-8:]
                state["phrases"] = state["phrases"][-1:]
                state["slots"] = state["slots"][-1:]
            elif mode == "decay_head":   # original (oldest-first) behaviour, kept for ablation
                state["terms"] = state["terms"][:8]
                state["phrases"] = state["phrases"][:1]
                state["slots"] = state["slots"][:1]
            elif mode == "slot":
                self._slot_override(state, message)
            state["last_override_turn"] = turn
            self._suppress_abandoned(state, message, self._route_cfg(state))
            self._release_abandoned_category(state, message, self._route_cfg(state))
            if self._route_cfg(state).get("abandoned_policy") == "deactivate":
                state["phrases"] = [sl.value for sl in state["slots"] if sl.usable]

        category, phrases = parse_message(message)
        outcome = classify_reply(message, phrases, category,
                                 open_world=turn_cfg_noise["open_world"])
        state["outcome"] = outcome
        if outcome == Outcome.REQUEST_MORE:
            state["wants_more"] += 1         # telemetry: how often they asked
            state["rotate_pending"] = True   # consumed by the next _rotate
        informative = outcome in (Outcome.INFORMATIVE, Outcome.OVERRIDE)
        if not turn_cfg_noise["filter_noise"]:
            # Ablation path: treat everything except an explicit no-preference
            # as evidence, which is what the agent did before Phase 1B.
            informative = outcome != Outcome.NO_PREFERENCE
        if state["asked"] and state["asked"][-1] == "other":
            state["dry_others"] = 0 if informative else state.get("dry_others", 0) + 1
        if state["asked"]:
            state["dry_streak"] = 0 if informative else state.get("dry_streak", 0) + 1
            # NO_PREFERENCE means "that facet does not matter to me" -> the facet
            # was wrong, so go open-ended. UNCERTAIN/REFUSAL means "I cannot
            # answer that" -> the facet was too hard, so ask an easier one.
            if outcome in (Outcome.UNCERTAIN, Outcome.REFUSAL):
                state["uncertain_streak"] = state.get("uncertain_streak", 0) + 1
            elif informative:
                state["uncertain_streak"] = 0
        if informative:
            had_category = bool(state["category"])
            if turn == 1 and not category:
                head = re.split(r"[.!?]", message)[0]
                head = re.sub(r"^(hi|hello|hey)\b[,!.\s]*", "", head, flags=re.I)
                head = re.sub(
                    r"\b(i|need|want|show|me|just|browsing|for|searching|everywhere|"
                    r"have|been|am|starting|to|shop|around|a|an|the|right|now|nothing|"
                    r"specific|honestly|today|there)\b", " ", head, flags=re.I)
                head = WS_RE.sub(" ", head).strip(" ,")
                category = head or None
            if category and not state["category"]:
                state["category"] = category
            if turn_cfg_noise["open_world"] and not phrases and not category:
                for attribute, value, polarity in open_world_evidence(message):
                    if any(sl.value == value for sl in state["slots"]):
                        continue
                    terms = tuple(self._terms(value))
                    state["slots"].append(SlotValue(
                        attribute=attribute, value=value, polarity=polarity,
                        hardness=hardness_of(message, "soft"),
                        confidence=turn_cfg_noise["open_world_confidence"],
                        source_turn=turn, provenance=terms))
                    if polarity > 0 and value not in state["phrases"]:
                        state["phrases"].append(value)
                        state["provenance"][value] = list(terms)
            for phrase in phrases:
                if phrase in state["phrases"]:
                    continue
                state["phrases"].append(phrase)
                terms = tuple(self._terms(phrase))
                state["provenance"][phrase] = list(terms)
                state["slots"].append(SlotValue(
                    attribute=slot_of(phrase), value=_norm(phrase),
                    hardness=hardness_of(message, "hard"),
                    source_turn=turn, provenance=terms))
            if phrases or (category and not had_category):
                # The query changed, so the old page is stale: restart paging
                # rather than carry "already shown" across a different result set.
                state["shown"] = []
                state["rotate_pending"] = False
            self._rebuild_terms(state, message, turn_cfg_noise)
            moved = self._route_cfg(state).get("force_route") or self._retarget(state)
            if moved != state["route"]:
                state["route"] = moved
                state.setdefault("route_history", []).append(moved)

        top_k = min(int(top_k), 100)  # contract: recommendations maxItems 100
        turn_cfg = self._route_cfg(state)
        depth = turn_cfg["candidates"]
        starved = self._starved(state, turn_cfg)
        if starved:
            depth = max(depth, int(turn_cfg["starved_candidates"]))
        state["starved"] = bool(starved)
        limit = max(top_k, depth) if turn_cfg["rerank"] else top_k
        cands, trace = self._candidates(state, turn_cfg, limit)
        if turn_cfg["rerank"] and cands:
            ranked = self._rerank(cands, state)
        else:
            ranked = [a for a, _ in cands]
        ranked = self._rotate(ranked, state, turn_cfg)
        ordered = ranked[:top_k]
        if turn_cfg["trace"]:
            # Recorded AFTER ranking and read by nobody upstream of it. The
            # decision trace has to be able to explain a turn without being
            # able to change one.
            trace.update({"turn": turn, "starved": bool(starved),
                          "route_history": list(state.get("route_history") or []),
                          "shown": len(ordered)})
            state.setdefault("trace_log", []).append(trace)
        for asin in ordered:
            if asin not in state["shown"]:
                state["shown"].append(asin)

        # The question is chosen AFTER retrieval so it can be conditioned on the
        # candidates that actually survived.
        attribute = self._pick_attribute(state, ranked)
        state["asked"].append(attribute)
        if turn_cfg["trace"]:
            # Question telemetry. Bounded: scalars for this turn only, appended
            # to the same per-turn trace, never a growing candidate history.
            trace.update({
                "asked": attribute,
                "asked_targeted": attribute != "other",
                "question_bits": round(float(state.get("last_bits") or 0.0), 4),
                "question_coverage": float(state.get("last_coverage") or 0.0),
                "answerability_weighed": bool(state.get("last_weighed")),
                "overgeneral": bool(state.get("broad_options")),
                "structured_options": len(state.get("broad_options") or []),
                "reply_outcome": state.get("outcome") or "",
                "slots_active": sum(1 for sl in state["slots"] if sl.usable),
            })

        return {
            "message": self._compose(attribute, state, ranked, ordered),
            "ask_attribute": attribute,
            "recommendations": [{"parent_asin": a} for a in ordered],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
