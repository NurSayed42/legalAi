"""
ingestion/run_phase1.py
=======================
Phase 1 পুরোটা এক command এ। প্রতিটা ধাপ resumable — মাঝপথে বন্ধ হলে আবার চালালেই হয়:

  1. judgements.json এর filename থেকে pdf_links.txt (v1 এর একই 8,615 file — v1/v2 তুলনীয় থাকে)
  2. scraper.py দিয়ে download (আগে নামানো file skip, scraper এর নিজের ০.৫s বিরতি) — পটভূমিতে
  3. download চলাকালীন বারবার pdf_extract.py — যা নামানো হয়েছে তার extraction
  4. download শেষে শেষবার pdf_extract, না-নামা file failures.jsonl এ, তারপর text_v2 এর audit
  5. enrich_judgements.py --text v2 → bangladesh_judgements_v2 (v1 collection ছোঁয় না)

    python ingestion/run_phase1.py --workers 3

API server বন্ধ রেখে চালাতে হবে — শেষ ধাপ Chroma তে লেখে, আর server RAM ধরে রাখে।
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
BASE_URL = "https://supremecourt.gov.bd/resources/documents/"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def log(msg: str):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def run(args: list[str]) -> int:
    log("$ " + " ".join(args))
    return subprocess.run([PY, *args], cwd=ROOT, env=ENV).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--extract-interval", type=int, default=300, help="download চলাকালীন extraction pass এর বিরতি (s)")
    ap.add_argument("--skip-index", action="store_true")
    args = ap.parse_args()

    filenames = [j["filename"] for j in json.loads((ROOT / "judgements.json").read_text(encoding="utf-8"))]
    links = ROOT / "pdf_links.txt"
    if not links.exists():
        links.write_text("".join(BASE_URL + f + "\n" for f in filenames), encoding="utf-8")
        log(f"pdf_links.txt: {len(filenames)} link")

    pdf_dir = ROOT / "downloaded_pdfs"
    have = sum(1 for f in filenames if (pdf_dir / f).exists())
    log(f"PDF আছে {have}/{len(filenames)} — download শুরু")
    download = subprocess.Popen([PY, "scraper.py"], cwd=ROOT, env=ENV)

    extract = ["ingestion/pdf_extract.py", "--workers", str(args.workers)]
    while download.poll() is None:
        run(extract)
        for _ in range(args.extract_interval):
            if download.poll() is not None:
                break
            time.sleep(1)
    log(f"download শেষ (exit {download.returncode})")
    run([*extract, "--settle-seconds", "0"])

    missing = [f for f in filenames if not (pdf_dir / f).exists()]
    if missing:
        with open(ROOT / "data_quality" / "failures.jsonl", "a", encoding="utf-8") as fh:
            for f in missing:
                fh.write(json.dumps({"stage": "download", "filename": f, "ok": False}) + "\n")
    text_dir = ROOT / "text_v2"
    extracted = sum(1 for f in filenames if (text_dir / (f[:-4] + ".txt")).exists())
    log(f"download হয়নি {len(missing)}, extracted {extracted}/{len(filenames)}")

    run(["ingestion/quality_audit.py", "--source", "text_v2", "--out", "data_quality/audit_v2.csv"])
    if not args.skip_index:
        run(["enrich_judgements.py", "--text", "v2", "--labels", "clean",
             "--collection", "bangladesh_judgements_v2", "--skip-tests"])
    log("Phase 1 run শেষ")


if __name__ == "__main__":
    main()
