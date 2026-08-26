from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a","an","and","are","as","at","be","but","by","for","from","i","in","is","it",
    "me","my","of","on","or","please","some","that","the","this","to","want","with",
    "would","you","looking","have","dont","don","additional","preference","not","quite",
    "right","yet","ask","about","one","specific","attribute","those","options","judgment",
    "use","actually","ignore","earlier","what","need","around","prefer","really",
}
ASK_ORDER = ["material","color","style","feature","use_case","category","brand","size","budget","other"]
NOISE = ("i don't have an additional preference","i don't have a preference",
         "those options are not quite right","please use your judgment")
OVERRIDE = "ignore my earlier preference"


def _text(value):
    if value is None: return ""
    if isinstance(value, dict): return " ".join(f"{k} {v}" for k, v in value.items())
    if isinstance(value, list): return " ".join(str(i) for i in value)
    return str(value)


def _terms(text):
    return [t.lower() for t in TOKEN_RE.findall(text)
            if len(t) > 1 and t.lower() not in STOPWORDS]


class Agent:
    """Probe: stateful accumulation + always ask an attribute. No LLM."""

    def __init__(self, catalog_path="data/catalog.jsonl"):
        self.catalog_path = Path(catalog_path)
        self.connection = sqlite3.connect(":memory:")
        self._state = {}
        self._build_index()

    def _build_index(self):
        cur = self.connection.cursor()
        cur.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, store, description, "
            "tokenize='unicode61 remove_diacritics 2')")
        batch = []
        with self.catalog_path.open(encoding="utf-8") as fh:
            for line in fh:
                p = json.loads(line)
                batch.append((str(p["parent_asin"]), _text(p.get("title")),
                              _text(p.get("categories")), _text(p.get("features")),
                              _text(p.get("details")), _text(p.get("store")),
                              _text(p.get("description"))))
                if len(batch) >= 1000:
                    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", batch)
                    batch.clear()
        if batch:
            cur.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", batch)
        self.connection.commit()

    def reset(self, session_id, user_profile):
        self._state[session_id] = {"terms": [], "asked": []}

    def respond(self, session_id, user_message, turn, top_k):
        st = self._state[session_id]
        low = (user_message or "").lower()

        if OVERRIDE in low:                 # slot erasure on intent override
            st["terms"] = []
        if not any(n in low for n in NOISE):   # skip zero-information replies
            for t in _terms(user_message):
                if t not in st["terms"]:
                    st["terms"].append(t)

        ask = next((a for a in ASK_ORDER if a not in st["asked"]), "other")
        st["asked"].append(ask)

        recs = []
        if st["terms"]:
            expr = " OR ".join(f'"{t}"' for t in st["terms"][:60])
            rows = self.connection.execute(
                "SELECT parent_asin FROM products WHERE products MATCH ? "
                "ORDER BY bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?",
                (expr, top_k)).fetchall()
            recs = [{"parent_asin": str(r[0])} for r in rows]

        return {"message": f"Could you tell me your preferred {ask.replace('_',' ')}?",
                "ask_attribute": ask,
                "recommendations": recs,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
