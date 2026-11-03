"""
eval/run_eval.py
================
/api/chat এ ২০টা Bangladeshi legal প্রশ্ন পাঠিয়ে retrieval আর citation quality log করে।

    python evals/run_eval.py --label before              # server :8000 এ চালু থাকতে হবে
    python evals/run_eval.py --label after
    python evals/run_eval.py --compare before after      # দুই run পাশাপাশি

Result যায় evals/reports/<label>.json এ।
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API = "http://localhost:8000"
RESULTS_DIR = Path(__file__).parent / "reports"

QUESTIONS = [
    "চেক ডিসঅনার হলে মামলা করার প্রক্রিয়া কী?",
    "খুনের মামলায় জামিন পাওয়া যায় কি?",
    "Section 302",
    "তালাক দেওয়ার প্রক্রিয়া কী?",
    "chek dishonour hole ki korbo?",
    "What is the punishment for cheque dishonour under the NI Act?",
    "আমার বাবা মারা যাওয়ার পর চাচা জমি দখল করে রেখেছে, আইনে কী প্রতিকার আছে?",
    "jomi dokhol hoye gele ki mamla korbo?",
    "How do I file a writ petition against an unlawful government order?",
    "স্ত্রী কি তালাকের পর দেনমোহর ও ভরণপোষণ পাবে?",
    "Can an accused get anticipatory bail in Bangladesh?",
    "ধারা ৪২০ কী বলে?",
    "Section 145 CrPC",
    "Article 102 of the Constitution",
    "My employer dismissed me without notice. What can I do?",
    "ভাড়াটিয়াকে উচ্ছেদ করার আইনি প্রক্রিয়া কী?",
    "Is a verbal agreement to sell land valid?",
    "Find High Court cases on dishonour of cheque where the conviction was set aside",
    "আমার বিরুদ্ধে মিথ্যা মামলা হয়েছে, আমি কি জিতবো?",
    "What is the limitation period to file a civil suit for recovery of possession?",
]

# মানুষের অভিজ্ঞতা দাবি — persona leak ধরার জন্য (api_v3 এর check এর সাথে স্বাধীনভাবে)
PERSONA_RE = re.compile(
    r"(\d+|[০-৯]+|দীর্ঘ)\s*বছরের\s*(অভিজ্ঞতা|প্র্যা?কটিস|কর্মজীবন)|প্র্যা?কটিসে\s*দেখেছি|"
    r"in my \d+ years|as a senior (advocate|lawyer)|I have (handled|argued)",
    re.IGNORECASE,
)


def _answer_text(answer) -> str:
    return json.dumps(answer, ensure_ascii=False) if answer else ""


def run_one(question: str) -> dict:
    t0 = time.time()
    try:
        r = requests.post(f"{API}/api/chat", json={"question": question}, timeout=300)
        status = r.status_code
        data = r.json()
    except Exception as e:
        return {"question": question, "status": "error", "error": str(e), "latency_s": round(time.time() - t0, 1)}

    answer = data.get("answer") or {}
    sources = data.get("sources") or {}
    return {
        "question": question,
        "status": status,
        "error": data.get("detail"),
        "latency_s": round(time.time() - t0, 1),
        "intent": data.get("intent"),
        "retrieved_laws": [f"{l.get('title')} | {l.get('section')}" for l in sources.get("laws") or []],
        "retrieved_cases": [c.get("case_no") for c in sources.get("cases") or []],
        "applicable_laws_count": len(answer.get("applicable_laws") or []),
        "applicable_laws": [f"{l.get('act')} | {l.get('section')}" for l in answer.get("applicable_laws") or []
                            if isinstance(l, dict)],
        "relevant_cases_count": len(answer.get("relevant_cases") or []),
        "citation_verified": data.get("citation_verified"),
        "unverified_citations": len(data.get("unverified_citations") or []),
        "retrieval_attempts": data.get("retrieval_attempts"),
        "retrieval_sufficient": data.get("retrieval_sufficient"),
        "persona_claim": bool(PERSONA_RE.search(_answer_text(answer))),
        "answer_preview": (answer.get("short_answer") or answer.get("summary") or "")[:200],
    }


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("status") == 200]
    n = len(ok) or 1
    return {
        "questions": len(rows),
        "http_200": len(ok),
        "with_retrieved_cases": sum(1 for r in ok if r["retrieved_cases"]),
        "with_retrieved_laws": sum(1 for r in ok if r["retrieved_laws"]),
        "avg_applicable_laws": round(sum(r["applicable_laws_count"] for r in ok) / n, 2),
        "with_relevant_cases_in_answer": sum(1 for r in ok if r["relevant_cases_count"]),
        "citation_verified_true": sum(1 for r in ok if r["citation_verified"] is True),
        "citation_verified_false": sum(1 for r in ok if r["citation_verified"] is False),
        "citation_verified_null": sum(1 for r in ok if r["citation_verified"] is None),
        "retrieval_attempts_null_or_0": sum(1 for r in ok if not r["retrieval_attempts"]),
        "retrieval_insufficient": sum(1 for r in ok if r["retrieval_sufficient"] is False),
        "persona_claims": sum(1 for r in ok if r["persona_claim"]),
        "avg_latency_s": round(sum(r["latency_s"] for r in ok) / n, 1),
    }


def print_rows(rows: list[dict]):
    for i, r in enumerate(rows, 1):
        print(f"\n[{i:02d}] {r['question']}")
        if r.get("status") != 200:
            print(f"     ERROR {r.get('status')}: {r.get('error')}")
            continue
        print(f"     intent={r['intent']}  verified={r['citation_verified']}  unverified={r['unverified_citations']}  "
              f"attempts={r['retrieval_attempts']}  sufficient={r['retrieval_sufficient']}  "
              f"persona={r['persona_claim']}  {r['latency_s']}s")
        print(f"     retrieved laws ({len(r['retrieved_laws'])}): {'; '.join(r['retrieved_laws'][:3])}")
        print(f"     retrieved cases ({len(r['retrieved_cases'])}): {'; '.join(map(str, r['retrieved_cases'][:3]))}")
        print(f"     applicable_laws ({r['applicable_laws_count']}): {'; '.join(r['applicable_laws'])}")
        print(f"     relevant_cases in answer: {r['relevant_cases_count']}")


def compare(a_label: str, b_label: str):
    a = json.loads((RESULTS_DIR / f"{a_label}.json").read_text(encoding="utf-8"))
    b = json.loads((RESULTS_DIR / f"{b_label}.json").read_text(encoding="utf-8"))
    print(f"{'metric':32} {a_label:>10} {b_label:>10}")
    for k in a["summary"]:
        print(f"{k:32} {str(a['summary'][k]):>10} {str(b['summary'][k]):>10}")
    print(f"\n{'#':3} {'question':48} {'cases':>11} {'app_laws':>9} {'verified':>13} {'attempts':>9}")
    for i, (ra, rb) in enumerate(zip(a["rows"], b["rows"]), 1):
        def g(r, k):
            return r.get(k) if r.get("status") == 200 else "ERR"
        print(f"{i:<3} {ra['question'][:48]:48} "
              f"{str(len(ra.get('retrieved_cases') or [])) + '→' + str(len(rb.get('retrieved_cases') or [])):>11} "
              f"{str(g(ra, 'applicable_laws_count')) + '→' + str(g(rb, 'applicable_laws_count')):>9} "
              f"{str(g(ra, 'citation_verified')) + '→' + str(g(rb, 'citation_verified')):>13} "
              f"{str(g(ra, 'retrieval_attempts')) + '→' + str(g(rb, 'retrieval_attempts')):>9}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", help="এই run এর নাম, যেমন before / after")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()

    if args.compare:
        compare(*args.compare)
        return
    if not args.label:
        ap.error("--label বা --compare দাও")

    rows = []
    for i, q in enumerate(QUESTIONS, 1):
        print(f"  {i:02d}/{len(QUESTIONS)} {q[:60]}", flush=True)
        rows.append(run_one(q))

    summary = summarize(rows)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.label}.json"
    out.write_text(json.dumps({"label": args.label, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print_rows(rows)
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k:32} {v}")
    print(f"\nsaved → {out}")


if __name__ == "__main__":
    main()
