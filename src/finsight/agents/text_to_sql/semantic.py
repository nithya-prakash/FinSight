"""Version-controlled semantic layer + lexical retriever (no external embedding service)."""
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DIR = Path(__file__).parent / "semantic"
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    out = []
    for w in _TOKEN.findall(text.lower().replace("_", " ")):
        out.append(w)
        if w.endswith("s") and len(w) > 3:
            out.append(w[:-1])
    return out


@dataclass(frozen=True)
class Catalog:
    schema: str
    tables: dict[str, dict]
    joins: list[str]
    note: str

    def columns(self, table: str) -> set[str]:
        return set(self.tables[table]["columns"])

    def sensitive_columns(self) -> set[str]:
        return {c for t in self.tables.values() for c, m in t["columns"].items() if m.get("sensitivity") == "pseudonymize"}

    def quasi_identifiers(self) -> set[str]:
        return {c for t in self.tables.values() for c, m in t["columns"].items() if m.get("quasi_identifier")}


@lru_cache
def load_catalog() -> Catalog:
    d = yaml.safe_load((DIR / "catalog.yaml").read_text())
    return Catalog(d["schema"], d["tables"], d["joins"], d.get("data_range_note", ""))


@lru_cache
def load_glossary() -> dict[str, dict]:
    return yaml.safe_load((DIR / "glossary.yaml").read_text())["terms"]


@lru_cache
def load_examples() -> list[dict]:
    return yaml.safe_load((DIR / "examples.yaml").read_text())["examples"]


def _tfidf(docs: list[str]):
    toks = [Counter(_tokens(d)) for d in docs]
    df = Counter(w for t in toks for w in t)
    n = len(docs)
    idf = {w: math.log((n + 1) / (c + 0.5)) + 1 for w, c in df.items()}
    vecs = []
    for t in toks:
        v = {w: (1 + math.log(c)) * idf[w] for w, c in t.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs, idf


def _rank(question: str, docs: list[str], k: int, min_score: float = 0.0) -> list[int]:
    vecs, idf = _tfidf(docs)
    q = Counter(_tokens(question))
    qv = {w: (1 + math.log(c)) * idf.get(w, 0) for w, c in q.items() if w in idf}
    scores = [(sum(qv.get(w, 0) * x for w, x in v.items()), i) for i, v in enumerate(vecs)]
    return [i for s, i in sorted(scores, key=lambda s: -s[0])[:k] if s > min_score]


def retrieve(question: str, max_tables: int = 6) -> dict:
    cat, gloss, ex = load_catalog(), load_glossary(), load_examples()
    names = list(cat.tables)
    tdocs = [f"{n} {t['description']} " + " ".join(f"{c} {m['description']}" for c, m in t["columns"].items())
             for n, t in cat.tables.items()]
    tables = [names[i] for i in _rank(question, tdocs, max_tables)]
    terms = list(gloss)
    term_idx = _rank(question, [f"{k} {v['name']} {v['definition']}" for k, v in gloss.items()], 3, 0.05)
    chosen_terms = [terms[i] for i in term_idx]
    for k in chosen_terms:  # tables named in the canonical definition are always supplied
        for n in names:
            if re.search(rf"\b{n}\b", gloss[k]["sql"]) and n not in tables:
                tables.append(n)
    exs = [ex[i] for i in _rank(question, [e["question"] for e in ex], 2, 0.05)]
    for e in exs:
        for n in names:
            if re.search(rf"\b{n}\b", e["sql"]) and n not in tables:
                tables.append(n)
    joins = [j for j in cat.joins if sum(t in j for t in tables) >= 2]
    return {"tables": tables, "terms": chosen_terms, "examples": exs, "joins": joins}


def render_context(r: dict) -> str:
    cat, gloss = load_catalog(), load_glossary()
    lines = [f"Schema: {cat.schema} (always qualify tables as {cat.schema}.<table>)", ""]
    for n in r["tables"]:
        t = cat.tables[n]
        lines.append(f"TABLE {cat.schema}.{n}: {t['description']}")
        for c, m in t["columns"].items():
            lines.append(f"  - {c} ({m['type']}): {m['description']}")
    if r["joins"]:
        lines += ["", "JOINS:"] + [f"  {j}" for j in r["joins"]]
    if r["terms"]:
        lines += ["", "BUSINESS DEFINITIONS (use exactly):"]
        for k in r["terms"]:
            lines.append(f"  - {gloss[k]['name']}: {gloss[k]['definition']}\n    SQL: {gloss[k]['sql']}")
    if r["examples"]:
        lines += ["", "APPROVED EXAMPLES:"]
        for e in r["examples"]:
            lines.append(f"  Q: {e['question']}\n  SQL: {e['sql']}")
    lines += ["", cat.note]
    return "\n".join(lines)
