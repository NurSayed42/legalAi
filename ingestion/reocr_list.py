"""
ingestion/reocr_list.py
=======================
কোন file আবার OCR হবে, আর কেন — strict audit এর ফল থেকে (docs/DECISIONS.md D-119)। শুধু পড়ে, OCR চলে না।

  death_ref_58_parts_2_3   D-114 এর জানা line loss; সবসময় তালিকায়
  earlier_24               আগের baseline এ ৮৫% এর নিচের ২৪টা file (data_quality/low_retention_24_old_baseline.txt)
  low_line_retention       নতুন check এ ৯৫% এর নিচে
  corrupted_text_layer     নতুন rule এ এই file এর কোনো page এ OCR বাড়বে (v2 তে নষ্ট text layer রয়ে গেছে)
  corrupted_saved_text     saved text এ নষ্ট বাংলা ধরা পড়েছে, কিন্তু উপরের rule এ না: OCR করা অংশ থেকেই এসেছে

    python ingestion/reocr_list.py --audit data_quality/audit_v2_strict.csv --out data_quality/reocr_list.csv

সময়ের হিসাব v2 এর আসল সময় থেকে: manifest এর seconds (সব worker এর যোগফল), নতুন OCR এর জন্য CONFIG এর মান।
"""

import argparse
import csv
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.pdf_extract import read_manifest  # noqa: E402

CONFIG = {
    "always": ["769454_DeathRef58of2013_2.pdf", "769454_DeathRef58of2013_3.pdf"],
    "earlier_list": ROOT / "data_quality" / "low_retention_24_old_baseline.txt",
    "seconds_per_new_full_page": 3.8,   # এক worker এ, D-106 এর probe
    "seconds_per_new_block": 1.5,       # অনুমান: ছোট clip, পুরো page এর চেয়ে অনেক কম
    "workers": 2,
}


def _int(row: dict, key: str) -> int:
    value = row.get(key, "")
    return int(float(value)) if value not in ("", None) else 0


def _float(row: dict, key: str) -> float | None:
    value = row.get(key, "")
    return float(value) if value not in ("", None) else None


def reasons_for(row: dict, always: set[str], earlier: set[str], min_retention: float) -> list[str]:
    name = row["filename"]
    reasons = []
    if name in always:
        reasons.append("death_ref_58_parts_2_3")
    if name in earlier:
        reasons.append("earlier_24")
    kept = _float(row, "line_retention")
    if kept is not None and kept < min_retention:
        reasons.append("low_line_retention")
    if _int(row, "layer_new_ocr_pages"):
        reasons.append("corrupted_text_layer")
    elif row.get("corrupted_bangla"):
        reasons.append("corrupted_saved_text")
    return reasons


def build(audit_rows: list[dict], manifest: dict, always: set[str], earlier: set[str], min_retention: float) -> list[dict]:
    out = []
    for row in audit_rows:
        reasons = reasons_for(row, always, earlier, min_retention)
        if not reasons:
            continue
        meta = manifest.get(row["filename"], {})
        new_full, new_blocks = _int(row, "layer_new_full_pages"), _int(row, "layer_new_ocr_blocks")
        seconds = float(meta.get("seconds") or 0) + new_full * CONFIG["seconds_per_new_full_page"] + new_blocks * CONFIG["seconds_per_new_block"]
        out.append({
            "filename": row["filename"],
            "reasons": ";".join(reasons),
            "pages": meta.get("pages", ""),
            "ocr_pages_v2": _int(row, "ocr_pages") + _int(row, "no_text_pages"),
            "line_retention": row.get("line_retention", ""),
            "low_retention_pages": row.get("low_retention_pages", ""),
            "layer_new_ocr_pages": _int(row, "layer_new_ocr_pages"),
            "corrupted_bangla": row.get("corrupted_bangla", ""),
            "est_seconds_one_worker": round(seconds, 1),
        })
    return sorted(out, key=lambda r: -r["est_seconds_one_worker"])


def summarize(rows: list[dict]) -> dict:
    by_reason = {}
    for row in rows:
        for reason in row["reasons"].split(";"):
            by_reason[reason] = by_reason.get(reason, 0) + 1
    total = sum(r["est_seconds_one_worker"] for r in rows)
    huge = sum(r["est_seconds_one_worker"] for r in rows if r["filename"].startswith("769454_"))
    return {
        "files": len(rows),
        "pages": sum(int(r["pages"] or 0) for r in rows),
        "ocr_pages_v2": sum(r["ocr_pages_v2"] for r in rows),
        "new_ocr_pages": sum(r["layer_new_ocr_pages"] for r in rows),
        "files_by_reason": by_reason,
        "est_hours_at_workers": {"workers": CONFIG["workers"], "all": round(total / CONFIG["workers"] / 3600, 2),
                                 "death_ref_58_only": round(huge / CONFIG["workers"] / 3600, 2),
                                 "others": round((total - huge) / CONFIG["workers"] / 3600, 2)},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default=str(ROOT / "data_quality" / "audit_v2_strict.csv"))
    ap.add_argument("--text-dir", default=str(ROOT / "text_v2"))
    ap.add_argument("--out", default=str(ROOT / "data_quality" / "reocr_list.csv"))
    ap.add_argument("--min-retention", type=float, default=None)
    args = ap.parse_args()

    from ingestion.quality_audit import RETENTION_CHECK
    min_retention = RETENTION_CHECK["min_line_retention"] if args.min_retention is None else args.min_retention
    with open(args.audit, encoding="utf-8", newline="") as fh:
        audit_rows = list(csv.DictReader(fh))
    earlier = set(Path(CONFIG["earlier_list"]).read_text(encoding="utf-8").split("\n")) - {""}
    rows = build(audit_rows, read_manifest(Path(args.text_dir)), set(CONFIG["always"]), earlier, min_retention)
    out = Path(args.out)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize(rows)
    out.with_name(out.stem + "_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
