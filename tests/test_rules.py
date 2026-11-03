"""
legal_rules.py এর deterministic নিয়মের unit test — model/DB/server লাগে না।

    python -m pytest tests/test_rules.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from legal_rules import (  # noqa: E402
    CONSTITUTION,
    CRPC,
    MFLO,
    NI_ACT,
    PENAL_CODE,
    detect_acts,
    detect_section_request,
    expand_query,
    find_persona_claims,
    inline_section_mentions,
    is_only_question,
    normalize_answer,
    procedure_gaps,
    resolve_intent,
    strip_persona_sentences,
)


def _pinned(question):
    return {s for hit in expand_query(question) for s in hit["sections"]}


def test_alias_cheque_dishonour_banglish_and_bangla():
    for q in ("chek dishonour hole ki korbo?", "চেক ডিসঅনার হলে মামলা করার প্রক্রিয়া কী?"):
        assert {(NI_ACT, "138"), (NI_ACT, "140"), (NI_ACT, "141")} <= _pinned(q)


def test_alias_bail_talaq_land_writ():
    assert {(CRPC, "497"), (CRPC, "498")} <= _pinned("খুনের মামলায় জামিন পাওয়া যায় কি?")
    assert (MFLO, "7") in _pinned("তালাক দেওয়ার প্রক্রিয়া কী?")
    assert (CRPC, "145") in _pinned("jomi dokhol hoye gele ki mamla korbo?")
    assert (CONSTITUTION, "102") in _pinned("How do I file a writ petition?")


def test_section_request_forms():
    assert detect_section_request("Section 302") == {"section_no": "302", "act_ids": [], "is_article": False}
    assert detect_section_request("ধারা ৪২০ কী বলে?")["section_no"] == "420"
    assert detect_section_request("৪২০ ধারা কী")["section_no"] == "420"
    assert detect_section_request("Section 145 CrPC")["act_ids"] == [CRPC]
    assert detect_section_request("Article 102 of the Constitution")["act_ids"] == [CONSTITUTION]
    assert detect_section_request("s. 138A NI Act")["section_no"] == "138A"
    assert detect_section_request("what are the rules. 5 people") is None


def test_detect_acts_does_not_confuse_crpc_and_cpc():
    assert detect_acts("Section 145 CrPC") == [CRPC]
    assert detect_acts("দণ্ডবিধির ৩০২ ধারা") == [PENAL_CODE]
    assert detect_acts("পেনাল কোড, ১৮৬০") == [PENAL_CODE]
    assert detect_acts("নেগোশিয়েবল ইনস্ট্রুমেন্টস অ্যাক্ট") == [NI_ACT]


def test_normalize_strips_copied_source_labels():
    a = normalize_answer({"applicable_laws": [{"act": "Act: The Penal Code, 1860", "section": "Section: Section 302 — X"}],
                          "relevant_cases": [{"case_no": "Case: Crl. Rev. 1 of 2020"}]})
    assert a["applicable_laws"][0]["act"] == "The Penal Code, 1860"
    assert a["applicable_laws"][0]["section"] == "Section 302 — X"
    assert a["relevant_cases"][0]["case_no"] == "Crl. Rev. 1 of 2020"


def test_intent_rules():
    assert resolve_intent("চেক ডিসঅনার হলে মামলা করার প্রক্রিয়া কী?", "law_lookup") == "procedure"
    assert resolve_intent("তালাক দেওয়ার প্রক্রিয়া কী?", "general") == "procedure"
    assert resolve_intent("আমার বিরুদ্ধে মিথ্যা মামলা হয়েছে, আমি কি জিতবো?", "general") == "prediction"
    assert resolve_intent("Section 302", "general") == "law_lookup"
    # router law_lookup বললেও Act/ধারার নাম না থাকলে general — নইলে judgements খোঁজা হয় না
    assert resolve_intent("খুনের মামলায় জামিন পাওয়া যায় কি?", "law_lookup") == "general"
    assert resolve_intent("Section 497 of the Code of Criminal Procedure", "general") == "law_lookup"
    assert resolve_intent("anything", "law_lookup", forced="prediction") == "prediction"


def test_persona_claims_caught():
    claims = [
        "আমার দীর্ঘ ৩০ বছরের প্র্যাকটিসে দেখেছি এমন মামলা টেকে না।",
        "আমি এমন অনেক মামলা পরিচালনা করেছি।",
        "আইনজীবী হিসেবে আমার মত হলো আপনি জিতবেন।",
        "In my 20 years of practice this rarely succeeds.",
        "I have handled many such cases.",
    ]
    for text in claims:
        assert find_persona_claims({"short_answer": text}), text


def test_persona_no_false_positive_on_advice():
    ok = [
        "বার কাউন্সিলের তালিকাভুক্ত একজন আইনজীবীর পরামর্শ নিন।",
        "As a lawyer would advise, file within the deadline.",
        "অভিজ্ঞ একজন আইনজীবীর সাথে কথা বলুন।",
    ]
    for text in ok:
        assert not find_persona_claims({"short_answer": text}), text


def test_strip_persona_keeps_other_sentences():
    out = strip_persona_sentences({"short_answer": "আমার ৩০ বছরের অভিজ্ঞতায় এটা সাধারণ। ধারা ৩০২ অনুযায়ী মৃত্যুদণ্ড হতে পারে।"})
    assert out["short_answer"] == "ধারা ৩০২ অনুযায়ী মৃত্যুদণ্ড হতে পারে।"


def test_inline_sections_skip_structured_fields():
    answer = normalize_answer({
        "short_answer": "ধারা ৪২০ আর s.138(1) দেখুন",
        "applicable_laws": [{"act": "X", "section": "Section 9 — heading"}],
    })
    assert sorted(no for _, no in inline_section_mentions(answer)) == ["138", "420"]


def test_normalize_answer_fixed_schema():
    a = normalize_answer({"summary": "old style", "procedure": {"steps": ["file complaint", "2. hearing"]}})
    assert list(a) == ["short_answer", "applicable_laws", "relevant_cases", "procedure", "risks",
                       "when_to_see_lawyer", "follow_up_options", "disclaimer"]
    assert a["short_answer"] == "old style"
    assert a["procedure"]["steps"] == ["1. file complaint", "2. hearing"]
    assert normalize_answer("not a dict")["applicable_laws"] == []


def test_procedure_and_question_only_checks():
    assert procedure_gaps(normalize_answer({})) == ["steps", "court", "deadline", "documents", "penalties"]
    assert is_only_question(normalize_answer({"short_answer": "আপনার মামলাটা কোন আদালতে?"}))
    assert not is_only_question(normalize_answer({"short_answer": "x?", "risks": ["r"]}))
