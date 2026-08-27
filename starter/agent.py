"""Conversational product-search agent.

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

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
WS_RE = re.compile(r"\s+")

BASE_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
}
# Boilerplate emitted by the simulated customer; carries zero information.
CHROME_STOP = {
    "dont", "don", "have", "additional", "preference", "not", "quite", "right",
    "yet", "ask", "about", "one", "specific", "attribute", "those", "options",
    "judgment", "use", "actually", "ignore", "earlier", "what", "need",
    "key", "requirement", "still", "exploring", "matters", "prefer",
}

NOISE_REPLIES = (
    "i don't have an additional preference",
    "i don't have a preference",
    "those options are not quite right",
)
OVERRIDE_MARK = "ignore my earlier preference"

PROBE_ORDER = ["material", "color", "style", "feature", "use_case",
               "category", "brand", "size", "budget", "other"]

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

_CATALOG_CACHE: dict[str, "_Catalog"] = {}


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{k} {v}" for k, v in value.items())
    if isinstance(value, list):
        return " ".join(str(i) for i in value)
    return str(value)


def _flatten(value: object) -> list[str]:
    """Mirror of the evaluator's _flatten_values: how constraint strings are made."""
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items() if v not in (None, "", [])]
    if isinstance(value, list):
        return [str(i) for i in value if i not in (None, "")]
    return [str(value)] if value not in (None, "") else []


def _norm(text: str) -> str:
    return WS_RE.sub(" ", text).strip().lower()


class _Catalog:
    """Immutable, process-wide catalog artefacts. Built once, shared by all agents."""

    FIELDS = ("title", "categories", "features", "details", "store", "description")

    def __init__(self, path: Path, extras: bool = True) -> None:
        # `extras` controls the order/card structures, which are only read when
        # w_pos / w_card are non-zero. Both are 0.0 by default.
        self.extras = extras
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        cur = self.conn.cursor()
        cur.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, store, description, "
            "tokenize='unicode61 remove_diacritics 2')")
        self.text: dict[str, str] = {}
        self.feat: dict[str, str] = {}        # features + details only
        self.vals: dict[str, set[str]] = {}   # exact flattened feature/detail values
        self.order: dict[str, list[str]] = {}  # ordered flattened values (position signal)
        self.card: dict[str, list[str]] = {}   # simulator-replicated 4 constraint strings
        self.cats: dict[str, str] = {}
        # The category LIST, not the flattened string. "women > clothing >
        # dresses" and "girls > clothing > dresses" share every token and are
        # different shelves; a route that wants "near dresses" needs the edges,
        # not a bag of words. Levels are interned: 50k paths over a few hundred
        # distinct level names cost almost nothing when shared.
        self.catpath: dict[str, tuple[str, ...]] = {}
        self.dept: dict[str, str] = {}    # details.Department, 87% covered
        self.store: dict[str, str] = {}   # brand proxy, 99% covered
        # Phase 2B indexes are built on first use, not here: they cost time and
        # memory that the compatibility path (dual_plane off) must not pay.
        self._cat_index: "_CategoryIndex | None" = None
        self._facet_index: "_FacetIndex | None" = None
        self._dense_index: "_DenseIndex | None" = None
        self.title: dict[str, str] = {}   # short display title for explanations
        self.pop: dict[str, float] = {}
        self.pop_pct: dict[str, float] = {}
        self.rating: dict[str, float] = {}
        self.df: dict[str, int] = {}
        batch: list[tuple] = []
        raw_pop: dict[str, float] = {}
        counts: dict[str, float] = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                p = json.loads(line)
                asin = str(p["parent_asin"])
                cols = tuple(_text(p.get(f)) for f in self.FIELDS)
                batch.append((asin, *cols))
                blob = _norm(" ".join(cols))
                self.text[asin] = blob
                fd = _flatten(p.get("features")) + _flatten(p.get("details"))
                self.feat[asin] = _norm(" ".join(fd))
                ordered = [_norm(v) for v in fd if v]
                self.vals[asin] = set(ordered)
                if extras:
                    self.order[asin] = ordered[:12]
                    self.card[asin] = _card4(p, blob)
                self.cats[asin] = _norm(_text(p.get("categories")))
                raw_path = [_norm(str(c)) for c in (p.get("categories") or []) if str(c).strip()]
                self.catpath[asin] = tuple(sys.intern(c) for c in raw_path)
                det = p.get("details")
                self.dept[asin] = _norm(str((det or {}).get("Department", ""))) \
                    if isinstance(det, dict) else ""
                self.title[asin] = _clean(_text(p.get("title")), 90)
                self.store[asin] = _norm(_text(p.get("store")))
                rating = p.get("average_rating") or 0.0
                count = p.get("rating_number") or 0
                raw_pop[asin] = (float(rating) / 5.0) * math.log1p(float(count))
                counts[asin] = float(count)
                self.rating[asin] = float(rating) / 5.0
                seen = set(TOKEN_RE.findall(blob))
                for tok in seen:
                    self.df[tok] = self.df.get(tok, 0) + 1
                if len(batch) >= 1000:
                    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", batch)
                    batch.clear()
        if batch:
            cur.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", batch)
        self.conn.commit()
        self.n_docs = len(self.text)
        top = max(raw_pop.values()) or 1.0
        self.pop = {k: v / top for k, v in raw_pop.items()}
        order = sorted(counts.items(), key=lambda kv: kv[1])
        n = len(order) or 1
        self.pop_pct = {asin: i / n for i, (asin, _) in enumerate(order)}

    @property
    def category_index(self) -> "_CategoryIndex":
        """Built once per catalog, shared by every agent and every turn."""
        if self._cat_index is None:
            self._cat_index = _CategoryIndex(self.catpath)
        return self._cat_index

    @property
    def facet_index(self) -> "_FacetIndex":
        if self._facet_index is None:
            self._facet_index = _FacetIndex(self.category_index.ids, self.text,
                                            self.store, self.dept)
        return self._facet_index

    def dense_index(self, dim: int, seed: int) -> "_DenseIndex":
        """Built once per catalog and shared, like the other indexes. Only
        reached when a route actually asks for a dense candidate source, so
        the lexical default pays nothing for it."""
        if self._dense_index is None or (self._dense_index.dim, self._dense_index.seed) != (dim, seed):
            self._dense_index = _DenseIndex(self, dim=dim, seed=seed)
        return self._dense_index

    def index_stats(self) -> dict:
        """Shape and cost of the Phase 2B indexes, for the telemetry row."""
        ci, fi = self.category_index, self.facet_index
        return {
            "category_nodes": len(ci.node),
            "category_leaves": len({tuple(p) for p in ci.leaf}),
            "facets": {name: {"values": fi.values(name),
                              "coverage": round(fi.coverage.get(name, 0.0), 4),
                              "missing_rate": fi.missing_rate(name)}
                       for name in sorted(fi.coverage)},
        }

    def close(self) -> None:
        """Release the in-memory SQLite handle.

        Without this, every catalog build leaks its connection: the tests clear
        _CATALOG_CACHE between cases and the old connections were only reclaimed
        by the GC, emitting ResourceWarnings and holding memory across long
        multi-config sweeps.
        """
        try:
            self.conn.close()
        except Exception:
            pass

    def popularity(self, asin: str, mode: str) -> float:
        if mode == "pct":
            return self.pop_pct.get(asin, 0.0)
        if mode == "pct2":
            return self.pop_pct.get(asin, 0.0) ** 2
        if mode == "pct4":
            return self.pop_pct.get(asin, 0.0) ** 4
        if mode == "pct_rating":
            return self.pop_pct.get(asin, 0.0) * self.rating.get(asin, 0.0)
        return self.pop.get(asin, 0.0)

    def idf(self, term: str) -> float:
        return math.log((self.n_docs + 1) / (self.df.get(term, 0) + 1))


# ---------------------------------------------------------------------------
# Phase 2B retrieval data planes.
#
# Phase 2A built a routing CONTROL plane: buying/browsing/mixed labels that
# selected different weights over one shared candidate set. That is not what
# Pillar I asks for, and calling it dual-track would have been a claim the
# code did not support. These two indexes exist so the routes can generate
# genuinely different candidates -- different topology, not different scoring.
#
# Ownership, so it is not claimed twice: CategoryIndex, FacetIndex, safe
# filtering and route budgets are PHASE 2B. DenseIndex, route-conditioned
# Weighted RRF and semantic hybrid retrieval are PHASE 4 and are not here.
# ---------------------------------------------------------------------------


class _CategoryIndex:
    """The category tree, with products attached at every level.

    Attaching at every LEVEL rather than only at the leaf is what makes
    expansion cheap: "one step up from women > clothing > dresses" is a
    dictionary lookup on the parent tuple, and it already contains the
    siblings.
    """

    def __init__(self, paths: dict[str, tuple[str, ...]]) -> None:
        self.asins: list[str] = sorted(paths)
        self.ids: dict[str, int] = {a: i for i, a in enumerate(self.asins)}
        self.node: dict[tuple, set[int]] = {}
        self.leaf: list[tuple] = [()] * len(self.asins)
        self.children: dict[tuple, set[tuple]] = {}
        self.by_token: dict[str, set[tuple]] = {}
        for asin, path in paths.items():
            i = self.ids[asin]
            self.leaf[i] = path
            for depth in range(1, len(path) + 1):
                prefix = path[:depth]
                self.node.setdefault(prefix, set()).add(i)
                if depth > 1:
                    self.children.setdefault(prefix[:-1], set()).add(prefix)
        self.tokens: dict[tuple, frozenset] = {}
        for prefix in self.node:
            toks = frozenset(TOKEN_RE.findall(" ".join(prefix)))
            self.tokens[prefix] = toks
            for token in toks:
                self.by_token.setdefault(token, set()).add(prefix)
        self.universe = frozenset(range(len(self.asins)))

    # ---- lookup --------------------------------------------------------
    def lookup(self, text: str, min_overlap: int = 1) -> tuple | None:
        """Best node for free-text like "Accessories Belts", or None.

        None is a real answer and callers must handle it: an unrecognised
        category has to degrade to "no category constraint", never to "no
        products". Returning an empty set here would silently empty the pool.
        """
        tokens = [t for t in TOKEN_RE.findall(_norm(text or "")) if len(t) > 2]
        if not tokens:
            return None
        scores: dict[tuple, int] = {}
        for token in tokens:
            for prefix in self.by_token.get(token, ()):  # noqa: B007
                scores[prefix] = scores.get(prefix, 0) + 1
        if not scores:
            return None
        best = max(scores.values())
        if best < min_overlap:
            return None
        winners = [p for p, v in scores.items() if v == best]
        # Rank by how completely the query accounts for the NODE's own name,
        # then by size. Ranking by DEPTH instead -- the first attempt -- picked
        # "clothing > women > clothing > dresses" over "clothing > women >
        # dresses" for the same query and handed back a five-product corner,
        # which then starved every filter downstream.
        return max(winners, key=lambda p: self._rank(p, tokens))

    def _rank(self, prefix: tuple, tokens: list[str]) -> tuple:
        toks = self.tokens.get(prefix) or frozenset()
        precision = len(toks & set(tokens)) / (len(toks) or 1)
        return (round(precision, 6), len(self.node[prefix]))

    def shelves(self, text: str) -> list[tuple]:
        """Every shelf that fits the phrase equally well, not just one.

        "Accessories Belts" names a shelf under Men AND one under Women, and
        picking whichever happens to rank first would exclude half the belts
        in the catalog before the ranker ever sees them. A category constraint
        is only safe if it keeps every equally good reading of it.
        """
        tokens = [t for t in TOKEN_RE.findall(_norm(text or "")) if len(t) > 2]
        if not tokens:
            return []
        scores: dict[tuple, int] = {}
        for token in tokens:
            for prefix in self.by_token.get(token, ()):  # noqa: B007
                scores[prefix] = scores.get(prefix, 0) + 1
        if not scores:
            return []
        best = max(scores.values())
        winners = [p for p, v in scores.items() if v == best]
        top = max(self._rank(p, tokens)[0] for p in winners)
        return sorted(p for p in winners if self._rank(p, tokens)[0] >= top)

    def matching_shelves(self, text: str) -> list[tuple]:
        """Every shelf whose path accounts for ALL the stated words, wherever
        it sits in the tree.

        The taxonomy files one product type under several top-level branches:
        "men > clothing > pants" and "sport specific clothing > golf > men >
        pants" are both men's pants. Keeping only the best-SCORING reading
        dropped the others, and measured over the public set that put the
        target outside the selected shelves 5.5% of the time. Requiring every
        stated token to appear keeps precision; unioning across branches is
        what stops a correct product being unreachable because of where the
        taxonomy happened to file it.
        """
        tokens = {t for t in TOKEN_RE.findall(_norm(text or "")) if len(t) > 2}
        if not tokens:
            return []
        hits = [prefix for prefix, toks in self.tokens.items() if tokens <= toks]
        return sorted(hits) or self.shelves(text)

    def members_of(self, text: str) -> frozenset[int]:
        """Union of every equally good shelf for `text`. Empty when unknown."""
        out: set[int] = set()
        for prefix in self.matching_shelves(text):
            out |= self.node.get(prefix, set())
        return frozenset(out)

    def members(self, path: tuple | None) -> frozenset[int]:
        return frozenset(self.node.get(path, ())) if path else frozenset()

    def expand(self, path: tuple | None, up: int = 1, down: int = 1) -> frozenset[int]:
        """The node, its ancestors up to `up` levels, and children `down` deep.

        Going up one level is what brings in siblings, which is the whole point
        for browsing: someone looking at dresses should see skirts.
        """
        if not path:
            return frozenset()
        out: set[int] = set(self.node.get(path, ()))
        anchor = path
        for _ in range(max(0, up)):
            if len(anchor) <= 1:
                break
            anchor = anchor[:-1]
            out |= self.node.get(anchor, set())
        frontier = {path}
        for _ in range(max(0, down)):
            nxt: set[tuple] = set()
            for node in frontier:
                for child in self.children.get(node, ()):  # noqa: B007
                    out |= self.node.get(child, set())
                    nxt.add(child)
            frontier = nxt
        return frozenset(out)

    def coverage(self, ids) -> dict:
        """How many distinct shelves a candidate set spans, and how evenly."""
        counts: dict[tuple, int] = {}
        for i in ids:
            counts[self.leaf[i]] = counts.get(self.leaf[i], 0) + 1
        total = sum(counts.values())
        if not total:
            return {"categories": 0, "entropy": 0.0, "top_share": 0.0}
        entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c)
        return {"categories": len(counts), "entropy": round(entropy, 4),
                "top_share": round(max(counts.values()) / total, 4)}


class _FacetIndex:
    """Value postings per attribute, with the coverage needed to trust them.

    The structured fields in this catalog cannot carry a filter: details.Color
    is present on 4.9% of products, Material on 4.1%, Size on 1.9%. Values are
    therefore read out of the product text with the same vocabularies the
    reranker uses, which lifts material to 57% and colour to 39%.

    Even at 57%, `material = leather` cannot mean "drop everything not indexed
    as leather" -- that would drop 43% of the catalog for having said nothing,
    the target included. So every filter here is PRESENCE-AWARE: a product is
    excluded only if it has some value for the facet and none of them match.
    Silence is never treated as refusal.
    """

    @staticmethod
    def sources() -> tuple[tuple[str, "re.Pattern[str]"], ...]:
        """Resolved at call time, not at class-definition time: the index
        classes sit above the vocabularies they read, and binding these at
        import made the module fail to load."""
        return (
            ("material", MATERIAL_RE),
            ("color", COLOR_RE),
            ("style", ATTR_VOCAB["style"]),
            ("use_case", ATTR_VOCAB["use_case"]),
            ("size", ATTR_VOCAB["size"]),
        )

    def __init__(self, ids: dict[str, int], text: dict[str, str],
                 store: dict[str, str], dept: dict[str, str]) -> None:
        self.n = len(ids) or 1
        self.postings: dict[str, dict[str, set[int]]] = {}
        self.present: dict[str, set[int]] = {}
        sources = self.sources()
        for name, _ in sources:
            self.postings[name] = {}
            self.present[name] = set()
        self.postings["brand"], self.present["brand"] = {}, set()
        self.postings["department"], self.present["department"] = {}, set()
        for asin, i in ids.items():
            blob = text.get(asin, "")
            for name, pattern in sources:
                seen = {_norm(m.group(0)) for m in pattern.finditer(blob)}
                seen = {v for v in seen if v}
                if seen:
                    self.present[name].add(i)
                    for value in seen:
                        self.postings[name].setdefault(value, set()).add(i)
            brand = _norm(store.get(asin, ""))
            if brand:
                self.present["brand"].add(i)
                self.postings["brand"].setdefault(brand, set()).add(i)
            department = _norm(dept.get(asin, ""))
            if department:
                self.present["department"].add(i)
                self.postings["department"].setdefault(department, set()).add(i)
        self.coverage = {name: len(present) / self.n
                         for name, present in self.present.items()}

    def values(self, facet: str) -> int:
        return len(self.postings.get(facet, {}))

    def missing_rate(self, facet: str) -> float:
        return round(1.0 - self.coverage.get(facet, 0.0), 4)

    def hard_ok(self, facet: str, min_coverage: float) -> bool:
        """Whether this facet may narrow a pool at all, filter aside."""
        return self.coverage.get(facet, 0.0) >= min_coverage

    def match(self, facet: str, value: str) -> frozenset[int]:
        table = self.postings.get(facet) or {}
        value = _norm(value)
        if value in table:
            return frozenset(table[value])
        # The stated phrase may carry the value plus words the catalog does not
        # use ("genuine leather"). Fall back to any indexed value it contains.
        hits: set[int] = set()
        for known, ids in table.items():
            if known and known in value:
                hits |= ids
        return frozenset(hits)

    def safe_keep(self, facet: str, value: str, universe: frozenset[int]) -> frozenset[int]:
        """Products consistent with `value`: matches, plus every product that
        never said. Presence-aware by construction -- this is the only filter
        primitive the buying plane is allowed to use."""
        unknown = universe - self.present.get(facet, set())
        return frozenset((self.match(facet, value) & universe) | unknown)


class _DenseIndex:
    """Reflective Random Indexing over the catalog, as sign signatures.

    Phase 4's requirement is a candidate source that is genuinely NOT the
    lexical one. A random projection of TF-IDF would not be: it preserves
    lexical inner products, so it returns BM25's neighbours through a
    different arithmetic and would be a lexical hash wearing a vector costume.

    The REFLECTION pass is what makes this co-occurrence-based. Terms are given
    random index vectors; documents are summed from them; then term *context*
    vectors are summed back from the documents each term appears in; then
    documents are rebuilt from those contexts. Two products sharing no terms
    can end up close, because their terms co-occur elsewhere in the catalog.

    Measured on the real catalog: BM25 Top-100 and dense Top-100 overlap by
    mean 0.020. Everything is stdlib, deterministic from a fixed seed, and
    nothing is fetched at any point.
    """

    NONZERO = 6          # non-zeros per term index vector
    TERMS_PER_DOC = 14   # highest-idf terms kept per product

    def __init__(self, cat: "_Catalog", dim: int = 32, seed: int = 20260827) -> None:
        self.dim, self.seed = int(dim), int(seed)
        started = time.perf_counter()
        self.asins: list[str] = sorted(cat.text)
        doc_terms: list[list[str]] = []
        for asin in self.asins:
            blob = cat.feat.get(asin, "") or cat.text.get(asin, "")[:400]
            toks = {t for t in TOKEN_RE.findall(blob) if len(t) > 2}
            doc_terms.append(sorted(toks, key=lambda t: -cat.idf(t))[:self.TERMS_PER_DOC])
        vocab = sorted({t for d in doc_terms for t in d})
        self.tid = {t: i for i, t in enumerate(vocab)}
        dim = self.dim

        # Pass 1: documents from term index vectors. array('f') rather than
        # lists of floats -- the list version peaked at 239 MB, over budget.
        doc_vec = array.array("f", bytes(4 * dim * len(doc_terms)))
        index_vec: dict[str, list[tuple[int, int]]] = {}
        for term in vocab:
            rng = random.Random(f"{seed}:{term}")
            picks = rng.sample(range(dim), min(self.NONZERO, dim))
            index_vec[term] = [(p, 1 if i % 2 == 0 else -1) for i, p in enumerate(picks)]
        for i, terms in enumerate(doc_terms):
            base = i * dim
            for term in terms:
                weight = cat.idf(term)
                for p, sign in index_vec[term]:
                    doc_vec[base + p] += sign * weight

        # Reflection: term contexts from the documents they occur in.
        self.term_ctx = array.array("f", bytes(4 * dim * len(vocab)))
        for i, terms in enumerate(doc_terms):
            base = i * dim
            for term in terms:
                tbase = self.tid[term] * dim
                for p in range(dim):
                    self.term_ctx[tbase + p] += doc_vec[base + p]
        del doc_vec

        # Pass 2: documents rebuilt from term CONTEXTS, then thresholded.
        self.sig: list[int] = []
        for terms in doc_terms:
            acc = [0.0] * dim
            for term in terms:
                tbase = self.tid[term] * dim
                for p in range(dim):
                    acc[p] += self.term_ctx[tbase + p]
            bits = 0
            for p in range(dim):
                if acc[p] > 0.0:
                    bits |= 1 << p
            self.sig.append(bits)
        self.build_seconds = round(time.perf_counter() - started, 2)
        self.vocab_size = len(vocab)

    def encode(self, terms) -> "int | None":
        """Query signature, or None when no term is in the vocabulary."""
        dim = self.dim
        acc = [0.0] * dim
        hit = False
        for term in terms:
            j = self.tid.get(term)
            if j is None:
                continue
            hit = True
            tbase = j * dim
            for p in range(dim):
                acc[p] += self.term_ctx[tbase + p]
        if not hit:
            return None
        bits = 0
        for p in range(dim):
            if acc[p] > 0.0:
                bits |= 1 << p
        return bits

    def search(self, terms, limit: int) -> list[tuple[str, float]]:
        """Nearest signatures by Hamming distance, best first.

        Returns a normalised similarity so the caller never has to know the
        dimension; RRF uses rank anyway, and this keeps the scores readable in
        a trace.
        """
        query = self.encode(terms)
        if query is None or limit < 1:
            return []
        dim = self.dim
        sig = self.sig
        order = sorted(range(len(sig)), key=lambda i: (query ^ sig[i]).bit_count())
        return [(self.asins[i], 1.0 - (query ^ sig[i]).bit_count() / dim)
                for i in order[:limit]]

    def identity(self, catalog_sha: str) -> dict:
        """What this artefact IS. No file, no download, no fetch."""
        return {"builder": "reflective_random_indexing_v1", "dim": self.dim,
                "seed": self.seed, "vocab": self.vocab_size,
                "catalog_sha256": catalog_sha, "offline": True,
                "build_seconds": self.build_seconds}


def clear_catalog_cache() -> None:
    """Drop cached catalogs, closing their connections first."""
    for cat in list(_CATALOG_CACHE.values()):
        cat.close()
    _CATALOG_CACHE.clear()


def _catalog(path: str | Path, extras: bool = True) -> _Catalog:
    key = str(Path(path).resolve())
    cached = _CATALOG_CACHE.get(key)
    # A catalog built WITH extras is a superset: reuse it for lean requests too.
    if cached is not None and (cached.extras or not extras):
        return cached
    if cached is not None:
        cached.close()          # superseded by the richer build; do not leak it
    _CATALOG_CACHE[key] = _Catalog(Path(path), extras=extras)
    return _CATALOG_CACHE[key]


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


# --------------------------------------------------------------------------
# Message parsing: turn simulated-customer prose into a structured state.
# --------------------------------------------------------------------------
LOOKING = "i'm looking for "
EXPLORING = ", but i'm still exploring."
KEY_REQ = ". a key requirement is: "
MATTERS = "for that, what matters is: "
MATERIAL_RE = re.compile(r"\b(cotton|polyester|nylon|leather|wool|spandex|silk|rayon|fabric)\b", re.I)
COLOR_RE = re.compile(r"\b(black|white|blue|red|pink|green|brown|gray|grey|purple|yellow|orange)\b", re.I)


def _clean(value: str, limit: int = 180) -> str:
    return WS_RE.sub(" ", value).strip(" -;,.\t\n")[:limit].rstrip()


def _card4(product: dict, blob: str) -> list[str]:
    """Replicate the strings the customer simulator would disclose for this product."""
    cand = _flatten(product.get("features")) + _flatten(product.get("details"))
    m = MATERIAL_RE.search(blob)
    c = COLOR_RE.search(blob)
    if m:
        cand.insert(0, m.group(1).lower())
    if c:
        cand.insert(1, f"color: {c.group(1).lower()}")
    if product.get("price") not in (None, ""):
        cand.append(f"budget around ${product['price']}")
    cleaned = list(dict.fromkeys(_clean(x) for x in cand if _clean(x)))
    return [_norm(x) for x in cleaned[:4]]
NEED_IS = "what i need is: "

# Fallback extraction for paraphrased customer language (template-independent).
CUE_RE = re.compile(
    r"(?:must have|essential(?: for me)? is|requirements?\s*:|important\s*:"
    r"|what i (?:truly )?need is\s*:?|now i want\s*:?|new requirement\s*:?"
    r"|what i care about is|(?:things? that really )?matters? to me (?:would be|is|are)"
    r")\s*(.+)", re.I)
TRAILER_RE = re.compile(
    r",?\s*(?:hope you can help|if that helps|to be honest|i am easy|you know)\.?\s*$", re.I)
NOISE_RE = re.compile(
    r"(?:\bno\b|\bnot\b|don'?t|couldn'?t|could not)[^.]{0,50}\bpreference\b"
    r"|your judgment|do not mind|don'?t mind|i'?m easy|i am easy|you pick"
    r"|any \w+ is fine|up to you|not quite|wrong direction|not really working", re.I)
# What a customer turn actually did. Distinguishing these is what stops an
# uninformative reply from being treated as product evidence: "Hmm, hard to say"
# used to contribute hmm/hard/say to the BM25 query.
class Outcome:
    INFORMATIVE = "informative"
    OVERRIDE = "override"
    NO_PREFERENCE = "no_preference"
    UNCERTAIN = "uncertain"
    REFUSAL = "refusal"
    REQUEST_MORE = "request_more_options"
    CORRECTION = "correction"


MORE_RE = re.compile(
    r"(?:show|give|see|got)\b[^.?!]{0,20}\bmore\b|more options|other options|anything else"
    r"|something else|different options|next\s+(?:few|batch|set)", re.I)
UNCERTAIN_RE = re.compile(
    r"not sure|hard to say|no idea|don'?t know|do not know|can'?t say|cannot say"
    r"|what do you think|you tell me|hmm+\b|maybe\?*$", re.I)
REFUSAL_RE = re.compile(
    r"rather not say|prefer not to|won'?t say|none of your|skip (?:that|this)", re.I)
CORRECTION_RE = re.compile(r"not quite|wrong direction|not really working|that'?s not", re.I)


# Common product qualities that carry real intent but appear in no template.
FEATURE_LEX = re.compile(
    r"\b(waterproof|water[- ]resistant|breathable|lightweight|light\s?weight|insulated"
    r"|adjustable|machine washable|non[- ]slip|slip[- ]resistant|padded|quick[- ]dry"
    r"|wrinkle[- ]free|stretchy|durable|warm|cushioned|arch support)\b", re.I)
NEGATION_RE = re.compile(
    r"\b(?:not|no|nothing|never|avoid|without|don'?t want|rather not|too"
    r"|steer clear of|stay away from|skip|pass on|rule out|allergic to"
    r"|can'?t stand|dislike|not a fan of)\b", re.I)
# Restrictive positives that LOOK like negations. "Nothing but leather" means
# only leather -- reading it as a rejection inverts the constraint and actively
# demotes the right product.
RESTRICTIVE_RE = re.compile(
    r"\b(?:nothing|none)\s+(?:but|other than|except|besides)\b"
    r"|\band nothing else\b|\bnot only\b|\bonly\b", re.I)
# Exceptives, which share every word after the first with the restrictives:
# "ANYTHING but leather" rejects leather, "NOTHING but leather" requires it.
# One pattern covering both read "anything except polyester" as a positive --
# an inversion, and the worse of the two failures, because it turns a
# rejection into a requirement for the very thing the customer refused.
EXCEPTIVE_RE = re.compile(
    r"\b(?:anything|everything|something|any)\s+(?:but|other than|except|besides)\b"
    r"|\bother than\b|\bexcept\b|\bapart from\b|\baside from\b", re.I)
# Hedges. "I'm not sure whether leather matters" contains a negation but
# states no constraint: reading it as a rejection manufactures evidence out of
# an admission of ignorance, and does so with the polarity inverted.
HEDGE_RE = re.compile(
    r"\b(?:not sure|unsure|don'?t know|do not know|no idea|hard to say"
    r"|can'?t say|cannot say|not certain|either way|no strong feelings"
    r"|no preference|whatever)\b", re.I)
# Materials/qualities worth catching as rejections even though they are not in
# the positive vocabularies.
NEGATIVE_LEX = re.compile(r"\b(synthetic|plastic|itchy|scratchy|bulky|sheer|see[- ]through)\b", re.I)


def open_world_evidence(message: str) -> list[tuple[str, str, int]]:
    """Pull (attribute, value, polarity) out of free-form phrasing.

    The template parser is high precision and low recall: anything it cannot
    parse yields no evidence at all, so "Leather would be ideal" and "Mostly
    for hiking" were discarded exactly like "Hmm, hard to say". That is safe
    against filler but silently drops real constraints, which is the paraphrase
    risk on the private set. This runs only after the templates find nothing.
    """
    raw = (message or "").strip()
    if not raw:
        return []
    found: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    vocab = list(ATTR_VOCAB.items()) + [("feature", FEATURE_LEX), ("material", NEGATIVE_LEX)]
    for attribute, pattern in vocab:
        for match in pattern.finditer(raw):
            value = _norm(match.group(0))
            if (attribute, value) in seen:
                continue
            seen.add((attribute, value))
            # Negation within the ~28 characters before the value flips
            # polarity: "nothing too formal" is a constraint, just an inverted
            # one. Three families have to be told apart, and they overlap:
            #   "nothing BUT leather"   -> restrictive positive (only leather)
            #   "anything BUT leather"  -> exceptive negative   (not leather)
            #   "not sure about leather"-> hedge, no constraint at all
            window = raw[max(0, match.start() - 28):match.start()]
            if HEDGE_RE.search(window):
                continue
            if RESTRICTIVE_RE.search(window):       # checked first: "nothing
                negated = False                     # other than X" is both
            elif EXCEPTIVE_RE.search(window):
                negated = True
            else:
                negated = bool(NEGATION_RE.search(window))
            found.append((attribute, value, -1 if negated else 1))
    return found


# Requirement language versus preference language. hardness was previously
# assigned from the TURN INDEX -- turn 1 hard, everything later soft -- which
# says nothing about what the customer actually asked for, and made the field
# unusable as a filter gate (a requirement stated on turn 3 was "soft", an
# idle turn-1 aside was "hard").
HARD_RE = re.compile(
    r"\b(?:must|needs? to be|has to be|have to be|requirement|required|require"
    r"|essential|non-?negotiable|deal-?breaker|can'?t do without|cannot do without"
    r"|strictly|definitely|absolutely|it has to|i need)\b", re.I)
SOFT_RE = re.compile(
    r"\b(?:prefer|preferably|ideal|ideally|would like|would love|would be nice"
    r"|would help|leaning|lean towards|maybe|perhaps|nice to have|if possible"
    r"|open to|sort of|kind of|rather|might|probably|somewhat|i guess"
    r"|something like|on the fence)\b", re.I)


def hardness_of(message: str, default: str = "soft") -> str:
    """Requirement or preference, read from the wording rather than the turn.

    `default` is the verdict when the message commits to neither: evidence the
    template parser understood is a stated constraint and defaults hard, while
    open-world extraction from free text defaults soft.
    """
    low = message or ""
    if HARD_RE.search(low):
        return "hard"
    if SOFT_RE.search(low):
        return "soft"
    return default


def relaxation_order(slots) -> list:
    """Constraints in give-up-first order, per the Phase 2B rescue contract:

        soft  ->  low-confidence hard  ->  older hard  ->  latest explicit hard

    The last thing surrendered is the most recent thing the customer stated as
    a requirement, which is also the thing they would notice being ignored.
    This is the single ordering both the rescue lane and (in Phase 2B) the
    safe hard-filter relaxation read, so the two cannot disagree.
    """
    return sorted([sl for sl in slots if sl.active],
                  key=lambda sl: (sl.hardness == "hard", sl.confidence, sl.source_turn))


def classify_reply(message: str, parsed_phrases: list[str],
                   parsed_category: str | None = None,
                   open_world: bool = True) -> str:
    """Label one customer turn. Evidence is merged only for INFORMATIVE/OVERRIDE.

    Order matters: an override is an override even though it also carries a new
    constraint, and a request for more options is not a preference statement.
    Anything unrecognised with no parsable constraint falls through to
    UNCERTAIN, so unknown phrasing can never silently become query terms.
    """
    low = (message or "").lower()
    if is_override(message):
        return Outcome.OVERRIDE
    evidence = bool(parsed_phrases) or bool(parsed_category)
    if MORE_RE.search(low) and not evidence:
        return Outcome.REQUEST_MORE
    if any(n in low for n in NOISE_REPLIES):
        return Outcome.NO_PREFERENCE
    if evidence:
        # A stated category is evidence even with no constraint attached:
        # "I'm looking for running shoes, but I'm still exploring."
        return Outcome.INFORMATIVE
    if NOISE_RE.search(low) and not CUE_RE.search(low):
        return Outcome.NO_PREFERENCE
    if CORRECTION_RE.search(low):
        return Outcome.CORRECTION
    if REFUSAL_RE.search(low):
        return Outcome.REFUSAL
    if open_world and open_world_evidence(message):
        return Outcome.INFORMATIVE
    return Outcome.UNCERTAIN


@dataclasses.dataclass
class SlotValue:
    """One piece of stated evidence, with everything needed to judge it later."""
    attribute: str
    value: str
    polarity: int = 1                 # +1 wants it, -1 rejects it
    hardness: str = "hard"            # hard | soft
    confidence: float = 1.0
    source_turn: int = 0
    provenance: tuple[str, ...] = ()  # query terms this slot contributed
    active: bool = True
    soft_ok: bool = True              # may this slot be soft-rescued?
    catalog_support: int = -1         # -1 unknown; else verbatim matches in pool
    contradiction: str = ""           # "" | "contested" | "unsupported"

    @property
    def usable(self) -> bool:
        return self.active and self.polarity > 0


# Slot taxonomy. The names come from the API contract's ALLOWED_ATTRIBUTES, not
# from the simulator, so the same buckets apply to any customer phrasing.
SLOT_RES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("budget", re.compile(r"budget|price|\$\s*\d|\bunder\s+\d|<=\s*\d|\bcheap|\bafford", re.I)),
    ("material", MATERIAL_RE),
    ("color", re.compile(r"\bcolou?r\b|" + COLOR_RE.pattern, re.I)),
    ("size", re.compile(r"\bsize|sizing|width|\bwide\b|narrow|\bfits?\b|\blength\b", re.I)),
    ("style", re.compile(r"department|\bstyle|\bfit\b|sleeve|\bneck|collar|pattern", re.I)),
    ("use_case", re.compile(r"hiking|running|gym|winter|summer|outdoor|\bwork\b|travel|casual", re.I)),
    ("brand", re.compile(r"\bbrand\b|\bmade by\b", re.I)),
)


# Catalog-readable attribute vocabularies. Used to measure how much a given
# question would actually split the current candidate pool.
# How likely a shopper can answer a question about this attribute at all.
# Someone who just said "I don't know" can still say what they'll use it for;
# they probably still cannot tell you a fabric composition.
ANSWERABILITY: dict[str, float] = {
    "use_case": 1.00, "category": 0.90, "style": 0.80, "color": 0.75,
    "budget": 0.55, "brand": 0.50, "feature": 0.45, "material": 0.40, "size": 0.35,
}

ATTR_VOCAB: dict[str, "re.Pattern[str]"] = {
    "material": MATERIAL_RE,
    "color": COLOR_RE,
    "style": re.compile(r"\b(slim|regular|relaxed|loose|oversized|crew neck|v-?neck|"
                        r"long sleeve|short sleeve|sleeveless|hooded|zip|button)\b", re.I),
    "use_case": re.compile(r"\b(hiking|running|gym|workout|yoga|winter|summer|outdoor|"
                           r"work|travel|casual|formal|sleep|swim)\b", re.I),
    "size": re.compile(r"\b(x-?small|small|medium|large|x-?large|xx-?large|petite|plus)\b", re.I),
}


def _distinguishing(labels: list[str]) -> list[str]:
    """Drop the words every option shares -- those carry no choice for the user.

    "women clothing dresses casual" / "women clothing dresses work" becomes
    "casual" / "work".
    """
    split = [l.split() for l in labels]
    if len(split) < 2:
        return labels
    common = set(split[0])
    for words in split[1:]:
        common &= set(words)
    trimmed = [" ".join(w for w in words if w not in common) or " ".join(words[-2:])
               for words in split]
    return trimmed if len(set(trimmed)) == len(trimmed) else labels


# Attributes a shopper can only hold one value of at a time. Two "feature"
# constraints coexist happily ("rubber sole" AND "imported"); two materials
# usually mean the later one replaced the earlier.
SINGLE_VALUED = frozenset({"material", "color", "size", "budget", "brand", "category"})


def slot_of(phrase: str) -> str:
    """Bucket a constraint phrase into one attribute slot ("feature" = default)."""
    for name, pattern in SLOT_RES:
        if pattern.search(phrase):
            return name
    return "feature"


# An override needs a replacement, not just the word "forget". The first fix
# for the missed "forget shoes slippers entirely" used a bare \bforget\b, which
# also fires on "Don't forget it needs pockets" and "I forgot to mention black"
# -- those ADD a requirement rather than replacing one.
OVERRIDE_RE = re.compile(
    r"ignore my earlier|disregard my earlier|forget what i said"
    r"|forget my (?:earlier|previous)|scratch that|change of plans"
    r"|changed my mind|start over|no longer (?:want|need|looking)"
    r"|not looking for\b[^.]{0,40}\banymore", re.I)
# "forget X" / "instead of X": an override only when something replaces X.
ABANDON_RE = re.compile(
    r"\b(?:forget|drop|skip)\s+(?:about\s+)?(?P<span>[^.,;!?]{2,60})"
    r"|\b(?:instead of|rather than)\s+(?P<span2>[^.,;!?]{2,60})", re.I)
# Deliberately excludes a bare "instead": in "made of leather instead of
# synthetic" the same word would satisfy both the abandon cue and the
# replacement cue, turning a single constraint into a false override.
REPLACEMENT_RE = re.compile(
    r"what i (?:really |truly )?need is|i (?:now )?want|i'?d (?:like|prefer)"
    r"|now i'?m|looking for|let'?s (?:go|try)|new requirement", re.I)
# Phrases containing "forget" that are never an intent override.
NOT_OVERRIDE_RE = re.compile(
    r"don'?t forget|do not forget|forgot to mention|forget-?me-?not|always forget"
    r"|never forget|didn'?t forget", re.I)


def is_override(message: str) -> bool:
    """Did this turn REPLACE an earlier intent (vs. add to it)?"""
    low = (message or "").lower()
    if NOT_OVERRIDE_RE.search(low):
        return False
    if OVERRIDE_MARK in low or OVERRIDE_RE.search(low):
        return True
    return bool(ABANDON_RE.search(low) and REPLACEMENT_RE.search(low))


def abandoned_span(message: str) -> str:
    """The thing the customer explicitly told us to drop, if they named one."""
    low = (message or "").lower()
    if NOT_OVERRIDE_RE.search(low):
        return ""
    match = ABANDON_RE.search(low)
    if not match:
        return ""
    span = (match.group("span") or match.group("span2") or "").strip()
    span = re.split(r"\s+[-\u2013\u2014]\s+", span)[0]       # drop a trailing aside
    span = re.sub(r"\b(entirely|completely|altogether|for now|please)\b", " ", span, flags=re.I)
    return WS_RE.sub(" ", span).strip(" -")


def _fallback_phrases(raw: str) -> list[str]:
    """Cue-based constraint extraction when no known template matches."""
    match = CUE_RE.search(raw)
    if not match:
        return []
    body = TRAILER_RE.sub("", match.group(1).strip()).strip().rstrip(".!?")
    parts = [TRAILER_RE.sub("", p).strip().rstrip(".!?") for p in body.split(";")]
    out = [p for p in parts if len(p) >= 3]
    if len(out) > 1 and len(body) >= 3:
        out.append(body)
    return out


def parse_message(message: str) -> tuple[str | None, list[str]]:
    """Return (stated_category, [constraint phrases]) for one customer turn."""
    raw = (message or "").strip()
    low = raw.lower()
    if low.startswith(LOOKING):
        rest = raw[len(LOOKING):]
        rest_low = low[len(LOOKING):]
        if rest_low.endswith(EXPLORING):
            return rest[: -len(EXPLORING)].strip(), []
        idx = rest_low.find(KEY_REQ)
        if idx >= 0:
            category = rest[:idx].strip()
            value = rest[idx + len(KEY_REQ):].strip().rstrip(".")
            return category, [value] if value else []
        head, sep, tail = rest.partition(". ")
        if sep:
            return head.strip(), [tail.strip().rstrip(".")] if tail.strip() else []
        return rest.strip().rstrip("."), []
    if low.startswith(MATTERS):
        body = raw[len(MATTERS):].strip().rstrip(".")
        parts = [p.strip() for p in body.split("; ") if p.strip()]
        # keep the whole body too: a constraint may itself contain "; "
        if len(parts) > 1:
            parts.append(body)
        return None, parts
    idx = low.find(NEED_IS)
    if idx >= 0:
        value = raw[idx + len(NEED_IS):].strip().rstrip(".")
        return None, [value] if value else []
    return None, _fallback_phrases(raw)


class Agent:
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
        return [t.lower() for t in TOKEN_RE.findall(text)
                if len(t) > 1 and t.lower() not in self.stop]

    @staticmethod
    def _route(first_message: str, phrases: list[str] | None = None,
               category: str | None = None) -> str:
        """Classify the opening turn.

        Unknown phrasing used to fall through to "override", which is a claim
        the message never made -- 100% of vague openings ("Show me something
        good") were labelled as intent overrides. Unknown now degrades to
        "mixed", and the label is inferred from what the message actually
        carried rather than from which template it failed to match.
        """
        low = (first_message or "").lower()
        if "a key requirement is" in low:
            return "buying"
        if "still exploring" in low:
            return "browsing"
        if is_override(first_message):
            return "override"
        if phrases:
            return "buying"        # a requirement was stated, whatever the wording
        if category:
            return "browsing"      # a category but no requirement yet
        return "mixed"             # under-specified: neither is known

    @staticmethod
    def _retarget(state: dict) -> str:
        """Routes are not fixed at turn 1: browsing converges into buying.

        Once a shopper has committed to real constraints they are no longer
        browsing, so an exploratory route must be able to firm up.
        """
        route = state.get("route") or "mixed"
        if route in ("mixed", "browsing"):
            # Any constraint the shopper volunteers is a commitment signal.
            # Gating on hardness=="hard" would only ever count turn-1 openings,
            # so a browser who later states requirements could never firm up.
            if any(slot.usable for slot in state["slots"]):
                return "buying"
            if route == "mixed" and state.get("category"):
                return "browsing"
        return route

    def _pool_entropy(self, asins: list[str], pattern: "re.Pattern[str]",
                      skip_missing: bool = False) -> float:
        """Shannon entropy (bits) of one attribute's values across the pool.

        A question is worth asking only if the surviving candidates actually
        disagree on it: if every candidate is black, "what colour?" buys nothing.
        """
        counts: dict[str, int] = {}
        for asin in asins:
            match = pattern.search(self.cat.text.get(asin, ""))
            if match is None and skip_missing:
                # Silence is not an answer the customer can give. Counting it
                # as a value makes a sparsely-described attribute look like the
                # most discriminating question in the pool.
                continue
            value = match.group(1).lower() if match else ""
            counts[value] = counts.get(value, 0) + 1
        total = sum(counts.values())
        if total < 2 or len(counts) < 2:
            return 0.0
        return -sum((c / total) * math.log2(c / total) for c in counts.values() if c)

    def _easiest_unasked(self, state: dict) -> str:
        """The most answerable facet not yet asked, ignoring pool entropy."""
        asked = set(state["asked"])
        options = [(score, name) for name, score in ANSWERABILITY.items()
                   if name not in asked and name in ATTR_VOCAB]
        return max(options)[1] if options else ""

    def _suppress_abandoned(self, state: dict, message: str, cfg: dict) -> None:
        """Bar soft-rescue for exactly what the customer named as abandoned.

        Blanket blocking of every pre-pivot slot fixes the category pivot but
        costs paraphrase robustness, because most old evidence is still valid.
        The customer told us which part is dead -- suppress only that.
        """
        if not cfg.get("suppress_abandoned"):
            return          # the switch must disable the whole mechanism, not
                            # just the rerank blocklist: without this the "off"
                            # arm of the ablation still deactivated slots.
        span = abandoned_span(message)
        if not span:
            return
        span_tokens = set(self._terms(span))
        if not span_tokens:
            return
        for slot in state["slots"]:
            if not slot.usable or not slot.soft_ok:
                continue
            tokens = set(self._terms(slot.value))
            if tokens and len(tokens & span_tokens) / len(tokens) >= 0.5:
                slot.soft_ok = False
                slot.contradiction = "abandoned"
                if cfg.get("abandoned_policy") == "deactivate":
                    slot.active = False

    def _release_abandoned_category(self, state: dict, message: str, cfg: dict) -> None:
        """Stop pinning the shelf the customer just walked away from.

        Before Phase 2B a stale category contributed a handful of weak query
        terms and the ranker could out-vote it. With a category data plane it
        selects the candidate pool, so "forget dresses" while state["category"]
        still says dresses holds the customer on the shelf they just left --
        and the rescue lane would be carrying the entire rest of the catalog.

        Dropping to None is deliberate: no category constraint is a weaker
        claim than the wrong one, and the next turn can re-establish it. Gated
        on dual_plane because on the legacy path the category still feeds
        query TERMS, and removing them there would move the frozen baseline.
        """
        if not self._category_on(cfg) or not state.get("category"):
            return
        span = abandoned_span(message)
        if not span:
            return
        span_tokens = set(self._terms(span))
        current = set(self._terms(state["category"]))
        if not span_tokens or not current:
            return
        # Measured against the SPAN, not the category: "forget dresses" names
        # one word of a three-word category and still abandons it, whereas
        # requiring a third of the category to be named let that through.
        if len(current & span_tokens) / len(span_tokens) < 0.5:
            return                      # they abandoned something else
        _, phrases = parse_message(message)
        for phrase in phrases:          # did they name a shelf to move to?
            if self.cat.category_index.shelves(phrase):
                state["category"] = phrase
                return
        state["category"] = None

    def _uncredible(self, state: dict) -> frozenset:
        """Constraint values that may still match exactly but must not be
        soft-matched: stated before an override, or contested by a newer value
        of a single-valued attribute.
        """
        pivot = state.get("last_override_turn", 0)
        blocked: set[str] = set()
        latest: dict[str, tuple[int, str]] = {}
        for slot in state["slots"]:
            if not slot.usable:
                continue
            if pivot and slot.source_turn <= pivot:
                blocked.add(slot.value)          # predates the customer's pivot
            if slot.attribute in SINGLE_VALUED:
                seen = latest.get(slot.attribute)
                if seen is None or slot.source_turn >= seen[0]:
                    if seen is not None and seen[1] != slot.value:
                        blocked.add(seen[1])     # superseded by a newer value
                    latest[slot.attribute] = (slot.source_turn, slot.value)
                elif seen[1] != slot.value:
                    blocked.add(slot.value)
        return frozenset(blocked)

    def _starved(self, state: dict, cfg: dict) -> bool:
        """Thin evidence, not merely a quiet turn.

        Widening trades MRR for recall, so it must fire only when recall is
        actually the binding constraint: the customer has stalled AND the query
        we would run is thin, or they explicitly asked to see more.
        """
        if not cfg["starved_candidates"]:
            return False
        stalled = state.get("dry_streak", 0) >= int(cfg["starved_after"])
        if not (stalled or state.get("rotate_pending")):
            return False
        active = sum(1 for slot in state["slots"] if slot.usable)
        return (len(state["terms"]) <= int(cfg["starved_max_terms"])
                or active <= int(cfg["starved_max_slots"]))

    def _rotate(self, ranked: list[str], state: dict, cfg: dict) -> list[str]:
        """Honour "show me something else" without throwing away the good head.

        Repeating an identical top-10 after the customer asked for alternatives
        burns a turn. Rotating everything would wreck MRR, so the confident head
        is pinned and only the tail is refreshed with unseen candidates.
        """
        if not (cfg["rotate_on_request"] and state.get("rotate_pending")):
            return ranked
        state["rotate_pending"] = False      # one request, one rotation
        keep = max(0, int(cfg["rotate_keep_top"]))
        head, tail = ranked[:keep], ranked[keep:]
        seen = set(state.get("shown") or ())
        fresh = [a for a in tail if a not in seen]
        stale = [a for a in tail if a in seen]
        return head + fresh + stale

    def _overgeneral(self, pool: list[str], cfg: dict) -> tuple[bool, list[str]]:
        """Is the pool too broad to rank, rather than merely unranked?

        Truncating recommendations would be pure loss under this metric, so the
        cutoff drives the QUESTION, not the result list: we stop trying to rank
        an under-specified request and ask a structured one instead.
        """
        limit = int(cfg["overgeneral_cats"])
        if not limit or not pool:
            return False, []
        head = pool[: max(2, int(cfg["pool_depth"]))]
        counts: dict[str, int] = {}
        for asin in head:
            leaf = (self.cat.cats.get(asin, "").split(",")[-1] or "").strip()
            if leaf:
                counts[leaf] = counts.get(leaf, 0) + 1
        if len(counts) < limit:
            return False, []
        top = [name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
        return True, _distinguishing(top)

    def _pool_attribute(self, state: dict, pool: list[str],
                        cfg: dict | None = None) -> tuple[str, float, int]:
        """Ask about whichever attribute best splits the live candidate pool."""
        cfg = cfg or self.cfg
        depth = max(2, int(cfg["pool_depth"]))
        window = pool[:depth]
        # Once the customer has gone quiet, a question they cannot answer costs
        # a whole turn for nothing, so discount by how answerable it is.
        weigh = state.get("dry_streak", 0) >= int(cfg["answerability_after"])
        utility = bool(cfg["question_utility"])
        best, best_bits, best_util = "other", 0.0, 0.0
        for attribute, pattern in ATTR_VOCAB.items():
            if attribute in state["asked"]:      # not_already_asked
                continue
            if utility:
                # information_gain x catalog_coverage x answerability
                #   - expected_dry_turn_cost
                bits = self._pool_entropy(window, pattern, skip_missing=True)
                coverage = self._facet_coverage(window, attribute)
                answerable = ANSWERABILITY.get(attribute, 0.5)
                util = (bits * coverage * answerable
                        - cfg["question_dry_cost"] * (1.0 - coverage * answerable))
            else:
                bits = self._pool_entropy(window, pattern)
                util = bits * ANSWERABILITY.get(attribute, 0.5) if weigh else bits
            if util > best_util:
                best, best_bits, best_util = attribute, bits, util
        # How much of the window actually STATES a value for the attribute we
        # are about to ask about. _pool_entropy counts the empty string as a
        # value, so a facet that most products are silent about scores as
        # "the candidates disagree" when what they do is say nothing.
        state["last_coverage"] = self._facet_coverage(window, best)
        state["last_weighed"] = bool(weigh)
        return best, best_bits, len(window)

    def _facet_coverage(self, window: list[str], attribute: str) -> float:
        """Share of the window carrying any value for `attribute`."""
        pattern = ATTR_VOCAB.get(attribute)
        if not pattern or not window:
            return 0.0
        seen = sum(1 for asin in window
                   if pattern.search(self.cat.text.get(asin, "")))
        return round(seen / len(window), 4)

    def _pick_attribute(self, state: dict, pool: list[str] | None = None) -> str:
        cfg = self._route_cfg(state)
        policy = cfg["ask_policy"]
        limit = cfg["ask_fallback_after"]
        if policy in ("pool", "other_then_pool"):
            if policy == "other_then_pool" and state["asked"].count("other") < 2:
                return "other"
            # A targeted question that yields nothing means our attribute
            # taxonomy disagrees with the customer's. Stop guessing buckets and
            # go open-ended rather than walking the whole list dry.
            broad, options = self._overgeneral(pool or [], cfg)
            state["broad_options"] = options
            # A customer who cannot answer is not a customer with no preference:
            # keep asking, but ask something easier, instead of falling back to
            # the open-ended question they already failed to answer.
            if state.get("uncertain_streak", 0) >= int(cfg["answerability_after"]):
                easy = self._easiest_unasked(state)
                if easy:
                    state["last_bits"] = 0.0
                    return easy
            # An over-general pool is exactly when a targeted question pays, so
            # the give-up guard is suspended while the request is still vague.
            if not broad and state.get("dry_streak", 0) >= cfg["pool_give_up_after"]:
                return "other"
            attribute, bits, _ = self._pool_attribute(state, pool or [], cfg)
            state["last_bits"] = bits
            # No question discriminates the pool -> fall back to open-ended.
            return attribute if bits >= 0.2 else "other"
        if policy == "other" and limit and state.get("dry_others", 0) >= limit:
            # The simulator is not answering "other": degrade to concrete attributes.
            return next((a for a in PROBE_ORDER[:-1] if a not in state["asked"]), "other")
        if policy == "probe_cycle":
            return next((a for a in PROBE_ORDER if a not in state["asked"]), "other")
        if policy == "other_then_cycle":
            if state["asked"].count("other") < 2:
                return "other"
            return next((a for a in PROBE_ORDER if a not in state["asked"]), "other")
        return "other"

    def _rebuild_terms(self, state: dict, message: str, cfg: dict) -> None:
        """Query terms come from accepted evidence, not from raw message tokens.

        The legacy path appended every non-stopword token of any message the
        noise filter let through, so "Hmm, hard to say really" contributed
        hmm/hard/say/really to the BM25 query. On the clean simulator the two
        paths agree exactly -- every message there is either a parsable template
        or already filtered -- so this is free on the public set and only bites
        when the customer says something the templates do not cover.
        """
        if not cfg["evidence_query"]:
            for term in self._terms(message):
                if term not in state["terms"]:
                    state["terms"].append(term)
            return
        terms: list[str] = []
        for term in self._terms(state["category"] or ""):
            if term not in terms:
                terms.append(term)
        for slot in state["slots"]:
            if not slot.usable:
                continue
            for term in slot.provenance:
                if term not in terms:
                    terms.append(term)
        state["terms"] = terms

    def _slot_override(self, state: dict, message: str) -> None:
        """Selective rewrite: drop only the slots the customer just replaced.

        "forget boots, I want running shoes" supersedes the use_case/category
        slot and leaves an unrelated colour constraint standing. Slots in a
        superseded attribute are deactivated along with the terms they
        contributed; every other slot survives.
        """
        new_category, new_phrases = parse_message(message)
        superseded = {slot_of(p) for p in new_phrases if len(_norm(p)) >= 3}
        if new_category:
            superseded.add("category")
            state["category"] = new_category
        if not superseded:
            return
        incoming = {_norm(p) for p in new_phrases}
        dropped = False
        for slot in state["slots"]:
            if not slot.usable or slot.value in incoming:
                continue                            # re-stated verbatim: not stale
            if slot.attribute in superseded:
                slot.active = False
                slot.contradiction = "superseded"
                dropped = True
        if not dropped:
            return
        # Slots are the source of truth: deactivating one removes the terms it
        # contributed, because the query is rebuilt from active evidence.
        state["phrases"] = [sl.value for sl in state["slots"] if sl.usable]
        survivors: set[str] = set(self._terms(state["category"] or ""))
        for slot in state["slots"]:
            if slot.usable:
                survivors.update(slot.provenance)
        state["terms"] = [t for t in state["terms"] if t in survivors]

    def _retrieve(self, terms: list[str], limit: int,
                  cfg: dict | None = None) -> list[tuple[str, float]]:
        # cfg is the ROUTE's config for this turn: term_cap and the bm25 field
        # weights are per-route retrieval topology, not global constants.
        cfg = cfg or self.cfg
        if not terms or cfg["term_cap"] < 1 or limit < 1:
            return []
        expression = " OR ".join(f'"{t}"' for t in terms[: cfg["term_cap"]])
        weights = ", ".join(str(w) for w in cfg["bm25"])
        rows = self.conn.execute(
            f"SELECT parent_asin, bm25(products, {weights}) AS s FROM products "
            f"WHERE products MATCH ? ORDER BY s LIMIT ?", (expression, limit)).fetchall()
        # FTS5 bm25() is negative and lower-is-better; flip so higher-is-better.
        return [(str(a), -float(s)) for a, s in rows]

    # ---- Phase 2B: route-specific retrieval data planes ----------------

    @staticmethod
    def _category_on(cfg: dict) -> bool:
        """Whether the category plane contributes at all this turn.

        R2 measured it as a net cost at both retrieval depths, so it is a
        separate switch that the candidate default leaves off, rather than
        something bolted to `deep_funnel`.
        """
        return bool(cfg["category_plane"] or cfg["dual_plane"])

    def _eligible_filters(self, state: dict, cfg: dict) -> tuple[list, list]:
        """Which constraints may narrow the pool, and why each other may not.

        Every rejection is recorded rather than merely happening, because the
        interesting failure of a filter is not that it fired but that it fired
        on something it should not have. Six gates, all of which have to pass:
        positive, live, not abandoned, stated as a requirement, near-certain,
        and backed by a facet the catalog describes often enough to be read.
        """
        facets = self.cat.facet_index
        contested = self._uncredible(state)
        eligible, skipped = [], []
        for slot in state["slots"]:
            reason = ""
            if slot.polarity < 0:
                # A rejection is penalised in the ranker, never filtered on,
                # until its coverage in the deep pool has been measured.
                reason = "negative polarity: scored, not filtered"
            elif not slot.active:
                reason = f"inactive ({slot.contradiction or 'superseded'})"
            elif not slot.soft_ok:
                reason = "named as abandoned"
            elif slot.value in contested:
                reason = "contested by newer evidence for the same attribute"
            elif slot.hardness != "hard":
                reason = "soft: a preference, not a requirement"
            elif slot.confidence < cfg["filter_min_confidence"]:
                reason = f"confidence {slot.confidence:.2f} < {cfg['filter_min_confidence']}"
            elif slot.attribute not in facets.coverage:
                reason = f"no facet index for {slot.attribute!r}"
            elif not facets.hard_ok(slot.attribute, cfg["facet_min_coverage"]):
                reason = (f"{slot.attribute} coverage "
                          f"{facets.coverage[slot.attribute]:.2f} < {cfg['facet_min_coverage']}")
            elif not facets.match(slot.attribute, slot.value):
                reason = f"no catalog support for {slot.attribute}={slot.value!r}"
            (skipped if reason else eligible).append(
                (slot.value, reason) if reason else slot)
        return eligible, skipped

    def _safe_pool(self, state: dict, cfg: dict, trace: dict) -> frozenset:
        """The facet-consistent pool, and the shelves the category names.

        R1 intersected with the category shelves unconditionally, which put the
        target outside the pool on 5.5% of public sessions and cost the whole
        regression. The category now narrows only when it resolves to a single
        shelf; otherwise it feeds a candidate source and the ranker's category
        weight, because an ambiguous reading is not grounds for exclusion.
        """
        cats, facets = self.cat.category_index, self.cat.facet_index
        universe = cats.universe
        on = self._category_on(cfg)
        shelves = cats.matching_shelves(state["category"] or "") if on else []
        shelf_ids = cats.members_of(state["category"] or "") if on else frozenset()
        hard_category = 0 < len(shelves) <= int(cfg["category_hard_max_shelves"])
        base = shelf_ids if (hard_category and shelf_ids) else universe
        trace["category_node"] = [" > ".join(p) for p in shelves][:6] or None
        trace["category_shelves"] = len(shelves)
        trace["category_pool"] = len(shelf_ids)
        trace["category_is_hard"] = bool(hard_category and shelf_ids)
        eligible, skipped = self._eligible_filters(state, cfg)
        trace["eligible_filters"] = [sl.value for sl in eligible]
        trace["skipped_filters"] = [{"value": v, "reason": r} for v, r in skipped]

        def narrow(slots) -> frozenset:
            pool = base
            for slot in slots:
                pool = pool & facets.safe_keep(slot.attribute, slot.value, universe)
            return pool

        applied = list(eligible)
        pool = narrow(applied)
        order = relaxation_order(applied)
        surrendered = []
        while len(pool) < cfg["buying_min_candidates"] and order:
            give_up = order.pop(0)
            applied = [sl for sl in applied if sl is not give_up]
            surrendered.append(give_up.value)
            pool = narrow(applied)
        trace["applied_filters"] = [sl.value for sl in applied]
        trace["surrendered_filters"] = surrendered
        trace["pool_before_filter"] = len(base)
        trace["pool_after_filter"] = len(pool)
        return pool if pool else base

    def _shelf_source(self, state: dict, cfg: dict, ids: frozenset,
                      budget: int, floor: float) -> list:
        """Category members BM25 never surfaced, as an `expansion` source.

        Ordered by a BOUNDED popularity prior and scored at the weakest
        observed lexical score rather than 0.0, so injecting them cannot
        stretch the reranker's score normalisation.
        """
        cats = self.cat.category_index
        ranked = sorted(ids, key=lambda i: -self.cat.pop_pct.get(cats.asins[i], 0.0))
        if not self._category_on(cfg):
            return []
        return [(cats.asins[i], floor, "expansion") for i in ranked[:max(0, budget)]]

    def _plane_buying(self, state: dict, cfg: dict, limit: int,
                      trace: dict) -> list:
        """deep BM25 -> presence-aware facet pool -> tagged sources."""
        cats = self.cat.category_index
        pool = self._safe_pool(state, cfg, trace)
        raw = self._retrieve(state["terms"], max(limit, int(cfg["buying_depth"])), cfg)
        floor = min((sc for _, sc in raw), default=0.0)
        inside = [(a, sc, "primary") for a, sc in raw if cats.ids.get(a, -1) in pool]
        # THE RESCUE LANE IS NOT OPTIONAL, but in R1 it was unbounded and
        # simply enlarged the ranker's input. It is now a quota INSIDE the
        # funnel: still reachable, no longer free.
        rescue = [(a, sc, "rescue") for a, sc in raw if cats.ids.get(a, -1) not in pool]
        seen = {a for a, _, _ in inside} | {a for a, _, _ in rescue}
        shelf_only = frozenset(i for i in cats.members_of(state["category"] or "")
                               if cats.asins[i] not in seen) \
            if self._category_on(cfg) else frozenset()
        expansion = self._shelf_source(state, cfg, shelf_only,
                                       int(cfg["funnel_top"]), floor)
        trace["route_candidates"] = {"bm25": len(raw), "in_pool": len(inside),
                                     "rescue": len(rescue), "expansion": len(expansion)}
        trace["filtered_out"] = len(rescue)
        trace["exclusion_rate"] = round(len(rescue) / (len(raw) or 1), 4)
        trace["rescue_candidates"] = len(rescue)
        return inside + expansion + rescue

    def _plane_browsing(self, state: dict, cfg: dict, limit: int,
                        trace: dict) -> list:
        """category expansion + BM25, capped per shelf. No facet filtering:
        browsing is for finding out what exists, and a filter only removes."""
        cats = self.cat.category_index
        shelves = cats.matching_shelves(state["category"] or "") \
            if self._category_on(cfg) else []
        near: set[int] = set()
        for shelf in shelves:
            near |= cats.expand(shelf, int(cfg["browsing_expand_up"]),
                                int(cfg["browsing_expand_down"]))
        trace["category_node"] = [" > ".join(p) for p in shelves][:6] or None
        trace["category_shelves"] = len(shelves)
        trace["category_pool"] = len(near)
        raw = self._retrieve(state["terms"], max(limit, int(cfg["browsing_depth"])), cfg)
        floor = min((sc for _, sc in raw), default=0.0)
        cap, counts = int(cfg["browsing_category_cap"]), {}
        primary, overflow = [], []
        for asin, score in raw:
            leaf = cats.leaf[cats.ids[asin]] if asin in cats.ids else ()
            if counts.get(leaf, 0) < cap:
                counts[leaf] = counts.get(leaf, 0) + 1
                primary.append((asin, score, "primary"))
            else:
                overflow.append((asin, score, "rescue"))
        seen = {a for a, _ in raw}
        expansion = self._shelf_source(
            state, cfg, frozenset(i for i in near if cats.asins[i] not in seen),
            int(cfg["browsing_neighbour_budget"]), floor)
        trace["route_candidates"] = {"bm25": len(raw), "expansion": len(expansion),
                                     "deferred": len(overflow)}
        trace["diversity_deferred"] = len(overflow)
        if not cfg["dense_browsing"]:
            return primary + expansion + overflow
        dense = self._dense_source(state, cfg, trace, {a for a, _ in raw})
        trace["route_candidates"]["dense"] = len(dense)
        if cfg["dense_fusion"] == "dense_only":
            # Arm B: the lexical list is dropped entirely, which is the point
            # of the arm -- it measures what dense alone can carry.
            trace["fusion"] = "dense_only"
            return dense + overflow
        trace["fusion"] = "rrf"
        return self._rrf([primary, dense], cfg, trace) + expansion + overflow

    def _plane_mixed(self, state: dict, cfg: dict, limit: int,
                     trace: dict) -> list:
        """Balanced union, no filtering. Where the route is uncertain the only
        safe move is to keep recall and let the next turn decide."""
        cats = self.cat.category_index
        on = self._category_on(cfg)
        shelves = cats.matching_shelves(state["category"] or "") if on else []
        members = cats.members_of(state["category"] or "") if on else frozenset()
        trace["category_node"] = [" > ".join(p) for p in shelves][:6] or None
        trace["category_shelves"] = len(shelves)
        trace["category_pool"] = len(members)
        raw = self._retrieve(state["terms"], max(limit, int(cfg["mixed_depth"])), cfg)
        floor = min((sc for _, sc in raw), default=0.0)
        seen = {a for a, _ in raw}
        expansion = self._shelf_source(
            state, cfg, frozenset(i for i in members if cats.asins[i] not in seen),
            int(cfg["mixed_category_budget"]), floor)
        lexical = [(a, sc, "primary") for a, sc in raw]
        trace["route_candidates"] = {"bm25": len(raw), "expansion": len(expansion)}
        if not cfg["dense_mixed"]:
            trace["fusion"] = "lexical_only"
            return lexical + expansion
        # Mixed begins browsing-oriented and firms up as evidence arrives:
        # once the customer has stated anything usable, _retarget() moves the
        # session to Buying and this plane stops being consulted at all. Until
        # then it fuses both sources rather than guessing which one is right.
        dense = self._dense_source(state, cfg, trace, seen)
        trace["route_candidates"]["dense"] = len(dense)
        trace["fusion"] = "rrf"
        trace["evidence_slots"] = sum(1 for sl in state["slots"] if sl.usable)
        return self._rrf([lexical, dense], cfg, trace) + expansion

    def _dense_source(self, state: dict, cfg: dict, trace: dict,
                      seen: set) -> list:
        """Dense candidates the lexical route did not already return.

        Tagged `dense` rather than `expansion` so the funnel's quotas and the
        telemetry can both tell where a candidate came from -- which is the
        whole Pillar I claim and cannot rest on a route label.
        """
        index = self.cat.dense_index(int(cfg["dense_dim"]), int(cfg["dense_seed"]))
        hits = index.search(state["terms"][: cfg["term_cap"]], int(cfg["dense_depth"]))
        trace["dense_returned"] = len(hits)
        fresh = [(a, sc, "dense") for a, sc in hits if a not in seen]
        trace["dense_only"] = len(fresh)
        trace["dense_overlap"] = len(hits) - len(fresh)
        return fresh

    @staticmethod
    def _rrf(ranked_lists: list, cfg: dict, trace: dict) -> list:
        """Weighted Reciprocal Rank Fusion over already-ranked sources.

        Rank-based, so the lexical BM25 scale and the dense Hamming similarity
        never have to be made commensurable -- which is the reason to start
        here rather than with a score blend.
        """
        k = float(cfg["rrf_k"])
        weights = {"primary": float(cfg["rrf_weight_lexical"]),
                   "dense": float(cfg["rrf_weight_dense"])}
        fused: dict[str, float] = {}
        origin: dict[str, str] = {}
        for rows in ranked_lists:
            for rank, (asin, _score, source) in enumerate(rows):
                fused[asin] = fused.get(asin, 0.0) + weights.get(source, 1.0) / (k + rank + 1)
                origin.setdefault(asin, source)
        order = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
        trace["rrf_fused"] = len(order)
        return [(asin, score, origin[asin]) for asin, score in order]

    def _funnel(self, tagged: list, cfg: dict, trace: dict) -> list:
        """Deterministic preselection to a fixed reranker budget.

        Deep retrieval is worth having only if the ranker is not asked to
        order all of it: R1 raised Recall@pool to 1.000 and lost 0.045 of
        score doing it, because 1274 candidates went straight into a reranker
        whose operating point is 100. Quotas are shares of funnel_top, so a
        wider source competes for the same budget instead of enlarging it.
        """
        top = int(cfg["funnel_top"])
        quota = {"primary": float(cfg["funnel_quota_primary"]),
                 "expansion": float(cfg["funnel_quota_expansion"]),
                 "rescue": float(cfg["funnel_quota_rescue"])}
        order = ("primary", "expansion", "rescue")
        buckets: dict[str, list] = {k: [] for k in order}
        seen: set[str] = set()
        for asin, score, source in tagged:
            if asin in seen:
                continue                      # first source to claim it wins
            seen.add(asin)
            buckets.setdefault(source, []).append((asin, score))
        for rows in buckets.values():
            rows.sort(key=lambda t: -t[1])
        picked: list[tuple[str, float]] = []
        taken: set[str] = set()
        for source in order:
            cap = int(round(top * quota.get(source, 0.0)))
            for asin, score in buckets.get(source, [])[:cap]:
                picked.append((asin, score))
                taken.add(asin)
        if len(picked) < top:                 # unused quota refills in order
            for source in order:
                for asin, score in buckets.get(source, []):
                    if len(picked) >= top:
                        break
                    if asin not in taken:
                        picked.append((asin, score))
                        taken.add(asin)
        trace["funnel_in"] = len(seen)
        trace["funnel_out"] = len(picked[:top])
        trace["funnel_sources"] = {s: len(buckets.get(s, [])) for s in order}
        return picked[:top]

    def _candidates(self, state: dict, cfg: dict,
                    limit: int) -> tuple[list[tuple[str, float]], dict]:
        """Candidate generation for this turn, and the trace describing it.

        The trace is built here and never read by the ranker: telemetry that
        can change a result is not telemetry.
        """
        route = cfg.get("force_route") or state.get("route") or "mixed"
        deep = bool(cfg["deep_funnel"] or cfg["dual_plane"])
        starved = bool(state.get("starved"))
        # The bypass is decided from this turn's starvation state alone, so it
        # cannot leak into a later turn that has evidence again.
        bypass = bool(deep and starved and cfg["starvation_bypass"])
        trace: dict = {"route": route, "plane": "legacy",
                       "starved": starved, "starvation_bypass": bypass,
                       "hard_slots": sum(1 for sl in state["slots"]
                                         if sl.usable and sl.hardness == "hard"),
                       "soft_slots": sum(1 for sl in state["slots"]
                                         if sl.usable and sl.hardness != "hard"),
                       "negative_slots": sum(1 for sl in state["slots"]
                                             if sl.active and sl.polarity < 0)}
        started = time.perf_counter()
        if not deep or bypass:
            # Phase 1's path verbatim, at whatever limit the caller set --
            # already widened to starved_candidates when starved. Reusing the
            # verified path is the point: nothing new is designed here.
            cands = self._retrieve(state["terms"], limit, cfg)
            trace["plane"] = "starved_legacy" if bypass else "legacy"
            trace["route_candidates"] = {"bm25": len(cands)}
        else:
            plane = {"buying": self._plane_buying,
                     "browsing": self._plane_browsing}.get(route, self._plane_mixed)
            trace["plane"] = route if route in ("buying", "browsing") else "mixed"
            cands = self._funnel(plane(state, cfg, limit, trace), cfg, trace)
        trace["retrieval_depth"] = limit
        seen: set[str] = set()
        deduped = [(a, sc) for a, sc in cands if not (a in seen or seen.add(a))]
        trace["fused_unique"] = len(deduped)
        if cfg["trace_candidates"]:
            trace["candidates"] = [a for a, _ in deduped]
        trace["retrieval_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if deep:
            ids = self.cat.category_index.ids
            trace["category_coverage"] = self.cat.category_index.coverage(
                [ids[a] for a, _ in deduped[:100] if a in ids])
        return deduped, trace

    def _route_cfg(self, state: dict) -> dict:
        """Config for this turn, with the route's overrides folded in.

        Applied turn-wide -- retrieval depth and question policy included, not
        just rerank weights -- so a route can genuinely select a pipeline.
        """
        overrides = self.cfg.get("route_overrides") or {}
        patch = overrides.get(state.get("route"))
        return {**self.cfg, **patch} if patch else self.cfg

    def score_candidates(self, cands: list[tuple[str, float]],
                         state: dict) -> dict[str, float]:
        """Per-candidate final score. Exposed so tests can assert that evidence
        weighting changes the SCORE, not merely the resulting order."""
        return dict(self._rerank(cands, state, want_scores=True))

    def _rerank(self, cands: list[tuple[str, float]], state: dict,
                want_scores: bool = False):
        cfg = self._route_cfg(state)
        phrases = [_norm(p) for p in state["phrases"]]
        phrases = [p for p in phrases if len(p) >= 3]
        # Evidence weight per phrase. Template evidence is 1.0; open-world
        # extraction is lower, so a guess from free text cannot outweigh what
        # the customer stated in a form the parser understood.
        conf = {sl.value: sl.confidence for sl in state["slots"] if sl.usable}
        pconf = [conf.get(p, 1.0) for p in phrases]
        # Denominator is the phrase COUNT, not the confidence sum. Dividing by
        # the sum makes confidence cancel for a lone constraint (0.1/0.1 == 1.0),
        # which only reallocates weight between phrases and leaves a single
        # low-confidence guess scoring like a stated fact. With the count, a
        # 0.1-confidence phrase contributes 0.1 of what a stated one would.
        n_phrases = len(phrases) or 1
        # Constraints the customer REJECTED. They never enter the query (a
        # negative is not a search term) but a candidate that matches one is
        # penalised here, in proportion to how sure we are of the rejection.
        neg = [(sl.value, sl.confidence) for sl in state["slots"]
               if sl.active and sl.polarity < 0 and len(sl.value) >= 3]
        terms = state["terms"][: cfg["term_cap"]]
        idfs = {t: self.cat.idf(t) for t in terms}
        idf_total = sum(idfs.values()) or 1.0
        pw = [sum(self.cat.idf(t) for t in TOKEN_RE.findall(p)) or 1.0 for p in phrases]
        # Pre-tokenise phrases once for the soft-overlap feature.
        ptoks: list[list[tuple[str, float]]] = []
        if (cfg["w_soft"] or cfg["soft_adaptive"] or cfg["slot_soft"]) and phrases:
            floor = cfg["soft_min_idf"]
            for ph in phrases:
                pairs = [(t, self.cat.idf(t)) for t in self._terms(ph)]
                kept = [(t, w) for t, w in pairs if w >= floor]
                ptoks.append(kept or pairs)
        cat_tokens = set(self._terms(state["category"] or ""))
        prof_tags: list[str] = []
        w_prof = cfg["w_profile"]
        raw_tags = [_norm(t) for t in (state.get("profile") or {}).get("preference_tags") or []]
        raw_tags = [t for t in raw_tags if t]
        if cfg["w_profile"]:
            prof_tags = raw_tags
        elif cfg["w_profile_adaptive"] and raw_tags:
            # Keep only tags that actually split the pool.
            pool_blobs = [self.cat.text.get(asin, "") for asin, _ in cands]
            n_pool = len(pool_blobs) or 1
            keep = [t for t in raw_tags
                    if sum(1 for b in pool_blobs if t in b) / n_pool <= cfg["profile_max_coverage"]]
            if keep:
                prof_tags = keep
                w_prof = cfg["w_profile_adaptive"]

        w_soft_eff = cfg["w_soft"]
        if cfg["soft_adaptive"] and phrases:
            alive = any(ph in self.cat.text.get(asin, "")
                        for asin, _ in cands[:50] for ph in phrases)
            w_soft_eff = cfg["w_soft_lo"] if alive else cfg["w_soft_hi"]

        dead: list[int] = []
        if cfg["slot_soft"] and phrases:
            pool = [self.cat.text.get(asin, "") for asin, _ in cands]
            blocked = set(self._uncredible(state)) if cfg["soft_needs_credible"] else set()
            if cfg["suppress_abandoned"]:
                blocked |= {sl.value for sl in state["slots"] if not sl.soft_ok}
            dead = [i for i, ph in enumerate(phrases)
                    if ";" not in ph                       # skip our own concatenations
                    and len(ph) <= 80                      # skip truncated long tails
                    and ph not in blocked
                    and not any(ph in blob for blob in pool)]
            if cfg["rescue_relax"] and len(dead) > 1:
                # Several constraints are unsatisfiable at once. Surrender them
                # in relaxation_order and keep rescuing only the tail -- the
                # requirements, rather than the preferences that happen to be
                # equally unmatched. This is the one live consumer of hardness;
                # the hard-filter relaxation in Phase 2B reads the same order.
                by_value = {sl.value: sl for sl in state["slots"] if sl.usable}
                ordered = relaxation_order([by_value[phrases[i]] for i in dead
                                            if phrases[i] in by_value])
                kept = {sl.value for sl in ordered[-int(cfg["rescue_keep"]):]}
                dead = [i for i in dead if phrases[i] in kept] or dead

        raw_bm25 = [s for _, s in cands]
        lo, hi = (min(raw_bm25), max(raw_bm25)) if raw_bm25 else (0.0, 1.0)
        span = (hi - lo) or 1.0

        scored: list[tuple[float, int, str]] = []
        for order, (asin, bm) in enumerate(cands):
            blob = self.cat.text.get(asin, "")
            f_bm25 = (bm - lo) / span
            f_phrase = f_exact = f_field = f_pos = f_card = f_soft = f_slot = 0.0
            if phrases:
                feat_blob = self.cat.feat.get(asin, "")
                vals = self.cat.vals.get(asin, ())
                if cfg["phrase_idf"]:
                    hit = sum(pw[i] for i, p in enumerate(phrases) if p in blob)
                    f_phrase = hit / (sum(pw) or 1.0)
                else:
                    f_phrase = sum(pconf[i] for i, p in enumerate(phrases)
                                   if p in blob) / n_phrases
                f_exact = sum(pconf[i] for i, p in enumerate(phrases)
                              if p in vals) / n_phrases
                if (w_soft_eff or cfg["soft_adaptive"]) and ptoks:
                    acc = 0.0
                    for i, tok_w in enumerate(ptoks):
                        tot = sum(w for _, w in tok_w) or 1.0
                        acc += (sum(w for t, w in tok_w if t in blob) / tot) * pconf[i]
                    f_soft = acc / len(ptoks)
                f_slot = 0.0
                if dead and ptoks:
                    acc = 0.0
                    for i in dead:
                        tok_w = ptoks[i]
                        tot = sum(w for _, w in tok_w) or 1.0
                        acc += (sum(w for t, w in tok_w if t in blob) / tot) * pconf[i]
                    f_slot = acc / len(dead)
                f_field = sum(pconf[i] for i, p in enumerate(phrases)
                              if p in feat_blob) / n_phrases
                if cfg["w_pos"]:
                    ordered_vals = self.cat.order.get(asin, [])
                    got = [ordered_vals.index(p) for p in phrases if p in ordered_vals]
                    if got:
                        f_pos = sum(1.0 / (1 + i) for i in got) / len(phrases)
                if cfg["w_card"]:
                    card = self.cat.card.get(asin, [])
                    f_card = sum(1 for p in phrases if p in card) / len(phrases)
            f_idf = 0.0
            if terms:
                f_idf = sum(w for t, w in idfs.items() if t in blob) / idf_total
            f_cat = 0.0
            if cat_tokens:
                prod_cats = self.cat.cats.get(asin, "")
                f_cat = sum(1 for t in cat_tokens if t in prod_cats) / len(cat_tokens)
            f_profile = 0.0
            if prof_tags:
                f_profile = sum(1 for t in prof_tags if t in blob) / len(prof_tags)
            f_neg = 0.0
            if neg:
                # A tentative extraction ("nothing too formal") must not veto as
                # hard as an explicit rejection.
                f_neg = sum(c for value, c in neg if value in blob) / len(neg)
            f_pop = self.cat.popularity(asin, cfg["pop_mode"])
            total = (cfg["w_bm25"] * f_bm25 + cfg["w_phrase"] * f_phrase
                     + cfg["w_idf"] * f_idf + cfg["w_cat"] * f_cat
                     + cfg["w_pop"] * f_pop + cfg["w_exact"] * f_exact
                     + cfg["w_field"] * f_field + cfg["w_pos"] * f_pos
                     + cfg["w_card"] * f_card + w_soft_eff * f_soft
                     + cfg["slot_soft"] * f_slot + w_prof * f_profile
                     - cfg["w_neg"] * f_neg)
            scored.append((-total, order, asin))
        scored.sort()
        if want_scores:
            return [(asin, -neg_total) for neg_total, _, asin in scored]
        return [asin for _, _, asin in scored]

    # ---- customer-facing copy ----------------------------------------
    # The evaluator only requires `message` to be a string; it drives the
    # simulator entirely from `ask_attribute`. So the wording is free, and
    # there is no reason for it to read like a form field.
    ASK_COPY = {
        "material": "what it should be made of",
        "color": "which colours work for you",
        "size": "what size you take",
        "style": "the cut or style you prefer",
        "feature": "any feature you can't do without",
        "use_case": "where you'll mostly be using it",
        "brand": "whether you lean towards a particular brand",
        "budget": "roughly what you'd like to spend",
        "category": "what kind of item you have in mind",
        "other": "anything else that matters to you",
    }

    def _compose(self, attribute: str, state: dict, ranked: list[str],
                 shown: list[str]) -> str:
        if not shown:
            return ("I haven't got a good match yet — could you tell me a bit more "
                    "about what you're after?")
        lead = ""
        top = self.cat.title.get(shown[0], "")
        # Prefer short attribute-like constraints; marketing prose lifted from a
        # description reads badly when quoted back at the customer.
        hits = [p for p in state["phrases"] if _norm(p) in self.cat.text.get(shown[0], "")]
        matched = [_clean(p, 44) for p in sorted(hits, key=len) if len(p) <= 50][:2]
        matched = [m for m in matched if m]
        if top:
            if matched:
                lead = (f"Top of the list right now is {top} — it matches "
                        f"{' and '.join(matched)}. ")
            else:
                lead = f"Top of the list right now is {top}. "
        options = state.get("broad_options") or []
        bits = state.get("last_bits", 0.0)
        if options and len(options) >= 2:
            listed = ", ".join(options[:-1]) + f" or {options[-1]}"
            return (lead + f"That still spans quite a range — I'm seeing {listed}. "
                    f"Which of those is closest to what you want?")
        if attribute in ATTR_VOCAB and bits:
            depth = min(len(ranked), max(2, int(self.cfg["pool_depth"])))
            ask = (f"The {depth} closest options still disagree most on "
                   f"{self.ASK_COPY.get(attribute, attribute)}, so that answer would "
                   f"narrow things down fastest — any preference?")
        else:
            ask = f"To sharpen this, could you tell me {self.ASK_COPY.get(attribute, attribute)}?"
        return lead + ask

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

    # ---- protocol ----------------------------------------------------
    @staticmethod
    def _blank_state(profile: dict | None = None) -> dict:
        return {
            "terms": [], "asked": [], "phrases": [], "category": None,
            "route": None, "profile": profile or {}, "dry_others": 0,
            # phrase -> terms it contributed, so slot erasure can drop them too
            "provenance": {}, "overrides": 0, "dry_streak": 0, "broad_options": [],
            "slots": [], "outcome": "", "wants_more": 0, "shown": [],
            "uncertain_streak": 0, "last_override_turn": 0, "rotate_pending": False,
            "route_history": [],
        }

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
