"""
ingestion/verify_v3.py
======================
text_v3 আর bangladesh_judgements_v3 ঠিক আছে কিনা প্রমাণ করে (SPEC Phase 1, docs/DECISIONS.md D-119)। শুধু পড়ে:

  - output সংখ্যা: প্রতিটা PDF এর একটা যাচাই করা .txt, বাড়তি কিছু নেই
  - page সংখ্যা: PDF থেকে নতুন করে গোনা, manifest এর সাথে
  - উৎস: তালিকার file v3_reocr, বাকিগুলো text_v2 এর সাথে byte ধরে হুবহু
  - chunk: text থেকে নতুন করে হিসাব করা id বনাম collection এর id, আর একটা sample এর লেখা
  - v1, v2, laws এর exact vector আর text_v2 আগের snapshot এর সাথে

    python ingestion/verify_v3.py --out data_quality/v3_verification.json

কোনো check ব্যর্থ হলে exit code 1।
"""

import argparse
import hashlib
import json
import random
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.build_text_v3 import PATHS, SOURCE_COPY, SOURCE_REOCR, read_list  # noqa: E402
from ingestion.chunk_cap import CAP_CONFIG, plan_index  # noqa: E402
from ingestion.pdf_extract import PAGE_SEP, output_is_valid, page_count, read_manifest  # noqa: E402

V3_COLLECTION = "bangladesh_judgements_v3"
BACKUPS = ROOT / "data_quality" / "backups"
CHROMA_BASELINE = BACKUPS / "chroma_pre_v3_exact.json"
TEXT_BASELINE = BACKUPS / "text_v2_pre_v3.json"
SAMPLE_DOCS = 300


def verify_outputs(out_dir: Path, pdf_dir: Path, source_dir: Path, listed: set[str]) -> dict:
    manifest = read_manifest(out_dir)
    pdfs = {p.name: p for p in pdf_dir.glob("*.pdf")}
    texts = {p.stem + ".pdf" for p in out_dir.glob("*.txt")}
    report = {
        "pdfs": len(pdfs), "text_files": len(texts), "manifest_rows": len(manifest),
        "missing_outputs": sorted(set(pdfs) - texts)[:20], "extra_outputs": sorted(texts - set(pdfs))[:20],
        "invalid": {}, "page_count_mismatch": {}, "wrong_source": {}, "copy_differs_from_v2": [],
        "by_source": {}, "failed_pages": 0, "page_split_differs": {SOURCE_COPY: 0, SOURCE_REOCR: 0},
    }
    for name, pdf in sorted(pdfs.items()):
        row = manifest.get(name)
        target = out_dir / f"{pdf.stem}.txt"
        ok, why = output_is_valid(target, row)
        if not ok:
            report["invalid"][name] = why
            continue
        source = row.get("source_version")
        report["by_source"][source] = report["by_source"].get(source, 0) + 1
        expected = SOURCE_REOCR if name in listed else SOURCE_COPY
        if source != expected:
            report["wrong_source"][name] = f"{source}, expected {expected}"
        n_pages = page_count(pdf)
        if row.get("pages") != n_pages:
            report["page_count_mismatch"][name] = {"pdf": n_pages, "manifest": row.get("pages")}
        data = target.read_bytes()
        report["failed_pages"] += len(row.get("failed_pages") or [])
        # ছড়ানো \f শুধু পুরনো v2 output এ থাকতে পারে (D-111); নতুন extraction এ থাকলে সেটা bug
        if data.decode("utf-8").count(PAGE_SEP) + 1 != n_pages and source in report["page_split_differs"]:
            report["page_split_differs"][source] += 1
        if source == SOURCE_COPY:
            original = source_dir / f"{pdf.stem}.txt"
            if not original.exists() or hashlib.sha256(data).hexdigest() != hashlib.sha256(original.read_bytes()).hexdigest():
                report["copy_differs_from_v2"].append(name)
    report["ok"] = not (report["missing_outputs"] or report["extra_outputs"] or report["invalid"]
                        or report["page_count_mismatch"] or report["wrong_source"] or report["copy_differs_from_v2"]
                        or report["page_split_differs"][SOURCE_REOCR] or report["failed_pages"])
    return report


def expected_chunks(out_dir: Path, chunker, cap: int | None) -> tuple[dict, dict[str, str]]:
    """text থেকে নতুন করে: কোন chunk id থাকার কথা। লেখাও ফেরত দেয় sample মেলানোর জন্য।"""
    counts, texts = {}, {}
    for path in sorted(out_dir.glob("*.txt")):
        name = path.stem + ".pdf"
        texts[name] = path.read_text(encoding="utf-8")
        counts[name] = len(chunker(texts[name]))
    return plan_index(counts, cap), texts


def compare_ids(plan: dict, actual_ids: set[str]) -> dict:
    expected = {f"{name}__chunk_{i}" for name, kept in plan["keep"].items() for i in kept}
    missing, extra = expected - actual_ids, actual_ids - expected
    return {
        "chunks_from_text": plan["chunks_from_text"], "chunks_expected_after_cap": plan["chunks_indexed"],
        "chunks_in_collection": len(actual_ids), "missing": sorted(missing)[:20], "extra": sorted(extra)[:20],
        "capped_cases": plan["capped_cases"], "max_chunks_per_case": plan["max_chunks_per_case"],
        "ok": not missing and not extra,
    }


def collection_ids(db: Path, name: str) -> set[str] | None:
    con = sqlite3.connect(f"file:{db / 'chroma.sqlite3'}?mode=ro", uri=True)
    try:
        row = con.execute("select id from collections where name = ?", (name,)).fetchone()
        if row is None:
            return None
        segment = con.execute("select id from segments where collection = ? and scope = 'METADATA'", (row[0],)).fetchone()[0]
        return {r[0] for r in con.execute("select embedding_id from embeddings where segment_id = ?", (segment,))}
    finally:
        con.close()


def verify_sample_documents(db: Path, name: str, ids: set[str], texts: dict[str, str], chunker) -> dict:
    """id মিললেও লেখা ভুল হতে পারে: একটা sample chunk এর লেখা text থেকে নতুন করে বানিয়ে মেলানো।"""
    import chromadb
    col = chromadb.PersistentClient(path=str(db)).get_collection(name)
    random.seed(120)
    sample = random.sample(sorted(ids), min(SAMPLE_DOCS, len(ids)))
    got = col.get(ids=sample, include=["documents", "metadatas"])
    differs = []
    cache: dict[str, list] = {}
    for doc_id, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
        filename, index = doc_id.rsplit("__chunk_", 1)
        chunks = cache.setdefault(filename, chunker(texts[filename]))
        if chunks[int(index)][1] != doc or meta.get("filename") != filename:
            differs.append(doc_id)
    return {"sampled": len(got["ids"]), "differs": differs[:20], "ok": not differs}


def verify_v3(db: Path) -> dict:
    from enrich_judgements import chunk_text
    from ingestion import integrity
    report = {"outputs": verify_outputs(PATHS["out_dir"], PATHS["pdf_dir"], PATHS["source_dir"], set(read_list()))}
    plan, texts = expected_chunks(PATHS["out_dir"], chunk_text, CAP_CONFIG["max_chunks_per_case"])
    ids = collection_ids(db, V3_COLLECTION)
    if ids is None:
        report["chunks"] = {"ok": False, "error": f"{V3_COLLECTION} does not exist"}
    else:
        report["chunks"] = compare_ids(plan, ids)
        report["sample_documents"] = verify_sample_documents(db, V3_COLLECTION, ids, texts, chunk_text)
    report["no_leftover_build"] = collection_ids(db, f"{V3_COLLECTION}__building") is None
    report["other_collections"] = integrity.verify_chroma(db, CHROMA_BASELINE)
    report["text_v2"] = integrity.verify_text(PATHS["source_dir"], TEXT_BASELINE)
    report["ok"] = all(v.get("ok", True) if isinstance(v, dict) else bool(v) for v in report.values())
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "legal_chroma_db"))
    ap.add_argument("--out", default=str(ROOT / "data_quality" / "v3_verification.json"))
    args = ap.parse_args()
    report = verify_v3(Path(args.db))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
