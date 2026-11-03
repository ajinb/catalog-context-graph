"""Retrieval-coverage evaluation: can flat top-k retrieval over catalog documents even
*see* the entities a multi-hop operational question needs?

For each question the ground truth is computed by the graph tools from declared relations
(the catalog's own definition of "depends on"). The baseline is BM25 over one document per
entity (its catalog YAML), the usual shape of "RAG over the catalog". The score is recall:
the share of ground-truth entities whose *name appears anywhere* in the top-k retrieved
documents. That is deliberately generous to the baseline: a mention does not mean the
dependency chain that links it to the question is in context. It is an upper bound on
what a model reading those documents could answer, so no LLM is needed and results are
deterministic. The stricter "the entity's own document was retrieved" figure is reported too.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import yaml

from .model import Graph
from .refs import parse_ref, short
from .tools import ContextTools

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75) -> None:
        self.ids = list(docs)
        self.tf = [Counter(_tokens(docs[i])) for i in self.ids]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = mean(self.len) if self.len else 0.0
        df = Counter(term for t in self.tf for term in t)
        n = len(self.ids)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def top(self, query: str, k: int) -> list[str]:
        q = _tokens(query)
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for term in q:
                if term in tf:
                    f = tf[term]
                    s += self.idf[term] * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            scores.append((s, self.ids[i]))
        scores.sort(key=lambda x: (-x[0], x[1]))
        return [i for s, i in scores[:k] if s > 0]


@dataclass
class Result:
    question: str
    tool: str
    hops: int
    truth: set[str]
    retrieved: list[str]
    context: str = ""

    def _name(self, ref: str) -> str:
        return ref.split("/", 1)[1]

    @property
    def covered(self) -> set[str]:
        """Truth entities whose name appears anywhere in the retrieved context (upper bound)."""
        return {r for r in self.truth if re.search(rf"\b{re.escape(self._name(r))}\b", self.context)}

    @property
    def recall(self) -> float:
        return len(self.covered) / len(self.truth) if self.truth else 1.0

    @property
    def strict_recall(self) -> float:
        return len(self.truth & set(self.retrieved)) / len(self.truth) if self.truth else 1.0


def ground_truth(tools: ContextTools, q: dict) -> tuple[set[str], int]:
    if q["tool"] == "blast_radius":
        rows = tools.blast_radius(q["ref"], depth=q.get("depth", 3))["impacted"]
        rows = [r for r in rows if r["kind"] == q.get("answer_kind", r["kind"])]
    elif q["tool"] == "dependencies":
        rows = tools.dependencies(q["ref"], depth=q.get("depth", 3))
        rows = [r for r in rows if r["kind"] == q.get("answer_kind", r["kind"])]
    elif q["tool"] == "teams_to_notify":
        teams = tools.blast_radius(q["ref"], depth=q.get("depth", 3))["teams_to_notify"]
        return {parse_ref(t) for t in teams}, q.get("depth", 3)
    else:
        raise ValueError(f"unknown tool {q['tool']}")
    return {parse_ref(r["ref"]) for r in rows}, max((r["distance"] for r in rows), default=0)


def run(graph: Graph, questions_file: Path, ks: tuple[int, ...] = (3, 5, 10)) -> dict:
    tools = ContextTools(graph)
    docs = {ref: yaml.safe_dump(e.raw, sort_keys=False) for ref, e in graph.entities.items() if e.raw}
    index = BM25(docs)
    questions = yaml.safe_load(questions_file.read_text(encoding="utf-8"))["questions"]
    report: dict = {"documents": len(docs), "questions": len(questions), "by_k": {}}
    for k in ks:
        results = []
        for q in questions:
            truth, hops = ground_truth(tools, q)
            retrieved = index.top(q["question"], k)
            context = "\n".join(docs[r] for r in retrieved).lower()
            results.append(Result(q["question"], q["tool"], hops, truth, retrieved, context))
        by_hops: dict[int, list[float]] = {}
        for r in results:
            by_hops.setdefault(r.hops, []).append(r.recall)
        report["by_k"][k] = {
            "mean_recall": round(mean(r.recall for r in results), 3),
            "mean_strict_recall": round(mean(r.strict_recall for r in results), 3),
            "context_chars": round(mean(len(r.context) for r in results)),
            "complete_answers": sum(r.recall == 1.0 for r in results),
            "recall_by_max_hops": {h: round(mean(v), 3) for h, v in sorted(by_hops.items())},
            "worst": sorted((round(r.recall, 2), r.question,
                              sorted(short(x) for x in r.truth - r.covered))
                             for r in results)[:3],
        }
    return report


def format_report(report: dict) -> str:
    lines = [f"{report['questions']} questions over {report['documents']} catalog documents",
             "", "BM25 top-k  recall (upper bound)  strict  complete  context chars  recall by max hops"]
    for k, r in report["by_k"].items():
        hops = "  ".join(f"{h}-hop {v:.2f}" for h, v in r["recall_by_max_hops"].items())
        lines.append(f"  k={k:<8} {r['mean_recall']:<19} {r['mean_strict_recall']:<7} "
                     f"{r['complete_answers']:>2}/{report['questions']:<6} {r['context_chars']:<14} {hops}")
    lines += ["", "Graph tools: exact by construction (recall 1.00 at every hop count).",
              "", "Hardest questions for retrieval (k = largest):"]
    last = report["by_k"][max(report["by_k"])]
    for recall, question, missing in last["worst"]:
        lines.append(f"  {recall:.2f}  {question}")
        lines.append(f"        missing: {', '.join(missing)}")
    return "\n".join(lines)
