"""Retrieval metric গুলো হাতে হিসাব করা উত্তরের সাথে, আর হিসাবের নিয়মগুলো (অফলাইন, model লাগে না)।"""

import math

import pytest

from evals.metrics.retrieval import (
    auc,
    capped_recall_at_k,
    domination,
    holm_adjust,
    judged_at_k,
    ndcg_at_k,
    paired_bootstrap_ci,
    precision_at_k,
    recall_at_k,
    rr_at_k,
    sign_flip_p,
)

GRADES = {"a": 2, "b": 1, "c": 0, "d": 2}


def test_precision_counts_empty_slots_as_misses():
    assert precision_at_k(["a", "x", "b"], GRADES, 10) == pytest.approx(0.2)
    assert precision_at_k(["a", "b"], GRADES, 2) == 1.0
    assert precision_at_k(["a", "b"], GRADES, 2, threshold=2) == 0.5


def test_recall_uses_only_top_k_and_known_relevant():
    assert recall_at_k(["a", "x"], GRADES, 1) == pytest.approx(1 / 3)
    assert recall_at_k(["a", "b", "d"], GRADES, 3) == 1.0
    assert recall_at_k(["a", "x", "d"], GRADES, 3, threshold=2) == 1.0


def test_no_known_relevant_is_undefined_not_zero():
    none = {"c": 0}
    assert recall_at_k(["c"], none, 10) is None
    assert capped_recall_at_k(["c"], none, 10) is None
    assert rr_at_k(["c"], none, 10) is None
    assert ndcg_at_k(["c"], none, 10) is None


def test_capped_recall_reaches_one_with_many_relevant():
    many = {f"r{i}": 1 for i in range(100)}
    top = [f"r{i}" for i in range(10)]
    assert recall_at_k(top, many, 10) == pytest.approx(0.1)
    assert capped_recall_at_k(top, many, 10) == 1.0


def test_reciprocal_rank():
    assert rr_at_k(["x", "y", "b"], GRADES, 10) == pytest.approx(1 / 3)
    assert rr_at_k(["x", "y", "b"], GRADES, 2) == 0.0
    assert rr_at_k(["b", "a"], GRADES, 10, threshold=2) == 0.5


def test_ndcg_by_hand():
    grades = {"a": 2, "b": 1}
    assert ndcg_at_k(["a", "b"], grades, 10) == 1.0
    # উল্টো ক্রম: DCG = 1/log2(2) + 3/log2(3); আদর্শ = 3/log2(2) + 1/log2(3)
    want = (1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3))
    assert ndcg_at_k(["b", "a"], grades, 10) == pytest.approx(want)
    assert ndcg_at_k(["x"], grades, 10) == 0.0


def test_judged_fraction_exposes_unjudged_results():
    assert judged_at_k(["a", "c", "x", "y"], GRADES, 4) == 0.5
    assert judged_at_k([], GRADES, 4) == 0.0


def test_domination_works_on_chunk_slots():
    d = domination(["big"] * 7 + ["a", "b", "c", "late"], 10)
    assert d == {"max_slots_one_case": 7, "top_case": "big", "distinct_cases": 4}
    assert domination([], 10)["max_slots_one_case"] == 0


def test_auc():
    assert auc([3, 4], [1, 2]) == 1.0
    assert auc([1, 2], [3, 4]) == 0.0
    assert auc([1, 1], [1, 1]) == 0.5
    assert auc([], [1]) is None


def test_no_difference_is_not_a_win():
    a = [0.5, 0.2, 0.9, 0.4]
    mean, low, high = paired_bootstrap_ci(a, a, ["1", "2", "3", "4"], 500, 1)
    assert (mean, low, high) == (0.0, 0.0, 0.0)
    assert sign_flip_p(a, a, ["1", "2", "3", "4"], 500, 1) == 1.0


def test_large_consistent_difference_is_detected():
    n = 30
    a, b = [0.8 + i * 0.001 for i in range(n)], [0.2 + i * 0.002 for i in range(n)]
    ids = [str(i) for i in range(n)]
    mean, low, high = paired_bootstrap_ci(a, b, ids, 2000, 1)
    assert low > 0 and high > low and low <= mean <= high
    assert sign_flip_p(a, b, ids, 2000, 1) < 0.01


def test_one_big_query_does_not_prove_a_win():
    n = 30
    a, b = [0.0] * n, [0.0] * n
    a[0] = 1.0
    ids = [str(i) for i in range(n)]
    _, low, _ = paired_bootstrap_ci(a, b, ids, 2000, 1)
    assert low == 0.0
    assert sign_flip_p(a, b, ids, 2000, 1) > 0.5


def test_pair_of_queries_from_one_need_counts_once():
    # একই need এর দুটো প্রশ্ন: ৪ টা প্রশ্ন হলেও আসলে ২ টা স্বাধীন মাপ
    a, b = [1.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 1.0]
    mean, _, _ = paired_bootstrap_ci(a, b, ["n1", "n1", "n2", "n2"], 200, 1)
    assert mean == 0.0


def test_holm_is_monotone_and_never_below_raw():
    adj = holm_adjust({"x": 0.01, "y": 0.04, "z": 0.03})
    assert adj["x"] == pytest.approx(0.03)
    assert adj["z"] == pytest.approx(0.06)
    assert adj["y"] == pytest.approx(0.06)
    assert holm_adjust({"x": 0.9, "y": 0.9})["x"] == 1.0
