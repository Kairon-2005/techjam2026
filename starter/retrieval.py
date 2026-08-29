"""Retrieval: which candidates the ranker gets to see, and in what order.

The safe presence-aware pool, the three route planes, the dense source and its
RRF fusion, the deterministic Top-100 funnel, and the reranker. Candidate
GENERATION and candidate ORDERING both live here because the funnel couples
them: retrieval depth is only safe because the funnel bounds what reaches the
ranker.

HOST CAPABILITIES REQUIRED (reached through `self`, never imported):
    self.cfg            resolved configuration
    self.cat            catalog and its indexes
    self.conn           the FTS5 connection
    self._terms()       tokenisation
    self._uncredible()  values that may not be soft-matched   [DialogueMixin]

The last is a call INTO DialogueMixin. It is listed rather than imported: the
two mixins must not import each other, and the domain dependency between them
is real and is documented, not dissolved. See notes/25-phase5b-design.md --
an acyclic IMPORT graph is not an acyclic DOMAIN graph.
"""
from __future__ import annotations

import time

from starter.context import normalize_profile_tags
from starter import semantic as _semantic
from starter.evidence import TOKEN_RE, _norm, relaxation_order


class RetrievalMixin:
    """Mixed into Agent. Not instantiable alone -- see host capabilities above."""



    # ---- Phase 7A-R1: the A2 semantic cascade ---------------------------
    #
    # Candidate ordering is retrieval's domain (Phase 5B), so the cascade lives
    # here and in starter/semantic.py. DialogueMixin owns none of it: an ONNX
    # session, a tokenizer, a product serialization and a rank fusion are not
    # dialogue concerns.

    def _semantic_scorer(self, model_dir: str, max_length: int):
        """One Scorer per model directory, cached on the Agent."""
        cached = getattr(self, "_semantic_cache", None)
        if cached is not None and cached[0] == model_dir:
            return cached[1]
        scorer = _semantic.Scorer.load(model_dir, max_length)
        self._semantic_cache = (model_dir, scorer)
        return scorer

    def _semantic_reorder(self, ordered: list[str], state: dict, cfg: dict,
                          top_k: int) -> tuple[list[str], str, int]:
        """A COPY of `ordered`, prefix-reordered. Returns (result, reason, k).

        `cfg` is the RESOLVED turn config and is the only source: mode, lambda,
        k, the model path and max_length must all come from the same place, or
        a route override could arm the mode while the model path came from the
        base config.
        """
        return _semantic.reorder(
            ordered, cat=self.cat, cfg=cfg, state=state, top_k=top_k,
            uncredible=self._uncredible(state),
            scorer_for=self._semantic_scorer,
            score_order=getattr(self, "_semantic_score_order", None))

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
                want_scores: bool = False, collect: list | None = None):
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
        # NOT NORMALIZED UNCONDITIONALLY. At the shipped weights -- w_profile
        # and w_profile_adaptive both 0.0 -- no branch below reads these tags,
        # so the work is skipped entirely rather than done and discarded. The
        # previous line normalized on EVERY turn before consulting either
        # weight, which is how a non-string in preference_tags crashed the
        # agent through `_norm` while profile weighting was switched off: the
        # cost and the failure were both being paid for a feature that was not
        # running.
        #
        # When a weight IS set, tags go through normalize_profile_tags -- the
        # SAME normalizer the Phase 6C1 profile classifier uses. One definition
        # of what a profile tag is: a second one here would let the reranker
        # and the profile decision disagree about the same tag, and 6C1's
        # shared-kernel rule exists precisely because that divergence is
        # invisible until it changes a score.
        raw_tags: list[str] = []
        if cfg["w_profile"] or cfg["w_profile_adaptive"]:
            raw_tags = list(normalize_profile_tags(
                (state.get("profile") or {}).get("preference_tags")))
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

        # ONE feature kernel. `_rerank` scores from it and the Phase 7A-R1
        # cache builder records it, so there is no second copy of these nine
        # formulas to drift out of step with this one. The replay gate
        # (lab/a1cache.py) re-ranks every cached turn with the default weights
        # and requires the full A0 order back, which is what proves it.
        def _features(asin: str, bm: float) -> dict:
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
            return {"f_bm25": f_bm25, "f_phrase": f_phrase, "f_idf": f_idf,
                    "f_cat": f_cat, "f_pop": f_pop, "f_exact": f_exact,
                    "f_field": f_field, "f_pos": f_pos, "f_card": f_card,
                    "f_soft": f_soft, "f_slot": f_slot,
                    "f_profile": f_profile, "f_neg": f_neg}

        def _score(f: dict) -> float:
            return (cfg["w_bm25"] * f["f_bm25"] + cfg["w_phrase"] * f["f_phrase"]
                    + cfg["w_idf"] * f["f_idf"] + cfg["w_cat"] * f["f_cat"]
                    + cfg["w_pop"] * f["f_pop"] + cfg["w_exact"] * f["f_exact"]
                    + cfg["w_field"] * f["f_field"] + cfg["w_pos"] * f["f_pos"]
                    + cfg["w_card"] * f["f_card"] + w_soft_eff * f["f_soft"]
                    + cfg["slot_soft"] * f["f_slot"] + w_prof * f["f_profile"]
                    - cfg["w_neg"] * f["f_neg"])

        if collect is not None:
            # The cache builder's hook. f_slot is computed here, INSIDE the
            # kernel, from `dead` -- so a later trial setting slot_soft = 0
            # cannot make it vanish from the cache: the trial reweights a
            # recorded feature, it does not re-derive it.
            for order, (asin, bm) in enumerate(cands):
                collect.append((asin, _features(asin, bm)))

        scored: list[tuple[float, int, str]] = []
        for order, (asin, bm) in enumerate(cands):
            total = _score(_features(asin, bm))
            scored.append((-total, order, asin))
        scored.sort()
        if want_scores:
            return [(asin, -neg_total) for neg_total, _, asin in scored]
        return [asin for _, _, asin in scored]
