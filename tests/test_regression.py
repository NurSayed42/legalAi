"""
/api/chat regression test — চালু server (:8000) আর আসল LLM লাগে।

    uvicorn api_v3:app --port 8000          # অন্য terminal এ
    python -m pytest tests/test_regression.py -v

UI testing এ পাওয়া চারটা প্রশ্ন: চেক ডিসঅনারের প্রক্রিয়া, খুনের মামলায় জামিন, "Section 302",
আর তালাকের প্রক্রিয়া।
"""

import functools
import re
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from legal_rules import find_persona_claims  # noqa: E402

API = "http://localhost:8000"
ANSWER_KEYS = ["short_answer", "applicable_laws", "relevant_cases", "procedure", "risks",
               "when_to_see_lawyer", "follow_up_options", "disclaimer", "summary"]   # summary = পুরনো alias


@functools.lru_cache(maxsize=None)
def ask(question: str) -> dict:
    try:
        r = requests.post(f"{API}/api/chat", json={"question": question}, timeout=300)
    except requests.ConnectionError:
        pytest.skip(f"API {API} এ চালু নেই")
    assert r.status_code == 200, r.text
    return r.json()


def cites(data: dict, act_fragment: str, sections: set[str]) -> bool:
    for law in data["answer"]["applicable_laws"]:
        num = re.search(r"\d+[A-Z]?", law["section"])
        if act_fragment.lower() in law["act"].lower() and num and num.group() in sections:
            return True
    return False


def check_common(data: dict):
    """প্রতিটা উত্তরে: fixed structure, verification field গুলো চালু, persona দাবি নেই,
    আর উত্তরের প্রতিটা আইন/মামলা sources এ আছে।"""
    answer = data["answer"]
    assert list(answer) == ANSWER_KEYS
    assert answer["short_answer"].strip()
    assert data["citation_verified"] in (True, False)
    assert isinstance(data["unverified_citations"], list)
    assert data["retrieval_attempts"] >= 1
    assert not find_persona_claims(answer), find_persona_claims(answer)

    source_laws = {(l["title"], l["section"]) for l in data["sources"]["laws"]}
    for law in answer["applicable_laws"]:
        assert (law["act"], law["section"]) in source_laws, law
    for case in answer["relevant_cases"]:
        assert case["division"] in ("HCD", "AD") and case["holding"].strip(), case
    assert len(answer["relevant_cases"]) <= 3


def check_procedure(data: dict):
    proc = data["answer"]["procedure"]
    assert len(proc["steps"]) >= 2 and all(re.match(r"^\s*[0-9০-৯]+\s*[.)।]", s) for s in proc["steps"]), proc["steps"]
    for key in ("court", "deadline", "penalties"):
        assert proc[key].strip(), key
    assert proc["documents"]


def test_cheque_dishonour_procedure():
    data = ask("চেক ডিসঅনার হলে মামলা করার প্রক্রিয়া কী?")
    check_common(data)
    assert data["intent"] == "procedure"
    assert data["retrieval_sufficient"]
    assert cites(data, "Negotiable Instruments", {"138"})
    check_procedure(data)
    assert data["sources"]["cases"], "cheque dishonour এর judgement retrieve হওয়া উচিত"
    assert data["answer"]["relevant_cases"]


def test_murder_bail():
    data = ask("খুনের মামলায় জামিন পাওয়া যায় কি?")
    check_common(data)
    assert data["intent"] != "law_lookup"
    assert cites(data, "Code of Criminal Procedure", {"497", "498"})
    assert data["sources"]["cases"]
    assert data["answer"]["relevant_cases"]


def test_section_302_answers_only_penal_code():
    data = ask("Section 302")
    check_common(data)
    assert data["intent"] == "law_lookup"
    laws = data["answer"]["applicable_laws"]
    assert laws and all("Penal Code" in l["act"] for l in laws), laws
    assert laws[0]["section"].startswith("Section 302")


def test_divorce_procedure():
    data = ask("তালাক দেওয়ার প্রক্রিয়া কী?")
    check_common(data)
    assert data["intent"] == "procedure"
    assert cites(data, "Muslim Family Laws", {"7"})
    check_procedure(data)
