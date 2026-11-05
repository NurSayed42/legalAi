"""Benchmark file গুলো নিজেরা ঠিক আছে কিনা, আর qrels এর প্রমাণ সত্যিই text এ আছে কিনা।

Qrels বা text_v3 না থাকলে সেই test skip হয়, কারণ text_v3 git এ নেই।"""

import json
import unicodedata
from pathlib import Path

import pytest

from evals.retrieval_schema import BenchmarkQuery, Qrel, load_config, load_qrels, load_queries

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "evals" / "golden"
QUERIES = GOLDEN / "retrieval_queries.jsonl"
QRELS = GOLDEN / "retrieval_qrels.jsonl"
TARGETS = GOLDEN / "known_targets.json"
HAS_TEXT = (ROOT / "text_v3").is_dir()


@pytest.fixture(scope="module")
def queries() -> list[BenchmarkQuery]:
    if not QUERIES.exists():
        pytest.skip("evaluation data is local only (D-211)")
    return load_queries(QUERIES)


@pytest.fixture(scope="module")
def corpus() -> dict[str, str]:
    from evals.tools.corpus import load_corpus, normalize

    return {k: normalize(v) for k, v in load_corpus().items()}


def test_config_loads():
    config = load_config(ROOT / "evals" / "retrieval_config.json")
    assert config.k == 10 and config.primary_metric == "ndcg"
    assert config.rerank_candidates <= config.fusion_depth * 2 <= config.fetch_depth * 2


def test_benchmark_has_both_languages_in_every_group(queries):
    for group in ("known_item", "legal_term", "topic", "dr58", "no_answer"):
        sub = [q for q in queries if q.group == group]
        assert sub, group
        assert {q.language for q in sub} >= {"bn", "en"}, group


def test_size_is_in_spec_range(queries):
    assert 60 <= len(queries) <= 100


def test_paired_groups_are_really_paired(queries):
    for group in ("legal_term", "topic", "dr58", "no_answer"):
        needs: dict[str, list[str]] = {}
        for q in queries:
            if q.group == group:
                needs.setdefault(q.need_id, []).append(q.language)
        assert all(sorted(v) == ["bn", "en"] for v in needs.values()), group


def test_queries_and_terms_are_nfc(queries):
    for q in queries:
        assert all(unicodedata.normalize("NFC", t) == t for t in q.key_terms), q.id


def test_rejects_non_nfc_query():
    with pytest.raises(ValueError):
        BenchmarkQuery(id="x", need_id="x", query="রায়", language="bn", group="topic", category="other", type="single-hop")


def test_positive_qrel_needs_evidence():
    with pytest.raises(ValueError):
        Qrel(need_id="x", case_key="y", grade=2, judged_by="me")
    assert Qrel(need_id="x", case_key="y", grade=0, judged_by="me").evidence == ""


def test_every_known_target_need_exists(queries):
    needs = {q.need_id for q in queries}
    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    assert set(targets) <= needs
    for q in queries:
        if q.group in ("known_item", "dr58"):
            assert q.need_id in targets, q.id


@pytest.mark.skipif(not HAS_TEXT, reason="text_v3 নেই")
def test_known_targets_contain_their_key_term(queries, corpus):
    from evals.tools.corpus import normalize

    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    for q in queries:
        if q.group == "known_item":
            for case in targets[q.need_id]:
                text = " ".join(corpus[case].split())
                assert any(normalize(t) in text for t in q.key_terms), (q.id, case)


@pytest.mark.skipif(not HAS_TEXT, reason="text_v3 নেই")
def test_no_answer_terms_really_are_absent(queries, corpus):
    from evals.tools.corpus import term_pattern

    for q in queries:
        if q.group == "no_answer":
            for term in q.key_terms:
                pattern = term_pattern(term)
                assert not any(pattern.search(text) for text in corpus.values()), (q.id, term)


@pytest.mark.skipif(not QRELS.exists(), reason="qrels এখনো তৈরি হয়নি")
def test_qrels_refer_to_known_needs(queries):
    needs = {q.need_id for q in queries}
    for r in load_qrels(QRELS):
        assert r.need_id in needs, r.need_id
    no_answer = {q.need_id for q in queries if q.group == "no_answer"}
    assert not [r for r in load_qrels(QRELS) if r.need_id in no_answer and r.grade > 0]


@pytest.mark.skipif(not (QRELS.exists() and HAS_TEXT), reason="qrels বা text_v3 নেই")
def test_every_evidence_quote_is_verbatim_in_its_case():
    from evals.tools.corpus import load_corpus

    raw = load_corpus()
    squeeze = lambda s: " ".join(unicodedata.normalize("NFC", s).split())  # noqa: E731
    texts = {k: squeeze(v) for k, v in raw.items()}
    bad = [(r.need_id, r.case_key) for r in load_qrels(QRELS) if r.grade >= 1 and squeeze(r.evidence) not in texts[r.case_key]]
    assert not bad
