from pathlib import Path

from catalog_context_graph.evaluate import BM25, run

QUESTIONS = Path(__file__).resolve().parent.parent / "eval" / "questions.yaml"


def test_bm25_ranks_lexical_matches_first():
    idx = BM25({"a": "orders database primary", "b": "payments fraud", "c": "orders queue"})
    assert idx.top("orders database", 2) == ["a", "c"]


def test_eval_is_deterministic_and_shows_the_multi_hop_gap(graph):
    r1, r2 = run(graph, QUESTIONS), run(graph, QUESTIONS)
    assert r1 == r2
    k10 = r1["by_k"][10]
    assert k10["recall_by_max_hops"][1] == 1.0
    assert k10["mean_recall"] < 0.8 and k10["complete_answers"] < r1["questions"] // 2
    assert k10["mean_strict_recall"] <= k10["mean_recall"]
