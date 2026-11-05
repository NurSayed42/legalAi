"""Scorer এর নিয়মগুলো, জানা উত্তরের ছোট run দিয়ে। আসল সংখ্যা দেখার আগেই ভুল ধরার জন্য।"""

import pytest

from evals.retrieval_schema import BenchmarkQuery, RetrievalEvalConfig
from evals.retrieval_score import (
    aggregate,
    case_list,
    declared_tests,
    domination_table,
    false_similarity,
    query_metrics,
    score_run,
)

SYSTEMS = ["dense", "bm25", "hybrid", "hybrid_rerank"]


@pytest.fixture
def cfg() -> RetrievalEvalConfig:
    return RetrievalEvalConfig(
        embed_model="m", fetch_depth=200, fusion_depth=100, rrf_k=60, rerank_model="r", rerank_max_length=256, rerank_text_chars=2000, rerank_candidates=50,
        api_retrieve_n=12, k=10, relevant_threshold=1, strict_threshold=2, primary_metric="ndcg", bootstrap_samples=2000, permutation_samples=2000, seed=1, alpha=0.05,
        large_case_top_share=0.5, similarity_thresholds=[0.5], rerank_api_threshold=0.05,
    )


def query(qid: str, need: str, language: str = "en", group: str = "topic") -> BenchmarkQuery:
    return BenchmarkQuery(id=qid, need_id=need, query=f"question {qid}", language=language, group=group, category="other", type="single-hop")


def row(cases: list[str]) -> dict:
    return {"chunks": [[f"{c}#{i}", c, 1.0 - i / 100] for i, c in enumerate(cases)], "seconds": 0.1, "term_hit_top_chunks": [True] * min(10, len(cases)), "term_hit_top_cases": [True]}


def make_run(answers: dict[str, dict[str, list[str]]]) -> dict:
    return {"results": {q: {s: row(c) for s, c in by.items()} for q, by in answers.items()}, "case_chunks": {"big": 90, "good": 2, "bad": 2, "other": 2}, "snippets": {}}


def test_collapse_keeps_first_chunk_of_each_case():
    assert [c[1] for c in case_list([["a#1", "a", 3], ["b#1", "b", 2], ["a#2", "a", 1]])] == ["a", "b"]


def test_one_case_filling_slots_counts_once_for_relevance_but_shows_as_domination(cfg):
    run = make_run({"q1": {"dense": ["big"] * 9 + ["good"]}})
    got = score_run(run, [query("q1", "t1")], {"t1": {"big": 2, "good": 2}}, cfg)["q1"]["dense"]
    assert got["precision"] == pytest.approx(0.2)  # দুটো মামলা, ১০টা chunk নয়
    assert got["max_slots_one_case"] == 9 and got["distinct_cases"] == 2
    assert got["returned_cases"] == 2


def test_no_answer_queries_stay_out_of_the_means(cfg):
    queries = [query("q1", "t1"), query("q2", "n1", group="no_answer")]
    run = make_run({"q1": {"dense": ["good"]}, "q2": {"dense": ["bad"]}})
    per = score_run(run, queries, {"t1": {"good": 2}}, cfg)
    assert per["q2"]["dense"]["ndcg"] is None and per["q2"]["dense"]["recall"] is None
    assert aggregate(per, queries, ["dense"], lambda q: True)["dense"]["ndcg"] == 1.0


def test_unjudged_results_count_against_the_system_and_are_reported(cfg):
    got = query_metrics(["x", "y", "good"], {"good": 2}, cfg)
    assert got["mrr"] == pytest.approx(1 / 3)
    assert got["judged"] == pytest.approx(1 / 3)


def _paired(n: int, a_cases: list[str], b_cases: list[str]) -> dict:
    answers = {}
    for i in range(n):
        for language in ("en", "bn"):
            answers[f"{language}{i}"] = {"dense": list(b_cases), "bm25": list(a_cases), "hybrid": list(a_cases), "hybrid_rerank": list(a_cases)}
    return make_run(answers)


def test_identical_systems_are_never_called_better(cfg):
    n = 20
    queries = [query(f"{la}{i}", f"t{i}", la) for i in range(n) for la in ("en", "bn")]
    per = score_run(_paired(n, ["good"], ["good"]), queries, {f"t{i}": {"good": 2} for i in range(n)}, cfg)
    assert {r["verdict"] for r in declared_tests(per, queries, cfg)} == {"no measured difference"}


def test_clear_gain_on_many_needs_is_confirmed_with_direction(cfg):
    n = 20
    queries = [query(f"{la}{i}", f"t{i}", la) for i in range(n) for la in ("en", "bn")]
    per = score_run(_paired(n, ["good"], ["bad"]), queries, {f"t{i}": {"good": 2} for i in range(n)}, cfg)
    rows = {r["name"]: r for r in declared_tests(per, queries, cfg)}
    assert rows["bm25 vs dense [bn]"]["verdict"] == "bm25 better"
    assert rows["hybrid vs dense [en]"]["diff"] == pytest.approx(1.0)
    assert rows["hybrid vs bm25 [en]"]["verdict"] == "no measured difference"
    assert len(rows) == 8


def test_gain_on_three_needs_cannot_be_confirmed(cfg):
    """নিখুঁত জয়ও ৩ টা need এ প্রমাণ নয়: সব চিহ্ন একদিকে হওয়া কাকতালীয় হতে পারে।"""
    n = 3
    queries = [query(f"{la}{i}", f"t{i}", la) for i in range(n) for la in ("en", "bn")]
    per = score_run(_paired(n, ["good"], ["bad"]), queries, {f"t{i}": {"good": 2} for i in range(n)}, cfg)
    assert {r["verdict"] for r in declared_tests(per, queries, cfg)} == {"no measured difference"}


def test_domination_counts_large_cases_and_skips_known_items(cfg):
    queries = [query("q1", "t1"), query("q2", "k1", group="known_item")]
    run = make_run({"q1": {"dense": ["big", "big", "good", "bad"]}, "q2": {"dense": ["big"] * 10}})
    per = score_run(run, queries, {"t1": {"good": 2, "bad": 0, "big": 0}, "k1": {"big": 2}}, cfg)
    got = domination_table(per, queries, ["dense"], run, cfg)
    assert got["systems"]["dense"]["queries"] == 1
    assert got["systems"]["dense"]["worst_max_slots"] == 2
    assert got["baseline"]["cutoff_chunks"] == 2  # large_case_top_share 0.5 of 4 cases
    assert got["systems"]["dense"]["hubs"][0]["queries"] == 1


def test_false_similarity_separates_scores_when_relevant_score_higher(cfg):
    queries = [query("q1", "t1"), query("q2", "n1", group="no_answer")]
    run = make_run({"q1": {"dense": ["good", "bad"]}, "q2": {"dense": ["other"]}})
    per = score_run(run, queries, {"t1": {"good": 2, "bad": 0}}, cfg)
    got = false_similarity(per, queries, cfg, {})["dense"]
    assert got["auc"] == 1.0
    assert got["max_not_relevant"] == pytest.approx(0.99)
    assert got["no_answer_top"]["max"] == pytest.approx(1.0)
    assert got["thresholds"][0]["no_answer_queries_passing"] == 1
