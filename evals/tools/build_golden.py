"""
evals/tools/build_golden.py
===========================
Retrieval benchmark আর কয়েকটা statute প্রশ্ন থেকে SPEC এর golden_set.jsonl বানায়। Benchmark এর label বদলায় না।

Statute প্রশ্নের reference answer আইনের লেখা থেকে নেওয়া খসড়া (test এ মেলানো হয়)। বাকিগুলোর reference answer ইচ্ছা করে ফাঁকা:
আইনি উত্তর মানুষের লেখা বা যাচাই করা দরকার (SPEC), তাই correctness সেগুলোতে মাপা হয় না যতক্ষণ না পূরণ হয়।

    python evals/tools/build_golden.py
"""

import json
import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.golden_schema import GoldenItem, SourceRef  # noqa: E402
from evals.retrieval_schema import grades_by_need, load_qrels, load_queries, write_jsonl  # noqa: E402

GOLDEN = ROOT / "evals" / "golden"
KNOWN_ITEM_LIMIT = 14  # প্রতিটা প্রশ্নে কয়েকটা LLM call, আর SPEC এর সীমা ১০০
COMMON_NOT_CLAIM = ["নিশ্চিত জিতবেন", "will definitely win", "guaranteed to win", "বছরের অভিজ্ঞতা"]
MUST_MENTION = {
    "t-01": ["138|১৩৮"], "t-04": ["নারী ও শিশু|nari"], "t-05": ["দেনমোহর|dower|মোহর"], "t-07": ["মাদক|narcotic"], "t-08": ["অধিগ্রহণ|acquisition"], "t-09": ["অর্থ ঋণ|অর্থঋণ|artha rin"],
    "t-10": ["অগ্রক্রয়|pre-emption|preemption"], "t-11": ["অর্পিত|vested"], "t-12": ["অবমাননা|contempt"], "t-13": ["আয়কর|income tax"], "t-14": ["শ্রম|labour"], "t-16": ["চুক্তি|performance|contract"],
    "l-01": ["561|৫৬১"], "l-02": ["res judicata|রেস জুডিকাটা|11|১১"], "l-03": ["locus|লোকাস"], "l-04": ["34|৩৪"], "l-05": ["তামাদি|limitation"], "l-06": ["dying|মৃত্যুকালীন"],
}
PENAL, NI, MFLO, LIMITATION, CRPC, CPC, SRA = "The Penal Code, 1860", "The Negotiable Instruments Act, 1881", "The Muslim Family Laws Ordinance, 1961", "The Limitation Act, 1908", "The Code of Criminal Procedure, 1898", "The Code of Civil Procedure, 1908", "The Specific Relief Act, 1877"
# (প্রশ্ন, ভাষা, category, আইন, ধারা, আইনের লেখা থেকে হুবহু অংশ যা test মেলায়, reference, must_mention)
STATUTE = [
    ("What does section 302 of the Penal Code say?", "en", "criminal", PENAL, "302", "shall be punished with death", "Whoever commits murder shall be punished with death, or imprisonment for life, and is also liable to fine.", ["death|মৃত্যুদণ্ড", "life|যাবজ্জীবন"]),
    ("দণ্ডবিধির ৩০২ ধারায় খুনের শাস্তি কী?", "bn", "criminal", PENAL, "302", "shall be punished with death", "খুনের শাস্তি মৃত্যুদণ্ড অথবা যাবজ্জীবন কারাদণ্ড, এবং অর্থদণ্ডও হতে পারে।", ["মৃত্যুদণ্ড|death"]),
    ("What does section 34 of the Penal Code say?", "en", "criminal", PENAL, "34", "in furtherance of the common intention", "When several persons do a criminal act in furtherance of the common intention of all, each is liable as if he had done it alone.", ["common intention|সাধারণ অভিপ্রায়"]),
    ("What is section 138 of the Negotiable Instruments Act about?", "en", "criminal", NI, "138", "is returned by the bank unpaid", "A cheque returned unpaid by the bank for insufficient funds is an offence by the person who drew it.", ["cheque|চেক"]),
    ("নেগোশিয়েবল ইনস্ট্রুমেন্টস অ্যাক্টের ১৩৮ ধারা কী নিয়ে?", "bn", "criminal", NI, "138", "is returned by the bank unpaid", "তহবিলের অভাবে ব্যাংক চেক ফেরত দিলে চেকদাতার অপরাধ।", ["চেক|cheque"]),
    ("What does section 7 of the Muslim Family Laws Ordinance require for a talaq?", "en", "family", MFLO, "7", "give the Chairman notice in writing", "The man must give the Chairman written notice of the talaq and a copy to the wife. Unless revoked, it takes effect ninety days after the notice reaches the Chairman.", ["chairman|চেয়ারম্যান", "ninety|90|নব্বই"]),
    ("What does section 5 of the Limitation Act allow?", "en", "other", LIMITATION, "5", "may be admitted after the period of limitation", "An appeal or application may be admitted after the limitation period if the applicant shows sufficient cause for the delay.", ["sufficient cause|পর্যাপ্ত কারণ"]),
    ("What is section 561A of the Code of Criminal Procedure?", "en", "criminal", CRPC, "561A", "inherent power of the High Court Division", "It preserves the High Court Division's inherent power to give effect to orders under the Code, prevent abuse of process and secure the ends of justice.", ["inherent|অন্তর্নিহিত"]),
    ("What does section 11 of the Code of Civil Procedure say?", "en", "other", CPC, "11", "No Court shall try any suit or issue", "No court may try a matter already directly and substantially in issue and decided in a former suit between the same parties. This is res judicata.", ["res judicata|রেস জুডিকাটা|former suit"]),
    ("What does section 9 of the Specific Relief Act allow?", "en", "land", SRA, "9", "by suit recover possession", "A person dispossessed of immovable property without consent and otherwise than by due course of law may recover possession by suit, notwithstanding any other title set up.", ["possession|দখল"]),
]
NFC = lambda s: unicodedata.normalize("NFC", s)  # noqa: E731


def expected_for(need: str, grades: dict[str, dict[str, int]]) -> list[SourceRef]:
    return [SourceRef(kind="judgement", ref=case) for case, grade in sorted(grades.get(need, {}).items()) if grade == 2]


def build(queries, lay_queries, grades, targets) -> list[GoldenItem]:
    rows: list[GoldenItem] = []
    known = 0
    for q in [*queries, *lay_queries]:
        if q.group == "no_answer":
            continue  # adversarial set এ আছে
        if q.group == "known_item":
            known += 1
            if known > KNOWN_ITEM_LIMIT:
                continue
        must_match = "all" if q.need_id in targets else "any"
        lay_note = f"Lay wording, style {q.style}." if q.group == "lay" else ""
        rows.append(GoldenItem(id=f"g-{len(rows) + 1:03d}", question=q.query, language=q.language, category=q.category, type=q.type, expected_sources=expected_for(q.need_id, grades), expected_match=must_match,
                               must_mention=MUST_MENTION.get(q.need_id, []), must_not_claim=COMMON_NOT_CLAIM, notes=lay_note, review_status="needs_reference", source_query_id=q.id))
    for question, language, category, act, section, _quote, reference, mention in STATUTE:
        rows.append(GoldenItem(id=f"g-{len(rows) + 1:03d}", question=NFC(question), language=language, category=category, type="statute-lookup", expected_sources=[SourceRef(kind="law", ref=f"{act}|{section}")], expected_match="all",
                               reference_answer=NFC(reference), must_mention=mention, must_not_claim=COMMON_NOT_CLAIM, notes="Reference drafted from the statute text.", review_status="draft"))
    return rows


def main() -> None:
    queries = load_queries(GOLDEN / "retrieval_queries.jsonl")
    lay = load_queries(GOLDEN / "lay_queries.jsonl")
    qrels = load_qrels(GOLDEN / "retrieval_qrels.jsonl") + load_qrels(GOLDEN / "lay_qrels.jsonl")
    targets = json.loads((GOLDEN / "known_targets.json").read_text(encoding="utf-8"))
    rows = build(queries, lay, grades_by_need(qrels), targets)
    write_jsonl(GOLDEN / "golden_set.jsonl", rows)
    print(len(rows), "items;", "with a reference:", sum(bool(r.reference_answer) for r in rows), "with expected sources:", sum(bool(r.expected_sources) for r in rows))


if __name__ == "__main__":
    main()
