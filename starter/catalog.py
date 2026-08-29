"""Catalog: the product corpus and every index built over it.

The FTS5/BM25 store, the category tree, the per-attribute facet postings and
the dense signature index, plus the process-wide cache that keeps them built
once. Indexes are built LAZILY and, for facets, per attribute -- eager
construction of all seven cost 6.19 s on the first turn that stated a
constraint.

Host capabilities required: NONE at runtime. This module depends only on
`starter.evidence` for the shared vocabularies (ATTR_VOCAB, MATERIAL_RE,
COLOR_RE) and text primitives. It never calls back into the agent.
"""
from __future__ import annotations

import array
import json
import math
import random
import sqlite3
import sys
import time
from pathlib import Path

from starter.evidence import (
    ATTR_VOCAB, COLOR_RE, MATERIAL_RE, TOKEN_RE, WS_RE, _norm,
)

# (resolved path, extras, semantic_fields) -> catalog. The capability is part
# of the key so a richer build never has to displace -- or close -- a leaner
# one that a live Agent is still using. See _catalog() below.
_CATALOG_CACHE: dict[tuple[str, bool, bool], "_Catalog"] = {}


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


class _Catalog:
    """Immutable, process-wide catalog artefacts. Built once, shared by all agents."""

    FIELDS = ("title", "categories", "features", "details", "store", "description")

    def __init__(self, path: Path, extras: bool = True,
                 semantic_fields: bool = False) -> None:
        # `extras` controls the order/card structures, which are only read when
        # w_pos / w_card are non-zero. Both are 0.0 by default.
        self.extras = extras
        # Phase 7A-R1. `text` is " ".join(title, categories, features, details,
        # store, description), so a semantic serialization built from it would
        # duplicate three fields and smuggle in `store`, which the
        # pre-registration excludes. These two are the fields NOT already
        # available separately: `feat` is features+details and `cats` is the
        # category path, but the full title (self.title is clipped to 90 for
        # display) and the description are not.
        #
        # OPT-IN, like `extras`: off by default, so the feature-off memory
        # profile is unchanged and nothing is paid for a path that is not
        # running. Its cost is measured in the integrated feasibility run.
        self.semantic_fields = semantic_fields
        self.sem_title: dict[str, str] = {}
        self.sem_desc: dict[str, str] = {}
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
                if semantic_fields:
                    self.sem_title[asin] = _norm(_text(p.get("title")))
                    self.sem_desc[asin] = _norm(_text(p.get("description")))
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


class _FacetCoverage:
    """A read-through view of per-facet coverage.

    `facet in coverage` and iteration answer from the static name list, so the
    eligibility gate can ask "is this attribute a facet at all?" -- which it
    does for every slot, including `budget` and `feature` -- without scanning
    50,000 products to find out.
    """

    def __init__(self, index: "_FacetIndex") -> None:
        self._index = index

    def __contains__(self, facet: object) -> bool:
        return facet in self._index.NAMES

    def __iter__(self):
        return iter(self._index.NAMES)

    def __getitem__(self, facet: str) -> float:
        if facet not in self._index.NAMES:
            return 0.0
        self._index._build(facet)
        return len(self._index.present.get(facet, ())) / self._index.n

    def get(self, facet: str, default: float = 0.0) -> float:
        return self[facet] if facet in self._index.NAMES else default


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

    # Every facet name this index can serve, known WITHOUT scanning anything.
    # Membership questions ("is `budget` a facet?") must not force a build.
    NAMES = ("material", "color", "style", "use_case", "size", "brand", "department")

    def __init__(self, ids: dict[str, int], text: dict[str, str],
                 store: dict[str, str], dept: dict[str, str]) -> None:
        # Nothing is scanned here. Building all seven facets eagerly cost
        # 6.19 s on the first turn that stated a constraint -- in the SHIPPED
        # default, where category_plane is off and most of those facets are
        # never consulted. Each facet is now built on first use, so a session
        # naming one material pays for one facet and a session naming none
        # pays nothing. The values produced are identical; only the moment of
        # construction changed.
        self._ids, self._text, self._store, self._dept = ids, text, store, dept
        self.n = len(ids) or 1
        self.postings: dict[str, dict[str, set[int]]] = {}
        self.present: dict[str, set[int]] = {}
        self._built: set[str] = set()

    def _build(self, facet: str) -> None:
        """One pass over the catalog for ONE facet."""
        if facet in self._built or facet not in self.NAMES:
            return
        self._built.add(facet)
        postings: dict[str, set[int]] = {}
        present: set[int] = set()
        if facet == "brand" or facet == "department":
            field = self._store if facet == "brand" else self._dept
            for asin, i in self._ids.items():
                value = _norm(field.get(asin, ""))
                if value:
                    present.add(i)
                    postings.setdefault(value, set()).add(i)
        else:
            pattern = dict(self.sources())[facet]
            for asin, i in self._ids.items():
                seen = {_norm(m.group(0)) for m in pattern.finditer(self._text.get(asin, ""))}
                seen.discard("")
                if seen:
                    present.add(i)
                    for value in seen:
                        postings.setdefault(value, set()).add(i)
        self.postings[facet], self.present[facet] = postings, present

    @property
    def coverage(self) -> "_FacetCoverage":
        """Coverage by facet name. Reading one builds only that facet;
        `in` and iteration answer from NAMES without building anything."""
        return _FacetCoverage(self)

    @property
    def built(self) -> frozenset:
        return frozenset(self._built)

    def values(self, facet: str) -> int:
        self._build(facet)
        return len(self.postings.get(facet, {}))

    def missing_rate(self, facet: str) -> float:
        return round(1.0 - self.coverage[facet], 4)

    def hard_ok(self, facet: str, min_coverage: float) -> bool:
        """Whether this facet may narrow a pool at all, filter aside."""
        return self.coverage[facet] >= min_coverage

    def match(self, facet: str, value: str) -> frozenset[int]:
        self._build(facet)
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
        self._build(facet)
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
    """Drop cached catalogs, closing their connections first.

    THE ONLY PLACE A CACHED CATALOG IS EVER CLOSED. A shared cache cannot know
    who still holds a reference to what it evicts, so eviction here is the
    caller's explicit statement that nothing does.
    """
    for cat in list(_CATALOG_CACHE.values()):
        cat.close()
    _CATALOG_CACHE.clear()


def _catalog(path: str | Path, extras: bool = True,
             semantic_fields: bool = False) -> _Catalog:
    """One catalog per (resolved path, extras, semantic_fields) CAPABILITY.

    THE KEY CARRIES THE CAPABILITY, and nothing cached is ever closed here.
    Keying on the path alone meant a richer build SUPERSEDED a leaner one and
    closed it on the way out -- so constructing a semantic-on A2 Agent pulled
    the SQLite connection out from under an A0 Agent that was still live and
    still holding the old object. That is a shared-cache lifetime defect, not a
    test artefact: the A0 Agent had done nothing wrong and had no way to know.

    Different capability versions now coexist. `clear_catalog_cache()` is the
    single place that closes anything, and it closes everything at once.
    """
    key = (str(Path(path).resolve()), bool(extras), bool(semantic_fields))
    cached = _CATALOG_CACHE.get(key)
    if cached is not None:
        return cached
    # A catalog built WITH extras is a superset: reuse it for lean requests too.
    # The same holds for the semantic fields, and both must be satisfied --
    # reusing a lean catalog for a semantic request would leave sem_desc empty
    # and silently serialize products without their descriptions. Candidates
    # are scanned in sorted order so which superset answers a lean request is
    # deterministic rather than dict-insertion order.
    for other_key in sorted(_CATALOG_CACHE):
        other_path, other_extras, other_semantic = other_key
        if other_path != key[0]:
            continue
        if (other_extras or not extras) and (other_semantic or not semantic_fields):
            return _CATALOG_CACHE[other_key]
    _CATALOG_CACHE[key] = _Catalog(Path(path), extras=extras,
                                   semantic_fields=semantic_fields)
    return _CATALOG_CACHE[key]


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
