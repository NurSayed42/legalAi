"""
ingestion/run_v3.py
===================
text_v3 আর bangladesh_judgements_v3 এক command এ। যতবার খুশি চালানো যায়; যা হয়ে গেছে তা আবার হয় না:

  1. আগের অবস্থার প্রমাণ: text_v2 এর snapshot, v1/v2/laws এর exact vector
  2. তালিকার file আবার OCR (দরকারে কয়েকবার, ব্যর্থগুলো একবার আবার চেষ্টা)
  3. বাকি file text_v2 থেকে হুবহু copy
  4. GATE: সব PDF এর যাচাই করা output না থাকলে এখানেই থামে; অসম্পূর্ণ text থেকে audit বা index হয় না
  5. text_v3 এর strict audit
  6. DB backup, তারপর bangladesh_judgements_v3 build (আগে থেকে থাকলে হয় না)
  7. verify_v3.py: output, page, chunk, আর v1/v2/laws/text_v2 অপরিবর্তিত

    python ingestion/run_v3.py --workers 2

API server বন্ধ রেখে চালাতে হবে। log এর শেষে V3_DONE অথবা V3_STOPPED: <কারণ>।
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.build_text_v3 import PATHS, read_list  # noqa: E402
from ingestion.pdf_extract import acquire_lock, inventory, keep_awake, log  # noqa: E402
from ingestion.run_phase1 import (  # noqa: E402
    ENV,
    PY,
    api_is_running,
    backup_chroma,
    collection_count,
    expected_files,
    extraction_gate,
)
from ingestion.verify_v3 import CHROMA_BASELINE, TEXT_BASELINE, V3_COLLECTION  # noqa: E402

OTHER_COLLECTIONS = ["bangladesh_judgements", "bangladesh_judgements_v2", "bangladesh_laws"]
CHROMA_BACKUP = ROOT / "legal_chroma_db_backup_pre_v3"
AUDIT_OUT = "data_quality/audit_v3_strict.csv"
EXTRACT_ATTEMPTS = 3


def run(args: list[str]) -> int:
    log("$ " + " ".join(args))
    code = subprocess.run([PY, *args], cwd=ROOT, env=ENV).returncode
    if code:
        log(f"   → exit code {code}")
    return code


def stop(reason: str, code: int = 2) -> int:
    log(f"V3_STOPPED: {reason}")
    return code


def take_baselines() -> str | None:
    """Build এর আগে প্রমাণ রাখা। আগে থেকে থাকলে নতুন নেয় না, নইলে মাঝপথের অবস্থাই baseline হয়ে যেত।"""
    if not TEXT_BASELINE.exists() and run(["ingestion/integrity.py", "snapshot-text", "--text-dir", "text_v2", "--out", str(TEXT_BASELINE)]):
        return "text_v2 এর snapshot নেওয়া গেল না"
    if not CHROMA_BASELINE.exists() and run(["ingestion/integrity.py", "snapshot-chroma", "--out", str(CHROMA_BASELINE), "--collections", *OTHER_COLLECTIONS]):
        return "collection এর fingerprint নেওয়া গেল না"
    return None


def run_reocr(workers: int) -> None:
    command = ["ingestion/build_text_v3.py", "reocr", "--workers", str(workers)]
    only = set(read_list())
    for attempt in range(1, EXTRACT_ATTEMPTS + 1):
        code = run(command)
        state = inventory(PATHS["pdf_dir"], PATHS["out_dir"], PATHS["failures"], only=only)
        pending = [n for n in state["todo"] if n not in set(state["failed"])]
        if code == 3:
            return
        if not pending and not state["invalid"]:
            break
        log(f"attempt {attempt}: এখনো {len(pending)} বাকি")
        time.sleep(60)
    if inventory(PATHS["pdf_dir"], PATHS["out_dir"], PATHS["failures"], only=only)["failed"]:
        log("আগে ব্যর্থ file গুলো একবার আবার চেষ্টা")
        run([*command, "--retry-failed"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--skip-index", action="store_true")
    args = ap.parse_args(argv)

    lock = acquire_lock(ROOT / "data_quality" / "run_v3.lock")
    if lock is None:
        return stop("আরেকটা run_v3 চলছে", 3)
    keep_awake()
    if api_is_running():
        return stop("port 8000 এ API চলছে — বন্ধ করে আবার চালান")

    problem = take_baselines()
    if problem:
        return stop(problem)

    run_reocr(args.workers)
    if run(["ingestion/build_text_v3.py", "copy"]):
        return stop("text_v2 থেকে copy ব্যর্থ")

    expected = expected_files()
    ok, why = extraction_gate(expected, inventory(PATHS["pdf_dir"], PATHS["out_dir"], PATHS["failures"]))
    log(f"gate: {why}")
    if not ok:
        return stop(why)

    if run(["ingestion/quality_audit.py", "--source", "text_v3", "--pdf-dir", "downloaded_pdfs",
            "--measure-new-ocr-pages", "--out", AUDIT_OUT]):
        return stop("audit ব্যর্থ")
    if args.skip_index:
        log("V3_DONE (index বাদ)")
        return 0

    existing = collection_count(V3_COLLECTION)
    if existing:
        log(f"{V3_COLLECTION} আগে থেকেই আছে ({existing} chunk) — build হবে না")
    else:
        if api_is_running():
            return stop("port 8000 এ API চলছে — বন্ধ করে আবার চালান")
        backup_chroma(CHROMA_BACKUP)
        if run(["enrich_judgements.py", "--text", "v3", "--labels", "clean", "--collection", V3_COLLECTION, "--skip-tests"]):
            return stop("v3 build ব্যর্থ — আবার চালালে __building থেকে চলবে")
        log(f"{V3_COLLECTION}: {collection_count(V3_COLLECTION)} chunk")

    if run(["ingestion/verify_v3.py"]):
        return stop(f"ALERT: যাচাই ব্যর্থ, data_quality/v3_verification.json দেখুন — backup আছে {CHROMA_BACKUP.name} এ", 4)
    log("V3_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
