"""
evals/tools/human_review.py
===========================
Claude এর দেওয়া grade মানুষ যাচাই করবে (D-202)। Export করে Excel এ খোলা যায় এমন CSV দেয়; `human_grade` পূরণ করে import
করলে qrels এ সেই grade বসে আর `human_verified` সত্য হয়। তারপর evals/retrieval_score.py আবার চালালেই নতুন সংখ্যা।

    python evals/tools/human_review.py export
    python evals/tools/human_review.py import

Grade 0 থেকে 1 বা 2 করলে `human_evidence` এ text_v3 এর হুবহু একটা অংশ দিতে হবে।
"""

import argparse
import csv
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.retrieval_schema import Qrel, load_qrels, load_queries, write_jsonl  # noqa: E402

GOLDEN = ROOT / "evals" / "golden"
QRELS = GOLDEN / "retrieval_qrels.jsonl"
SHEET = GOLDEN / "human_review.csv"
FIELDS = ["need_id", "queries", "case_key", "claude_grade", "evidence", "human_grade", "human_evidence", "human_notes"]


def export() -> None:
    queries: dict[str, list[str]] = {}
    for q in load_queries(GOLDEN / "retrieval_queries.jsonl"):
        queries.setdefault(q.need_id, []).append(q.query)
    rows = sorted(load_qrels(QRELS), key=lambda r: (r.need_id, -r.grade, r.case_key))
    # utf-8-sig: Excel বাংলা ঠিকভাবে দেখায়
    with SHEET.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({"need_id": r.need_id, "queries": " || ".join(queries[r.need_id]), "case_key": r.case_key, "claude_grade": r.grade, "evidence": r.evidence,
                             "human_grade": r.grade if r.human_verified else "", "human_evidence": "", "human_notes": ""})
    print(f"{len(rows)} rows -> {SHEET}")


def apply_rows(qrels: list[Qrel], sheet_rows: list[dict]) -> tuple[list[Qrel], int]:
    by_key = {(r.need_id, r.case_key): r for r in qrels}
    changed = 0
    for row in sheet_rows:
        grade = (row.get("human_grade") or "").strip()
        if not grade:
            continue
        key = (row["need_id"], row["case_key"])
        if key not in by_key:
            raise SystemExit(f"unknown row: {key}")
        old = by_key[key]
        new_grade = int(grade)
        evidence = (row.get("human_evidence") or "").strip() or old.evidence
        if new_grade >= 1 and not evidence:
            raise SystemExit(f"{key}: grade {new_grade} needs human_evidence")
        by_key[key] = Qrel(need_id=old.need_id, case_key=old.case_key, grade=new_grade, evidence=evidence if new_grade >= 1 else "", judged_by=old.judged_by,
                           human_verified=True, notes=(row.get("human_notes") or old.notes))
        changed += 1
    return [by_key[k] for k in sorted(by_key)], changed


def do_import() -> None:
    with SHEET.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    updated, changed = apply_rows(load_qrels(QRELS), rows)
    write_jsonl(QRELS, updated)
    print(f"{changed} rows verified by a person, of {len(updated)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["export", "import"])
    args = ap.parse_args()
    export() if args.command == "export" else do_import()


if __name__ == "__main__":
    main()
