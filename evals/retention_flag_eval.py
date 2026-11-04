"""
evals/retention_flag_eval.py
============================
Retention flag কি সত্যিই হারানো text এর চিহ্ন? (docs/DECISIONS.md D-118) Flag হওয়া আর না-হওয়া file এর sample
নিয়ে page গুলো আসল extractor দিয়ে memory তে আবার চালায়, সংরক্ষিত text এর সাথে শব্দ মেলায়। লেখে শুধু --out।

    python evals/retention_flag_eval.py --audit data_quality/audit_v2_strict.csv --out data_quality/retention_flag_eval.json

"সত্যি" মানে আবার OCR এ শব্দ বাড়ে: কমপক্ষে gain_min_words টা, আর সংরক্ষিত শব্দের gain_min_share ভাগ।
সতর্কতা: নতুন OCR ও নিখুঁত না, Latin junk এও শব্দ বাড়ে; তবে এটা আসল চালানোর মতোই একই মাপ।
"""

import argparse
import collections
import csv
import json
import random
import sys
import time
from pathlib import Path

import pymupdf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.pdf_extract import _init_worker, extract_page_pymupdf  # noqa: E402

CONFIG = {
    "seed": 2026,
    "files_per_stratum": 25,
    "pages_per_file": 2,
    "unflagged_files": 40,
    "gain_min_words": 8,
    "gain_min_share": 0.05,
    "max_minutes": 25,
}
HUGE_PREFIX = "769454_"


def _shortfall(row: dict) -> int:
    return int(row["pdf_lines"]) - int(row["kept_lines"])


def stratum(row: dict) -> str:
    n = _shortfall(row)
    return "1-2 lines" if n <= 2 else "3-9 lines" if n < 10 else "10-99 lines" if n < 100 else "100+ lines"


def gained(saved: str, new: str) -> bool:
    a, b = len(saved.split()), len(new.split())
    return b - a >= max(CONFIG["gain_min_words"], CONFIG["gain_min_share"] * max(1, a))


def measure_page(doc, name: str, page_no: int, parts: list[str]) -> dict:
    new, method, _ = extract_page_pymupdf(doc[page_no])
    saved = parts[page_no]
    return {"page": page_no, "method": method, "saved_words": len(saved.split()), "new_words": len(new.split()), "gained": gained(saved, new)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default=str(ROOT / "data_quality" / "audit_v2_strict.csv"))
    ap.add_argument("--pages", default=None)
    ap.add_argument("--pdf-dir", default=str(ROOT / "downloaded_pdfs"))
    ap.add_argument("--text-dir", default=str(ROOT / "text_v2"))
    ap.add_argument("--out", default=str(ROOT / "data_quality" / "retention_flag_eval.json"))
    args = ap.parse_args()

    from ingestion.quality_audit import RETENTION_CHECK
    limit = RETENTION_CHECK["min_line_retention"]
    pages_csv = Path(args.pages) if args.pages else Path(args.audit).with_name(Path(args.audit).stem + "_pages.csv")
    rows = list(csv.DictReader(open(args.audit, encoding="utf-8")))
    low_pages = collections.defaultdict(list)
    all_pages = collections.defaultdict(list)
    with open(pages_csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            all_pages[r["filename"]].append(int(r["page"]))
            if float(r["line_retention"]) < limit:
                low_pages[r["filename"]].append(int(r["page"]))

    measured = [r for r in rows if r.get("line_retention") not in ("", None) and r.get("retention_level") == "page"]
    flagged = [r for r in measured if float(r["line_retention"]) < limit and not r["filename"].startswith(HUGE_PREFIX)]
    unflagged = [r for r in measured if float(r["line_retention"]) >= limit and not r["filename"].startswith(HUGE_PREFIX)]
    rng = random.Random(CONFIG["seed"])
    strata = collections.defaultdict(list)
    for r in flagged:
        strata[stratum(r)].append(r)

    _init_worker()
    started = time.time()
    budget = CONFIG["max_minutes"] * 60
    results = {"flagged": {}, "unflagged": {}}

    def run_file(row: dict, candidates: list[int]) -> dict | None:
        name = row["filename"]
        parts = (Path(args.text_dir) / (Path(name).stem + ".txt")).read_text(encoding="utf-8").split("\f")
        doc = pymupdf.open(Path(args.pdf_dir) / name)
        try:
            picks = rng.sample(candidates, min(CONFIG["pages_per_file"], len(candidates)))
            pages = [measure_page(doc, name, p, parts) for p in picks]
        finally:
            doc.close()
        return {"file": name, "retention": float(row["line_retention"]), "shortfall": _shortfall(row),
                "any_gain": any(p["gained"] for p in pages), "pages": pages}

    for name, group in sorted(strata.items()):
        rng.shuffle(group)
        done = []
        for row in group[:CONFIG["files_per_stratum"]]:
            if time.time() - started > budget:
                break
            if low_pages.get(row["filename"]):
                done.append(run_file(row, low_pages[row["filename"]]))
        results["flagged"][name] = {"files_in_stratum": len(group), "sampled": len(done),
                                    "true": sum(x["any_gain"] for x in done),
                                    "precision": round(sum(x["any_gain"] for x in done) / len(done), 3) if done else None, "files": done}
        print(name, {k: v for k, v in results["flagged"][name].items() if k != "files"}, f"{time.time() - started:.0f}s", flush=True)

    rng.shuffle(unflagged)
    done = []
    for row in unflagged[:CONFIG["unflagged_files"]]:
        if time.time() - started > budget:
            break
        if all_pages.get(row["filename"]):
            done.append(run_file(row, all_pages[row["filename"]]))
    results["unflagged"] = {"files_total": len(unflagged), "sampled": len(done), "with_gain": sum(x["any_gain"] for x in done), "files": done}
    print("unflagged", {k: v for k, v in results["unflagged"].items() if k != "files"}, f"{time.time() - started:.0f}s", flush=True)

    def estimate() -> dict:
        true_est = sum(v["files_in_stratum"] * (v["precision"] or 0) for v in results["flagged"].values())
        return {"flagged_files_excluding_death_ref": len(flagged), "estimated_true": round(true_est),
                "estimated_precision": round(true_est / max(1, len(flagged)), 3)}

    results["overall"] = estimate()
    results["config"] = CONFIG
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(results["overall"])


if __name__ == "__main__":
    main()
