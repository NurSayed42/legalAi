"""
evals/retrieval_smoke.py
========================
Judgements collection গুলোতে একই প্রশ্ন, শুধু vector search (API, BM25, reranker বা LLM ছাড়া)। Read-only।

মূল প্রশ্ন: top-10 এর কতটা একটাই document বা মামলা নিয়ে নেয়। প্রথম সংস্করণে ৫টা বাংলা প্রশ্ন ছিল (প্রথম ৫টা); আরও
৫টা যোগ হয়েছে v3 এর ফল দেখার আগে, যাতে বাংলার পরীক্ষা ১০ প্রশ্নের হয় (docs/DECISIONS.md D-120)।

    python evals/retrieval_smoke.py --collections bangladesh_judgements_v2 bangladesh_judgements_v3 --out evals/reports/retrieval_v2_v3.json
"""

import argparse
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.chunk_cap import case_key  # noqa: E402
from ingestion.quality_audit import bangla_problem, bangla_signals  # noqa: E402

EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
QUERIES_EN = [
    "dishonour of cheque Negotiable Instruments Act section 138 conviction",
    "bail in a murder case section 302 Penal Code",
    "writ petition government service dismissal Article 102",
    "suit for recovery of possession of land Specific Relief Act section 9",
    "dower and maintenance after talaq Muslim Family Laws Ordinance",
]
QUERIES_BN = [
    "চেক ডিসঅনার মামলায় দণ্ডের বিরুদ্ধে রিভিশন",
    "খুনের মামলায় আসামির জামিন আবেদন",
    "জমি দখল নিয়ে দেওয়ানী মামলা",
    "নারী ও শিশু নির্যাতন দমন আইনে জামিন",
    "পিলখানা বিদ্রোহ বিডিআর হত্যাকাণ্ড",
    "তালাকের পর দেনমোহর ও ভরণপোষণের দাবি",
    "সরকারি চাকরি থেকে বরখাস্তের বিরুদ্ধে রিট আবেদন",
    "মাদকদ্রব্য নিয়ন্ত্রণ আইনে আসামির সাজা",
    "ভূমি অধিগ্রহণের ক্ষতিপূরণ নিয়ে বিরোধ",
    "অর্থ ঋণ আদালতে খেলাপি ঋণ আদায়ের মামলা",
]
LEGACY = re.compile(r"[¡¢£¤¥¦§«¬®¯°±²³µ¶·¸¹º»¼½¾¿ÑÐ−‡†ˆ‰Š‹ŒÖ¨]")
BN = re.compile("[\\u0980-\\u09ff]")
CASE_TO_WATCH = "death_reference_58_of_2013"
# প্রতিটি প্রশ্নের মূল শব্দ, প্রাসঙ্গিকতার মোটা হিসাব। শব্দ থাকলেই ফলটা প্রাসঙ্গিক নয়, কিন্তু না থাকলে প্রায় নিশ্চিত অপ্রাসঙ্গিক।
# v3 এর ফল দেখার পরে লেখা (D-122), তাই এটা v2 আর v3 তুলনার জন্য, প্রমাণ হিসেবে নয়।
QUERY_TERMS = {
    "চেক ডিসঅনার মামলায় দণ্ডের বিরুদ্ধে রিভিশন": ["চেক", "ডিসঅনার"],
    "খুনের মামলায় আসামির জামিন আবেদন": ["জামিন"],
    "জমি দখল নিয়ে দেওয়ানী মামলা": ["দখল"],
    "নারী ও শিশু নির্যাতন দমন আইনে জামিন": ["নারী ও শিশু"],
    "পিলখানা বিদ্রোহ বিডিআর হত্যাকাণ্ড": ["পিলখানা", "বিডিআর"],
    "তালাকের পর দেনমোহর ও ভরণপোষণের দাবি": ["দেনমোহর", "ভরণপোষণ"],
    "সরকারি চাকরি থেকে বরখাস্তের বিরুদ্ধে রিট আবেদন": ["বরখাস্ত"],
    "মাদকদ্রব্য নিয়ন্ত্রণ আইনে আসামির সাজা": ["মাদক"],
    "ভূমি অধিগ্রহণের ক্ষতিপূরণ নিয়ে বিরোধ": ["অধিগ্রহণ"],
    "অর্থ ঋণ আদালতে খেলাপি ঋণ আদায়ের মামলা": ["অর্থ ঋণ", "খেলাপি"],
    "dishonour of cheque Negotiable Instruments Act section 138 conviction": ["cheque"],
    "bail in a murder case section 302 Penal Code": ["bail"],
    "writ petition government service dismissal Article 102": ["dismiss", "removed from service"],
    "suit for recovery of possession of land Specific Relief Act section 9": ["possession"],
    "dower and maintenance after talaq Muslim Family Laws Ordinance": ["dower", "talaq", "maintenance"],
}


def term_hits(docs: list[str], terms: list[str]) -> int:
    """কয়টা ফলে প্রশ্নের মূল শব্দ আছে।"""
    wanted = [unicodedata.normalize("NFC", t).casefold() for t in terms]
    return sum(any(t in unicodedata.normalize("NFC", d).casefold() for t in wanted) for d in docs)


def score_results(docs: list[str], filenames: list[str], terms: list[str] | None = None) -> dict:
    """এক প্রশ্নের top-k এর হিসাব। মামলা গোনা হয় case_key দিয়ে, তাই Death Ref 58 এর সব PDF মিলে একটা।"""
    by_doc = collections.Counter(filenames)
    by_case = collections.Counter(case_key(f) for f in filenames)
    readable = sum(len(LEGACY.findall(d)) / (sum(c.isalpha() for c in d) or 1) < 0.02 for d in docs)
    corrupted = sum(bool(bangla_problem(bangla_signals(d), 10)) for d in docs)
    return {
        "max_single_document": by_doc.most_common(1)[0][1] if by_doc else 0,
        "max_single_case": by_case.most_common(1)[0][1] if by_case else 0,
        "top_case": by_case.most_common(1)[0][0] if by_case else "",
        "distinct_documents": len(by_doc),
        "distinct_texts": len({" ".join(d.split()) for d in docs}),
        "term_hits": term_hits(docs, terms or []),
        "death_ref_58_chunks": by_case.get(CASE_TO_WATCH, 0),
        "readable": readable,
        "corrupted_bangla": corrupted,
        "with_bangla_text": sum(len(BN.findall(d)) > 50 for d in docs),
    }


def totals(rows: list[dict]) -> dict:
    n = len(rows) or 1
    return {
        "queries": len(rows),
        "death_ref_58_slots": sum(r["death_ref_58_chunks"] for r in rows),
        "worst_single_case": max((r["max_single_case"] for r in rows), default=0),
        "mean_max_single_case": round(sum(r["max_single_case"] for r in rows) / n, 2),
        "distinct_documents": sum(r["distinct_documents"] for r in rows),
        "distinct_texts": sum(r["distinct_texts"] for r in rows),
        "term_hits": sum(r["term_hits"] for r in rows),
        "readable": sum(r["readable"] for r in rows),
        "corrupted_bangla": sum(r["corrupted_bangla"] for r in rows),
    }


def main() -> None:
    import chromadb
    from chromadb.utils import embedding_functions

    ap = argparse.ArgumentParser()
    ap.add_argument("--collections", nargs="+", default=["bangladesh_judgements", "bangladesh_judgements_v2"])
    ap.add_argument("--db", default=str(ROOT / "evals" / "cache" / "chroma_eval_copy"), help="production DB নয়: Chroma খুললেই file বদলায় (D-208)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--snippets", type=int, default=3, help="প্রতি প্রশ্নে কয়টা ফল ছাপা হবে")
    args = ap.parse_args()

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    client = chromadb.PersistentClient(path=args.db)
    cols = {name: client.get_collection(name, embedding_function=ef) for name in args.collections}
    report = {"collections": args.collections, "queries": {}, "totals": {}}
    rows = collections.defaultdict(lambda: collections.defaultdict(list))
    for lang, queries in (("bangla", QUERIES_BN), ("english", QUERIES_EN)):
        for q in queries:
            print(f"\n### {q}")
            report["queries"][q] = {}
            for name, col in cols.items():
                r = col.query(query_texts=[q], n_results=10, include=["documents", "metadatas", "distances"])
                docs, metas, dists = r["documents"][0], r["metadatas"][0], r["distances"][0]
                row = score_results(docs, [m["filename"] for m in metas], QUERY_TERMS[q])
                row["best_similarity"] = round(1 - dists[0], 3)
                report["queries"][q][name] = row
                rows[name][lang].append(row)
                print(f" {name}: most from one case {row['max_single_case']}/10 ({row['top_case'][:34]}) | distinct docs "
                      f"{row['distinct_documents']} | Death Ref 58 {row['death_ref_58_chunks']}/10 | term in {row['term_hits']}/10 | corrupted {row['corrupted_bangla']}/10 | best {row['best_similarity']}")
                for m, d, dist in list(zip(metas, docs, dists))[:args.snippets]:
                    print(f"     {1 - dist:.3f} {m['filename'][:36]:36} {' '.join(d.split())[:110]}")
    report["totals"] = {name: {lang: totals(v) for lang, v in by_lang.items()} for name, by_lang in rows.items()}
    print("\n=== totals, top 10 each")
    print(json.dumps(report["totals"], ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
