"""Lay benchmark, golden আর adversarial set নিজেরা ঠিক আছে কিনা। text_v3 বা আইনের file না থাকলে সেই test skip হয়।"""

import json
import re
import unicodedata
from pathlib import Path

import pytest

from evals.golden_schema import AdversarialItem, GoldenItem, load_adversarial, load_golden
from evals.retrieval_schema import BenchmarkQuery, load_qrels, load_queries

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "evals" / "golden"
LAY = load_queries(GOLDEN / "lay_queries.jsonl")
BENCHMARK = load_queries(GOLDEN / "retrieval_queries.jsonl")
HAS_TEXT = (ROOT / "text_v3").is_dir()
HAS_LAWS = (ROOT / "bangladesh_laws.json").exists()
# আইনি পরিভাষা: uses_legal_terms=False প্রশ্নে এগুলো থাকলে সেটা আর সাধারণ মানুষের ভাষা নয়
LEGAL_WORDS = re.compile(
    r"\b(section|act|ordinance|writ|bail|dower|talaq|custody|appeal|revision|negotiable|dishonour|pre-?emption|contempt|injunction|plaintiff|petition|acquisition)\b"
    r"|ধারা|আইন|রিট|জামিন|দেনমোহর|তালাক|হেফাজত|আপীল|আপিল|রিভিশন|ডিসঅনার|অগ্রক্রয়|অবমাননা|নিষেধাজ্ঞা|অধিগ্রহণ|বাদী",
    re.IGNORECASE,
)


def test_lay_set_size_and_language_mix():
    assert 18 <= len(LAY) <= 25
    counts = {la: sum(q.language == la for q in LAY) for la in ("bn", "en", "mixed")}
    assert counts["bn"] >= 8 and counts["en"] >= 8 and counts["mixed"] >= 2


def test_lay_set_has_varied_styles():
    assert len({q.style for q in LAY}) >= 6
    assert all(q.style and q.group == "lay" for q in LAY)
    assert any(q.style == "banglish" for q in LAY)


def test_no_lay_query_copies_the_benchmark():
    assert not {q.query for q in LAY} & {q.query for q in BENCHMARK}


def test_queries_marked_plain_really_avoid_legal_terms():
    plain = [q for q in LAY if q.uses_legal_terms is False]
    assert len(plain) >= 16
    for q in plain:
        unicodedata.normalize("NFC", q.query)
        assert not LEGAL_WORDS.search(q.query), q.query


def test_lay_needs_have_known_judgements_where_claimed():
    inherited = {q.need_id for q in BENCHMARK}
    new_labels = load_qrels(GOLDEN / "lay_qrels.jsonl")
    labelled = {r.need_id for r in new_labels if r.grade >= 1}
    for q in LAY:
        assert q.need_id in inherited or q.need_id in labelled, q.id


def test_lay_labels_stay_in_their_own_file_and_never_overlap_the_frozen_ones():
    frozen = {(r.need_id, r.case_key) for r in load_qrels(GOLDEN / "retrieval_qrels.jsonl")}
    new = {(r.need_id, r.case_key) for r in load_qrels(GOLDEN / "lay_qrels.jsonl")}
    assert not frozen & new
    assert all(n.startswith("ln-") for n, _ in new)


@pytest.mark.skipif(not HAS_TEXT, reason="text_v3 not available")
def test_lay_evidence_is_verbatim_in_its_case():
    from evals.tools.build_pool import squeeze
    from evals.tools.corpus import load_corpus

    corpus = load_corpus()
    for r in load_qrels(GOLDEN / "lay_qrels.jsonl"):
        if r.grade:
            assert squeeze(r.evidence) in squeeze(corpus[r.case_key]), (r.need_id, r.case_key)


def test_benchmark_group_accepts_lay_without_changing_old_rows():
    assert BenchmarkQuery(id="x", need_id="n", query="q", language="en", group="lay", category="other", type="single-hop").style == ""


GOLD = load_golden(GOLDEN / "golden_set.jsonl")
ADV = load_adversarial(GOLDEN / "adversarial_set.jsonl")


def test_set_sizes_are_in_the_spec_range():
    assert 60 <= len(GOLD) <= 100
    assert 20 <= len(ADV) <= 40


def test_golden_has_both_languages_and_all_kinds():
    assert {g.language for g in GOLD} >= {"bn", "en"}
    assert {g.type for g in GOLD} >= {"single-hop", "case-lookup", "statute-lookup"}


def test_only_reference_answers_that_exist_are_called_draft():
    for g in GOLD:
        assert bool(g.reference_answer) == (g.review_status != "needs_reference"), g.id
        assert g.review_status != "human_verified"  # কোনো মানুষ এখনো যাচাই করেনি


def test_golden_expected_sources_match_the_labels():
    frozen = load_qrels(GOLDEN / "retrieval_qrels.jsonl") + load_qrels(GOLDEN / "lay_qrels.jsonl")
    on_point = {(r.need_id, r.case_key) for r in frozen if r.grade == 2}
    by_id = {q.id: q for q in [*BENCHMARK, *LAY]}
    for g in GOLD:
        if g.source_query_id:
            need = by_id[g.source_query_id].need_id
            assert all((need, s.ref) in on_point for s in g.expected_sources), g.id


@pytest.mark.skipif(not HAS_LAWS, reason="bangladesh_laws.json not available")
def test_statute_items_quote_real_statute_text():
    from evals.tools.build_golden import STATUTE

    laws = {law["title"]: law for law in json.loads((ROOT / "bangladesh_laws.json").read_text(encoding="utf-8"))}
    for _q, _la, _cat, act, section, quote, _ref, _mention in STATUTE:
        texts = [" ".join(s["text"].split()) for s in laws[act]["sections"]]
        hit = [t for t in texts if quote in t]
        assert hit, (act, section, quote)
        assert any(re.match(rf"\[?\(?{re.escape(section)}\b", t) or f"{section}." in t[:20] for t in hit), (act, section)


@pytest.mark.skipif(not HAS_LAWS, reason="bangladesh_laws.json not available")
def test_false_premises_really_contradict_the_statutes():
    laws = {law["title"]: law for law in json.loads((ROOT / "bangladesh_laws.json").read_text(encoding="utf-8"))}

    def section(act: str, heading: str) -> str:
        return next(" ".join(s["text"].split()) for s in laws[act]["sections"] if heading in s.get("section", ""))

    assert "insubordination" in section("The Penal Code, 1860", "Abetment of act of insubordination")
    assert "cheque" not in section("The Penal Code, 1860", "Abetment of act of insubordination")
    assert "death" in section("The Penal Code, 1860", "Punishment for murder")
    talaq = section("The Muslim Family Laws Ordinance, 1961", "Talaq")
    assert "Chairman notice in writing" in talaq and "ninety days" in talaq
    assert "notwithstanding any other title" in section("The Specific Relief Act, 1877", "Suit by person dispossessed")


def test_adversarial_covers_every_type():
    assert {a.adv_type for a in ADV} == {"no_answer", "false_premise", "injection", "vague_prediction"}
    assert all(a.expected_behavior == {"no_answer": "abstain", "false_premise": "correct_premise", "injection": "resist", "vague_prediction": "clarify"}[a.adv_type] for a in ADV)


def test_false_premise_items_can_be_scored():
    assert all(a.must_mention for a in ADV if a.adv_type == "false_premise")


def test_injection_has_question_level_and_planted_cases():
    injected = [a for a in ADV if a.adv_type == "injection"]
    assert sum(bool(a.planted_snippet) for a in injected) >= 3
    assert sum(not a.planted_snippet for a in injected) >= 3
    for a in injected:
        assert a.canary in a.question or a.canary in a.planted_snippet


def test_injection_needs_a_canary():
    with pytest.raises(ValueError):
        AdversarialItem(id="x", question="q", language="en", category="other", type="single-hop", adv_type="injection", expected_behavior="resist")


def test_golden_item_rejects_non_nfc():
    with pytest.raises(ValueError):
        GoldenItem(id="x", question="রায়", language="bn", category="other", type="single-hop")
