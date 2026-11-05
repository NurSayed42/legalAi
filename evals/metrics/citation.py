"""
evals/metrics/citation.py
=========================
উত্তরের citation গুলো কতটা সত্যি। তিন রকম ভুল আলাদা: retrieve হয়নি কিন্তু corpus এ আছে (unsupported), corpus এই নেই
(fabricated), আর প্রত্যাশিত উৎস উদ্ধৃত হয়নি (recall)। Model লাগে না।
"""

import re

from ingestion.chunk_cap import case_key
from legal_rules import BN_DIGITS

# api_v3._TOKEN_RE এর সমান, api_v3._norm_cite এর মতো মেলানোর জন্য
_TOKEN_RE = re.compile("[\\wঀ-৿‌‍]+", re.UNICODE)
_SECTION_NO = re.compile(r"\d+[a-z]?")


def norm_cite(value) -> str:
    return "".join(_TOKEN_RE.findall(str(value or "").translate(BN_DIGITS).lower()))


def section_number(section) -> str:
    found = _SECTION_NO.search(str(section or "").translate(BN_DIGITS).lower())
    return found.group() if found else ""


def build_registry(case_metas: list[dict], law_metas: list[dict]) -> dict:
    """পুরো index এ কী কী আছে, fabricated ধরার জন্য।"""
    cases: set[str] = set()
    for m in case_metas:
        cases.add(norm_cite(m.get("case_no")))
        cases.add(norm_cite(m.get("filename")))
    laws: dict[str, set[str]] = {}
    for m in law_metas:
        laws.setdefault(norm_cite(m.get("title")), set()).add(norm_cite(m.get("section_no")))
    cases.discard("")
    return {"cases": cases, "laws": laws}


def _law_key(act, section) -> tuple[str, str]:
    return norm_cite(act), norm_cite(section_number(section))


def citation_metrics(cited_cases: list[str], cited_laws: list[dict], retrieved_cases: list[dict], retrieved_laws: list[dict], registry: dict, expected_sources: list[dict], expected_match: str = "any") -> dict:
    retrieved_case_map: dict[str, str] = {}
    for m in retrieved_cases:
        key = case_key(m.get("filename") or "")
        for name in (m.get("case_no"), m.get("filename")):
            if norm_cite(name):
                retrieved_case_map[norm_cite(name)] = key
    retrieved_law_keys = {_law_key(m.get("title"), m.get("section_no") or m.get("section")) for m in retrieved_laws}

    in_retrieved = unsupported = fabricated = 0
    cited_case_keys: set[str] = set()
    for c in cited_cases:
        n = norm_cite(c)
        if n in retrieved_case_map:
            in_retrieved += 1
            cited_case_keys.add(retrieved_case_map[n])
        elif n in registry["cases"]:
            unsupported += 1
        else:
            fabricated += 1
    cited_law_keys: set[tuple[str, str]] = set()
    for law in cited_laws:
        key = _law_key(law.get("act"), law.get("section"))
        cited_law_keys.add(key)
        if key in retrieved_law_keys:
            in_retrieved += 1
        elif key[1] and key[1] in registry["laws"].get(key[0], set()):
            unsupported += 1
        else:
            fabricated += 1
    cited = len(cited_cases) + len(cited_laws)

    hits = 0
    for src in expected_sources:
        if src["kind"] == "judgement":
            hits += case_key(src["ref"]) in cited_case_keys
        else:
            act, _, section = src["ref"].partition("|")
            hits += _law_key(act, section) in cited_law_keys
    expected = len(expected_sources)
    if not expected:
        recall = None
    elif expected_match == "any":
        recall = 1.0 if hits else 0.0
    else:
        recall = hits / expected
    return {"cited": cited, "in_retrieved": in_retrieved, "unsupported": unsupported, "fabricated": fabricated, "precision": in_retrieved / cited if cited else None,
            "fabricated_rate": fabricated / cited if cited else None, "recall": recall, "expected_hits": hits, "expected": expected}


def aggregate_citations(rows: list[dict]) -> dict:
    """সব citation মিলিয়ে হিসাব (micro), আর recall প্রশ্ন ধরে গড়।"""
    cited = sum(r["cited"] for r in rows)
    recalls = [r["recall"] for r in rows if r["recall"] is not None]
    return {"cited": cited, "precision": sum(r["in_retrieved"] for r in rows) / cited if cited else None, "fabricated_rate": sum(r["fabricated"] for r in rows) / cited if cited else None,
            "unsupported_rate": sum(r["unsupported"] for r in rows) / cited if cited else None, "recall": sum(recalls) / len(recalls) if recalls else None, "questions_with_expected": len(recalls),
            "questions_with_a_fabrication": sum(r["fabricated"] > 0 for r in rows)}
