"""মানুষের review এর নিয়ম: label ফাইল কখনো বদলায় না, সিদ্ধান্ত আলাদা থাকে, আর চাইলেই শুধু score এ লাগে।"""

import csv
import hashlib
from pathlib import Path

import pytest

from evals.retrieval_schema import HumanDecision, Qrel, apply_human_decisions, latest_decisions, load_decisions, load_qrels
from evals.tools.human_review import (
    SHEET,
    SHEET_FIELDS,
    append_log,
    best_chunks,
    decisions_from_rows,
    known_labels,
    locate_page,
    review_id,
    review_status,
)

ROOT = Path(__file__).resolve().parent.parent
QRELS = ROOT / "evals" / "golden" / "retrieval_qrels.jsonl"
# Phase 2 এ commit করা label এর hash। এটা বদলালে কেউ label বদলেছে, যা শুধু সচেতন সিদ্ধান্তে হওয়ার কথা (D-207)।
FROZEN_QRELS_SHA = "4ae03634ec39"
FROZEN_QUERIES_SHA = "24baabdf691a"


def qrel(grade: int, need: str = "n", case: str = "c") -> Qrel:
    return Qrel(need_id=need, case_key=case, grade=grade, evidence="quote" if grade else "", judged_by="claude")


def decision(kind: str, proposed: int = 2, human: int | None = None, need: str = "n", case: str = "c", reviewer: str = "R") -> HumanDecision:
    return HumanDecision(review_id=review_id(need, case), need_id=need, case_key=case, proposed_grade=proposed, decision=kind, human_grade=human, reviewer=reviewer, reviewed_on="2026-11-06")


def test_frozen_labels_and_queries_are_unchanged():
    assert hashlib.sha256(QRELS.read_bytes()).hexdigest()[:12] == FROZEN_QRELS_SHA
    assert hashlib.sha256((ROOT / "evals" / "golden" / "retrieval_queries.jsonl").read_bytes()).hexdigest()[:12] == FROZEN_QUERIES_SHA


def test_no_label_is_marked_verified_by_a_person_in_the_labels_file():
    assert not any(r.human_verified for r in load_qrels(QRELS))


def test_change_applies_only_to_the_returned_copy():
    original = [qrel(2)]
    updated, stats = apply_human_decisions(original, [decision("change", 2, 0)])
    assert updated[0].grade == 0 and updated[0].human_verified
    assert original[0].grade == 2 and not original[0].human_verified
    assert stats["changed"] == 1


def test_agree_keeps_the_grade_and_marks_it_reviewed():
    updated, stats = apply_human_decisions([qrel(1)], [decision("agree", 1)])
    assert (updated[0].grade, updated[0].human_verified, stats["agreed"]) == (1, True, 1)


def test_unsure_leaves_the_label_as_it_was():
    updated, stats = apply_human_decisions([qrel(2)], [decision("unsure", 2)])
    assert (updated[0].grade, updated[0].human_verified, stats["unsure"]) == (2, False, 1)


def test_latest_decision_wins():
    log = [decision("agree", 2), decision("change", 2, 1)]
    assert latest_decisions(log)[("n", "c")].decision == "change"
    assert apply_human_decisions([qrel(2)], log)[0][0].grade == 1


@pytest.mark.parametrize("kwargs", [
    {"decision": "change", "human_grade": None},
    {"decision": "change", "human_grade": 2},
    {"decision": "agree", "human_grade": 0},
    {"decision": "unsure", "human_grade": 1},
    {"decision": "agree", "human_grade": None, "reviewer": "  "},
])
def test_inconsistent_decisions_are_rejected(kwargs):
    base = {"review_id": "x", "need_id": "n", "case_key": "c", "proposed_grade": 2, "reviewer": "R", "reviewed_on": "2026-11-06"}
    with pytest.raises(ValueError):
        HumanDecision(**{**base, **kwargs})


def _known():
    return known_labels([qrel(2)])


def _row(**kw):
    return {"review_id": review_id("n", "c"), "decision": "", "human_grade": "", "reviewer_notes": "", **kw}


def test_blank_rows_are_skipped():
    assert decisions_from_rows([_row()], _known(), "R", "2026-11-06") == []


def test_import_reads_a_change_with_its_note():
    got = decisions_from_rows([_row(decision="Change", human_grade="1", reviewer_notes="minor part")], _known(), "R", "2026-11-06")
    assert (got[0].decision, got[0].human_grade, got[0].notes, got[0].reviewer) == ("change", 1, "minor part", "R")


def test_unknown_row_stops_the_whole_import():
    with pytest.raises(SystemExit):
        decisions_from_rows([_row(review_id="rv-unknown", decision="agree")], _known(), "R", "2026-11-06")


def test_import_needs_a_reviewer_name():
    with pytest.raises(ValueError):
        decisions_from_rows([_row(decision="agree")], _known(), "", "2026-11-06")


def test_log_only_grows_and_skips_exact_repeats(tmp_path):
    log = tmp_path / "log.jsonl"
    assert append_log(log, [decision("agree")]) == 1
    assert append_log(log, [decision("agree")]) == 0
    assert append_log(log, [decision("change", 2, 1)]) == 1
    assert [d.decision for d in load_decisions(log)] == ["agree", "change"]


def test_status_counts_and_ignores_grade_zero_labels():
    qrels = [qrel(2, case="a"), qrel(1, case="b"), qrel(0, case="z"), qrel(2, case="u")]
    log = [decision("agree", case="a"), decision("change", 1, 2, case="b"), decision("unsure", case="u")]
    got = review_status(qrels, log)
    assert (got["positive_labels"], got["reviewed"], got["agreed"], got["changed"], got["unsure"], got["remaining"]) == (3, 3, 1, 1, 1, 0)
    assert got["agreement_rate"] == 0.5
    assert got["changes"] == {"1 to 2": 1}


def test_locate_page_finds_the_page_and_a_quote_across_pages():
    pages = ["first page text", "", "second part ends here", "next page begins"]
    assert locate_page(pages, "second part") == 3
    assert locate_page(pages, "ends here next page") == 3
    assert locate_page(pages, "not there") is None


def test_best_chunk_keeps_the_best_rank_and_names_no_system():
    run = {"config": {"k": 2}, "results": {"q": {"dense": {"chunks": [["c1#1", "c1", 0.9], ["c2#1", "c2", 0.8]]}, "bm25": {"chunks": [["c2#7", "c2", 5.0], ["c1#3", "c1", 4.0]]}}}}

    class Q:
        id = "q"

    got = best_chunks(run, {"n": [Q()]})
    assert got[("n", "c2")] == "c2#7" and got[("n", "c1")] == "c1#1"


@pytest.mark.skipif(not SHEET.exists(), reason="sheet not exported")
def test_sheet_has_exactly_the_positive_labels_and_no_system_names():
    with SHEET.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    positive = {review_id(r.need_id, r.case_key): r for r in load_qrels(QRELS) if r.grade >= 1}
    assert len(rows) == len(positive) == 564
    assert {r["review_id"] for r in rows} == set(positive)
    assert list(rows[0]) == SHEET_FIELDS
    for row in rows:
        assert int(row["proposed_grade"]) == positive[row["review_id"]].grade
        assert row["queries"] and row["judgement"] and row["graded_passage"]
        assert not row["decision"] and not row["human_grade"]
    header = " ".join(SHEET_FIELDS)
    for name in ("dense", "bm25", "hybrid", "rerank"):
        assert name not in header


def test_review_page_treats_judgement_text_as_data():
    template = (ROOT / "evals" / "tools" / "review_template.html").read_text(encoding="utf-8")
    assert "/*__DATA__*/null" in template
    assert "innerHTML" not in template
    assert "<script src" not in template  # বাইরের কিছু লোড হয় না
    assert "fetch(" not in template  # সিদ্ধান্ত কোথাও পাঠায় না
