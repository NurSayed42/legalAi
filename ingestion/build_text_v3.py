"""
ingestion/build_text_v3.py
==========================
text_v3/ বানায়: পুরো corpus, যেখানে data_quality/reocr_list.csv এর file গুলো নতুন extractor দিয়ে আবার OCR করা, বাকিগুলো
text_v2 থেকে byte ধরে হুবহু copy। text_v2/ শুধু পড়া হয়। (docs/DECISIONS.md D-119)

    python ingestion/build_text_v3.py status          # read-only
    python ingestion/build_text_v3.py reocr --workers 2 [--retry-failed]
    python ingestion/build_text_v3.py copy

_manifest.jsonl এর প্রতিটা row এ source_version: "v3_reocr" (reocr_reasons সহ) অথবা "v2" (sha256 সহ)। দুটো ধাপই যতবার
খুশি চালানো যায়: pdf_extract এর মতোই যাচাই করা output skip হয়, মাঝপথে বন্ধ হলে checkpoint থেকে চলে, ব্যর্থতা
data_quality/failures_v3.jsonl এ।
"""

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.pdf_extract import (  # noqa: E402
    MANIFEST_NAME,
    acquire_lock,
    append_jsonl,
    inventory,
    keep_awake,
    log,
    process_all,
    read_manifest,
    repair_jsonl_tail,
)

PATHS = {
    "reocr_list": ROOT / "data_quality" / "reocr_list.csv",
    "pdf_dir": ROOT / "downloaded_pdfs",
    "source_dir": ROOT / "text_v2",
    "out_dir": ROOT / "text_v3",
    "parts_dir": ROOT / "text_v3_parts",
    "failures": ROOT / "data_quality" / "failures_v3.jsonl",
    "quarantine": ROOT / "data_quality" / "quarantine_v3",
    "lock": ROOT / "data_quality" / "extract_v3.lock",
}
SOURCE_REOCR = "v3_reocr"
SOURCE_COPY = "v2"


def read_list(path: Path | None = None) -> dict[str, str]:
    with open(path or PATHS["reocr_list"], encoding="utf-8", newline="") as fh:
        return {row["filename"]: row["reasons"] for row in csv.DictReader(fh)}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Byte ধরে লেখে: text mode এ newline বদলে গেলে আর 'হুবহু copy' থাকে না।"""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def copy_unlisted(source_dir: Path, out_dir: Path, listed: set[str]) -> dict:
    """তালিকার বাইরের সব file হুবহু copy। ঠিক থাকা copy আবার লেখে না, তালিকার file কখনো ছোঁয় না।"""
    if source_dir.resolve() == out_dir.resolve():
        raise ValueError("source and output folders must differ")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_NAME
    repair_jsonl_tail(manifest_path)
    done = read_manifest(out_dir)
    counts = {"copied": 0, "already_ok": 0, "skipped_listed": 0}
    for name, row in sorted(read_manifest(source_dir).items()):
        if name in listed:
            counts["skipped_listed"] += 1
            continue
        stem = Path(name).stem
        data = (source_dir / f"{stem}.txt").read_bytes()
        digest = _sha256(data)
        target = out_dir / f"{stem}.txt"
        previous = done.get(name)
        if (previous and previous.get("source_version") == SOURCE_COPY and previous.get("sha256") == digest
                and target.exists() and _sha256(target.read_bytes()) == digest):
            counts["already_ok"] += 1
            continue
        _atomic_write_bytes(target, data)
        append_jsonl(manifest_path, {**row, "source_version": SOURCE_COPY, "sha256": digest})
        counts["copied"] += 1
    return counts


def reocr(workers: int, retry_failed: bool = False) -> dict:
    listed = read_list()
    return process_all(PATHS["pdf_dir"], PATHS["out_dir"], PATHS["parts_dir"], PATHS["failures"], PATHS["quarantine"],
                       workers=workers, retry_failed=retry_failed, only=set(listed),
                       manifest_extra=lambda name: {"source_version": SOURCE_REOCR, "reocr_reasons": listed[name]})


def status() -> dict:
    listed = read_list()
    state = inventory(PATHS["pdf_dir"], PATHS["out_dir"], PATHS["failures"], only=set(listed))
    manifest = read_manifest(PATHS["out_dir"]) if PATHS["out_dir"].exists() else {}
    sources: dict[str, int] = {}
    for row in manifest.values():
        sources[row.get("source_version", "?")] = sources.get(row.get("source_version", "?"), 0) + 1
    return {"listed": len(listed), "reocr_valid": len(state["valid"]), "reocr_remaining": len(state["todo"]),
            "reocr_invalid": state["invalid"], "reocr_failed": state["failed"], "manifest_by_source": sources,
            "text_files": len(list(PATHS["out_dir"].glob("*.txt"))) if PATHS["out_dir"].exists() else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["status", "reocr", "copy"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()

    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=1))
        return 0
    lock = acquire_lock(PATHS["lock"])
    if lock is None:
        log("আরেকটা text_v3 run চলছে — এটা বন্ধ হলো")
        return 3
    keep_awake()
    if args.command == "reocr":
        reocr(args.workers, args.retry_failed)
    else:
        counts = copy_unlisted(PATHS["source_dir"], PATHS["out_dir"], set(read_list()))
        log(f"text_v2 → text_v3: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
