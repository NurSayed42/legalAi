"""
ingestion/run_phase1.py
=======================
Phase 1 পুরোটা এক command এ। যতবার খুশি চালানো যায় — যা হয়ে গেছে তা আবার হয় না:

  1. PDF যাচাই; না-থাকা গুলো scraper.py দিয়ে download
  2. pdf_extract.py (দরকারে কয়েকবার), ব্যর্থগুলো একবার আবার চেষ্টা
  3. GATE: সব PDF এর যাচাই করা output না থাকলে এখানেই থামে — অসম্পূর্ণ text থেকে index হয় না
  4. text_v2 এর audit
  5. bangladesh_judgements_v2 build: আগে v1 এর fingerprint আর পুরো DB এর backup,
     পরে fingerprint মিলিয়ে দেখা v1 অপরিবর্তিত। v2 আগে থেকেই থাকলে build হয় না।

    python ingestion/run_phase1.py --workers 2

API server বন্ধ রেখে চালাতে হবে। শেষে log এ PHASE1_DONE অথবা PHASE1_STOPPED: <কারণ> লেখা থাকে।
"""

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.pdf_extract import acquire_lock, inventory, keep_awake, log  # noqa: E402

PY = sys.executable
BASE_URL = "https://supremecourt.gov.bd/resources/documents/"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
PDF_DIR = ROOT / "downloaded_pdfs"
TEXT_DIR = ROOT / "text_v2"
FAILURES = ROOT / "data_quality" / "failures.jsonl"
BACKUPS = ROOT / "data_quality" / "backups"
CHROMA_DIR = ROOT / os.getenv("CHROMA_DB_PATH", "legal_chroma_db")
CHROMA_BACKUP = ROOT / "legal_chroma_db_backup_pre_v2"
CHROMA_BASELINE = BACKUPS / "chroma_pre_v2.json"
TEXT_BASELINE = BACKUPS / "text_v2_pre_resume.json"
V2_COLLECTION = "bangladesh_judgements_v2"
EXTRACT_ATTEMPTS = 3


def run(args: list[str]) -> int:
    log("$ " + " ".join(args))
    code = subprocess.run([PY, *args], cwd=ROOT, env=ENV).returncode
    if code:
        log(f"   → exit code {code}")
    return code


def expected_files() -> list[str]:
    links = ROOT / "pdf_links.txt"
    if not links.exists():
        names = [j["filename"] for j in json.loads((ROOT / "judgements.json").read_text(encoding="utf-8"))]
        links.write_text("".join(BASE_URL + n + "\n" for n in names), encoding="utf-8")
    return [line.strip().rsplit("/", 1)[-1] for line in links.read_text(encoding="utf-8").splitlines() if line.strip()]


def extraction_gate(expected: list[str], state: dict, allow_failed: bool = False) -> tuple[bool, str]:
    """Audit/index এর আগে শর্ত: প্রতিটা expected PDF এর যাচাই করা output আছে। Progress counter না, disk দেখে।"""
    valid = set(state["valid"])
    missing = [n for n in expected if n not in valid]
    failed = [n for n in missing if n in set(state["failed"])]
    pending = [n for n in missing if n not in set(state["failed"])]
    if state["invalid"]:
        return False, f"{len(state['invalid'])} অবৈধ output: {list(state['invalid'])[:3]}"
    if pending:
        return False, f"{len(pending)} PDF এর output নেই: {pending[:3]}"
    if failed and not allow_failed:
        return False, f"{len(failed)} PDF ব্যর্থ (--allow-failed ছাড়া index হবে না): {failed[:3]}"
    return True, f"{len(valid)}/{len(expected)} যাচাই করা output, {len(failed)} ব্যর্থ"


def collection_count(name: str) -> int | None:
    db = CHROMA_DIR / "chroma.sqlite3"
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("select id from collections where name = ?", (name,)).fetchone()
        if row is None:
            return None
        segment = con.execute("select id from segments where collection = ? and scope = 'METADATA'", (row[0],)).fetchone()[0]
        return con.execute("select count(*) from embeddings where segment_id = ?", (segment,)).fetchone()[0]
    finally:
        con.close()


def api_is_running() -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", 8000)) == 0


def backup_chroma() -> None:
    """পুরো DB এর copy। .partial এ copy করে শেষে rename, তাই অর্ধেক backup কখনো 'সম্পূর্ণ' দেখায় না।"""
    if CHROMA_BACKUP.exists():
        log(f"backup আগে থেকেই আছে: {CHROMA_BACKUP.name}")
        return
    partial = CHROMA_BACKUP.with_name(CHROMA_BACKUP.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    log(f"Chroma DB backup → {CHROMA_BACKUP.name}")
    shutil.copytree(CHROMA_DIR, partial)
    os.replace(partial, CHROMA_BACKUP)


def stop(reason: str, code: int = 2) -> int:
    log(f"PHASE1_STOPPED: {reason}")
    return code


def run_extraction(workers: int) -> None:
    extract = ["ingestion/pdf_extract.py", "--workers", str(workers), "--settle-seconds", "0"]
    for attempt in range(1, EXTRACT_ATTEMPTS + 1):
        code = run(extract)
        state = inventory(PDF_DIR, TEXT_DIR, FAILURES)
        pending = [n for n in state["todo"] if n not in set(state["failed"])]
        if code == 3:
            return
        if not pending and not state["invalid"]:
            break
        log(f"attempt {attempt}: এখনো {len(pending)} বাকি")
        time.sleep(60)
    if inventory(PDF_DIR, TEXT_DIR, FAILURES)["failed"]:
        log("আগে ব্যর্থ file গুলো একবার আবার চেষ্টা")
        run([*extract, "--retry-failed"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--allow-failed", action="store_true", help="কিছু PDF ব্যর্থ হলেও index বানাও (বাদ পড়া file report এ থাকবে)")
    ap.add_argument("--skip-index", action="store_true")
    args = ap.parse_args(argv)

    lock = acquire_lock(ROOT / "data_quality" / "phase1.lock")
    if lock is None:
        return stop("আরেকটা run_phase1 চলছে", 3)
    keep_awake()

    expected = expected_files()
    missing = [n for n in expected if not (PDF_DIR / n).exists()]
    log(f"PDF আছে {len(expected) - len(missing)}/{len(expected)}")
    if missing:
        if run(["scraper.py"]):
            return stop("download ব্যর্থ")
        missing = [n for n in expected if not (PDF_DIR / n).exists()]
        if missing:
            return stop(f"{len(missing)} PDF download হয়নি: {missing[:3]}")

    run_extraction(args.workers)

    state = inventory(PDF_DIR, TEXT_DIR, FAILURES)
    ok, why = extraction_gate(expected, state, args.allow_failed)
    log(f"gate: {why}")
    if not ok:
        return stop(why)

    if run(["ingestion/quality_audit.py", "--source", "text_v2", "--out", "data_quality/audit_v2.csv"]):
        return stop("audit ব্যর্থ")
    if args.skip_index:
        log("PHASE1_DONE (index বাদ)")
        return 0

    existing = collection_count(V2_COLLECTION)
    if existing:
        log(f"{V2_COLLECTION} আগে থেকেই আছে ({existing} chunk) — build হবে না")
    else:
        if api_is_running():
            return stop("port 8000 এ API চলছে — বন্ধ করে আবার চালান")
        if not CHROMA_BASELINE.exists() and run(["ingestion/integrity.py", "snapshot-chroma", "--out", str(CHROMA_BASELINE)]):
            return stop("v1 fingerprint নেওয়া গেল না")
        backup_chroma()
        if run(["enrich_judgements.py", "--text", "v2", "--labels", "clean",
                "--collection", V2_COLLECTION, "--skip-tests"]):
            return stop("v2 build ব্যর্থ — আবার চালালে __building থেকে চলবে")
        log(f"{V2_COLLECTION}: {collection_count(V2_COLLECTION)} chunk")

    if run(["ingestion/integrity.py", "verify-chroma", "--against", str(CHROMA_BASELINE)]):
        return stop(f"ALERT: v1/laws fingerprint মেলেনি — backup আছে {CHROMA_BACKUP.name} এ", 4)
    if TEXT_BASELINE.exists() and run(["ingestion/integrity.py", "verify-text", "--against", str(TEXT_BASELINE)]):
        return stop("ALERT: আগের text_v2 output বদলে গেছে", 4)
    log("PHASE1_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
