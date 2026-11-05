"""
evals/compare_v2_v3.py
======================
v2 আর v3 পাশাপাশি: audit bucket, লাইন ধরে রাখা, chunk সংখ্যা, আর top-10 এ একটা মামলা কতটা নেয়। শুধু আগে থেকে তৈরি file পড়ে।

    python evals/compare_v2_v3.py --out evals/reports/v2_v3_comparison.json

লাইনের হিসাব একই page এ মেলানো হয় (v2 যে page গুলো OCR করেছিল), কারণ v3 এ নতুন page ও OCR হয়; সেগুলো আলাদা গোনা।
"""

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DQ = ROOT / "data_quality"
THRESHOLDS = (0.95, 0.9, 0.85, 0.7)
HUGE = "769454_DeathRef58of2013_"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def buckets(rows: list[dict], key: str) -> dict:
    counts = Counter(r[key] for r in rows)
    return {b: {"count": counts[b], "percent": round(counts[b] / len(rows) * 100, 1)} for b in ("good", "suspect", "broken")}


def file_retention(rows: list[dict]) -> dict:
    measured = [r for r in rows if r["line_retention"] != ""]
    return {
        "files_measured": len(measured),
        "files_below": {str(t): sum(float(r["line_retention"]) < t for r in measured) for t in THRESHOLDS},
        "death_ref": {r["filename"].removeprefix(HUGE)[:-4]: float(r["line_retention"]) for r in measured if r["filename"].startswith(HUGE)},
    }


def page_key(row: dict) -> tuple[str, int]:
    return row["filename"], int(row["page"])


def same_pages(v2_pages: list[dict], v3_pages: list[dict], listed: set[str]) -> dict:
    """একই page এ v2 আর v3। v3 এ page টা না থাকলে (ছড়ানো \\f এর file) বাদ।"""
    v3 = {page_key(r): r for r in v3_pages}
    groups: dict[str, list[int]] = {}

    def add(group: str, pdf: int, a: int, b: int) -> None:
        row = groups.setdefault(group, [0, 0, 0, 0, 0, 0])
        row[0] += 1
        row[1] += pdf
        row[2] += min(pdf, a)
        row[3] += min(pdf, b)
        row[4] += min(pdf, a) / pdf < 0.95 if pdf else 0
        row[5] += min(pdf, b) / pdf < 0.95 if pdf else 0

    for r in v2_pages:
        other = v3.get(page_key(r))
        if other is None:
            continue
        pdf, a, b = int(r["pdf_lines"]), int(r["output_lines"]), int(other["output_lines"])
        add("all pages v2 OCRed", pdf, a, b)
        if r["filename"] in (HUGE + "2.pdf", HUGE + "3.pdf"):
            add("Death Ref parts 2-3", pdf, a, b)
        elif r["filename"] in listed:
            add("other re-OCRed files", pdf, a, b)
        else:
            add("files copied from v2", pdf, a, b)
    return {name: {"pages": v[0], "pdf_lines": v[1], "v2_lines_kept": v[2], "v3_lines_kept": v[3],
                   "v2_percent": round(v[2] / max(1, v[1]) * 100, 2), "v3_percent": round(v[3] / max(1, v[1]) * 100, 2),
                   "v2_pages_below_95": v[4], "v3_pages_below_95": v[5]} for name, v in groups.items()}


def new_ocr_pages(v2_pages: list[dict], v3_pages: list[dict]) -> dict:
    v2 = {page_key(r) for r in v2_pages}
    rows = [r for r in v3_pages if page_key(r) not in v2]
    pdf = sum(int(r["pdf_lines"]) for r in rows)
    kept = sum(min(int(r["pdf_lines"]), int(r["output_lines"])) for r in rows)
    return {"pages": len(rows), "pdf_lines": pdf, "kept": kept, "percent": round(kept / max(1, pdf) * 100, 2)}


def chunk_counts(db: Path, names: list[str]) -> dict:
    con = sqlite3.connect(f"file:{db / 'chroma.sqlite3'}?mode=ro", uri=True)
    out = {}
    try:
        for name in names:
            row = con.execute("select id from collections where name = ?", (name,)).fetchone()
            if row is None:
                out[name] = None
                continue
            segment = con.execute("select id from segments where collection = ? and scope = 'METADATA'", (row[0],)).fetchone()[0]
            out[name] = con.execute("select count(*) from embeddings where segment_id = ?", (segment,)).fetchone()[0]
    finally:
        con.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "legal_chroma_db"))
    ap.add_argument("--retrieval", default=str(ROOT / "evals" / "reports" / "retrieval_v2_v3.json"))
    ap.add_argument("--out", default=str(ROOT / "evals" / "reports" / "v2_v3_comparison.json"))
    args = ap.parse_args()

    a2, a3 = read_csv(DQ / "audit_v2_strict.csv"), read_csv(DQ / "audit_v3_strict.csv")
    p2, p3 = read_csv(DQ / "audit_v2_strict_pages.csv"), read_csv(DQ / "audit_v3_strict_pages.csv")
    listed = {r["filename"] for r in read_csv(DQ / "reocr_list.csv")}
    s2 = json.loads((DQ / "audit_v2_strict_summary.json").read_text(encoding="utf-8"))
    s3 = json.loads((DQ / "audit_v3_strict_summary.json").read_text(encoding="utf-8"))
    report = {
        "audit": {
            "strict": {"v2": buckets(a2, "bucket"), "v3": buckets(a3, "bucket")},
            "original_rules": {"v2": buckets(a2, "bucket_old"), "v3": buckets(a3, "bucket_old")},
            "reasons": {"v2": s2["reasons"], "v3": s3["reasons"]},
            "corrupted_saved_files": {"v2": s2["corrupted_bangla_files"], "v3": s3["corrupted_bangla_files"]},
            "words": {"v2": sum(int(r["words"]) for r in a2), "v3": sum(int(r["words"]) for r in a3)},
            "bangla_tokens": {"v2": sum(int(r["bangla_tokens"]) for r in a2), "v3": sum(int(r["bangla_tokens"]) for r in a3)},
        },
        "line_retention": {
            "files": {"v2": file_retention(a2), "v3": file_retention(a3)},
            "same_pages": same_pages(p2, p3, listed),
            "pages_only_v3_ocrs": new_ocr_pages(p2, p3),
        },
        "chunks": {"in_collections": chunk_counts(Path(args.db), ["bangladesh_judgements", "bangladesh_judgements_v2", "bangladesh_judgements_v3", "bangladesh_laws"])},
    }
    plan_path = DQ / "v3_index_plan.json"
    if plan_path.exists():
        report["chunks"]["v3_plan"] = json.loads(plan_path.read_text(encoding="utf-8"))
    retrieval_path = Path(args.retrieval)
    if retrieval_path.exists():
        report["retrieval"] = json.loads(retrieval_path.read_text(encoding="utf-8"))["totals"]
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
