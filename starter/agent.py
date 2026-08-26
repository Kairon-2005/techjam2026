"""Conversational product-search agent.

Pipeline:  constraint-state tracking -> FTS5/BM25 retrieval of N candidates
           -> feature-based reranking -> top-10.

Everything runs locally: Python standard library only, no network, no GPU,
no model weights. Every behavioural choice is a config knob so that
lab/sweep.py can run controlled ablations.

Config resolution: explicit arg > TJ_CONFIG env var (JSON) > DEFAULTS.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
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
    "ask_policy": "other",        # other | probe_cycle | other_then_cycle
    "ask_fallback_after": 2,      # consecutive uninformative replies to "other" before cycling
    "on_override": "keep",        # keep | erase | decay
    "filter_noise": True,
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
    "pop_mode": "log",           # log | pct | pct2 | pct4 | pct_rating
    # Per-route weight overrides, e.g. {"browsing": {"w_pop": 6.0}}.
    "route_overrides": {},
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

    def __init__(self, path: Path) -> None:
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
                self.order[asin] = ordered[:12]
                self.card[asin] = _card4(p, blob)
                self.cats[asin] = _norm(_text(p.get("categories")))
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


def _catalog(path: str | Path) -> _Catalog:
    key = str(Path(path).resolve())
    if key not in _CATALOG_CACHE:
        _CATALOG_CACHE[key] = _Catalog(Path(path))
    return _CATALOG_CACHE[key]


def _load_config(config: dict | None) -> dict:
    resolved = dict(DEFAULTS)
    raw = os.environ.get("TJ_CONFIG")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                resolved.update(parsed)
        except ValueError:
            pass  # a stray env var on the judging host must never kill the run
    if config:
        resolved.update(config)
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
OVERRIDE_RE = re.compile(
    r"ignore my earlier|forget what i said|scratch that|disregard my earlier"
    r"|change of plans|start over", re.I)


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
        self.cat = _catalog(_resolve_catalog(catalog_path))
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
    def _route(first_message: str) -> str:
        low = (first_message or "").lower()
        if "a key requirement is" in low:
            return "buying"
        if "still exploring" in low:
            return "browsing"
        return "override"

    def _pick_attribute(self, state: dict) -> str:
        policy = self.cfg["ask_policy"]
        limit = self.cfg["ask_fallback_after"]
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

    def _retrieve(self, terms: list[str], limit: int) -> list[tuple[str, float]]:
        if not terms or self.cfg["term_cap"] < 1 or limit < 1:
            return []
        expression = " OR ".join(f'"{t}"' for t in terms[: self.cfg["term_cap"]])
        weights = ", ".join(str(w) for w in self.cfg["bm25"])
        rows = self.conn.execute(
            f"SELECT parent_asin, bm25(products, {weights}) AS s FROM products "
            f"WHERE products MATCH ? ORDER BY s LIMIT ?", (expression, limit)).fetchall()
        # FTS5 bm25() is negative and lower-is-better; flip so higher-is-better.
        return [(str(a), -float(s)) for a, s in rows]

    def _rerank(self, cands: list[tuple[str, float]], state: dict) -> list[str]:
        cfg = self.cfg
        overrides = cfg.get("route_overrides") or {}
        if state.get("route") in overrides:
            cfg = {**cfg, **overrides[state["route"]]}
        phrases = [_norm(p) for p in state["phrases"]]
        phrases = [p for p in phrases if len(p) >= 3]
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

        w_soft_eff = cfg["w_soft"]
        if cfg["soft_adaptive"] and phrases:
            alive = any(ph in self.cat.text.get(asin, "")
                        for asin, _ in cands[:50] for ph in phrases)
            w_soft_eff = cfg["w_soft_lo"] if alive else cfg["w_soft_hi"]

        dead: list[int] = []
        if cfg["slot_soft"] and phrases:
            pool = [self.cat.text.get(asin, "") for asin, _ in cands]
            dead = [i for i, ph in enumerate(phrases)
                    if ";" not in ph                       # skip our own concatenations
                    and len(ph) <= 80                      # skip truncated long tails
                    and not any(ph in blob for blob in pool)]

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
                    f_phrase = sum(1 for p in phrases if p in blob) / len(phrases)
                f_exact = sum(1 for p in phrases if p in vals) / len(phrases)
                if (w_soft_eff or cfg["soft_adaptive"]) and ptoks:
                    acc = 0.0
                    for tok_w in ptoks:
                        tot = sum(w for _, w in tok_w) or 1.0
                        acc += sum(w for t, w in tok_w if t in blob) / tot
                    f_soft = acc / len(ptoks)
                f_slot = 0.0
                if dead and ptoks:
                    acc = 0.0
                    for i in dead:
                        tok_w = ptoks[i]
                        tot = sum(w for _, w in tok_w) or 1.0
                        acc += sum(w for t, w in tok_w if t in blob) / tot
                    f_slot = acc / len(dead)
                f_field = sum(1 for p in phrases if p in feat_blob) / len(phrases)
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
            f_pop = self.cat.popularity(asin, cfg["pop_mode"])
            total = (cfg["w_bm25"] * f_bm25 + cfg["w_phrase"] * f_phrase
                     + cfg["w_idf"] * f_idf + cfg["w_cat"] * f_cat
                     + cfg["w_pop"] * f_pop + cfg["w_exact"] * f_exact
                     + cfg["w_field"] * f_field + cfg["w_pos"] * f_pos
                     + cfg["w_card"] * f_card + w_soft_eff * f_soft
                     + cfg["slot_soft"] * f_slot)
            scored.append((-total, order, asin))
        scored.sort()
        return [asin for _, _, asin in scored]

    # ---- protocol ----------------------------------------------------
    def reset(self, session_id: str, user_profile: dict) -> None:
        self._sessions[session_id] = {
            "terms": [], "asked": [], "phrases": [], "category": None,
            "route": None, "profile": user_profile or {}, "dry_others": 0,
        }

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        state = self._sessions.setdefault(session_id, {
            "terms": [], "asked": [], "phrases": [], "category": None,
            "route": None, "profile": {}, "dry_others": 0})
        message = user_message if isinstance(user_message, str) else str(user_message or "")
        low = message.lower()

        if turn == 1:
            state["route"] = self._route(message)

        if OVERRIDE_MARK in low or OVERRIDE_RE.search(low):
            mode = self.cfg["on_override"]
            if mode == "erase":
                state["terms"] = []
                state["phrases"] = []
            elif mode == "decay":
                state["terms"] = state["terms"][:8]
                state["phrases"] = state["phrases"][:1]

        noisy = any(n in low for n in NOISE_REPLIES) or (
            NOISE_RE.search(low) is not None and not CUE_RE.search(low))
        informative = not (self.cfg["filter_noise"] and noisy)
        if state["asked"] and state["asked"][-1] == "other":
            state["dry_others"] = 0 if informative else state.get("dry_others", 0) + 1
        if informative:
            category, phrases = parse_message(message)
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
            for phrase in phrases:
                if phrase not in state["phrases"]:
                    state["phrases"].append(phrase)
            for term in self._terms(message):
                if term not in state["terms"]:
                    state["terms"].append(term)

        attribute = self._pick_attribute(state)
        state["asked"].append(attribute)

        top_k = min(int(top_k), 100)  # contract: recommendations maxItems 100
        limit = max(top_k, self.cfg["candidates"]) if self.cfg["rerank"] else top_k
        cands = self._retrieve(state["terms"], limit)
        if self.cfg["rerank"] and cands:
            ordered = self._rerank(cands, state)[:top_k]
        else:
            ordered = [a for a, _ in cands][:top_k]

        return {
            "message": f"Could you tell me more about your preferred {attribute.replace('_', ' ')}?",
            "ask_attribute": attribute,
            "recommendations": [{"parent_asin": a} for a in ordered],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
