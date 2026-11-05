"""
evals/tools/make_eval_db.py
===========================
Pipeline পরীক্ষার জন্য production DB এর কপি (D-208)। আসল DB শুধু পড়া হয়। কপি শেষে exact hash মিলিয়ে দেখে, না মিললে থামে।
কপি gitignored, প্রায় ৩.৮ GB, পরে মুছে ফেলা নিরাপদ। API চলাকালীন চালাবে না: খোলা DB এর কপি অসম্পূর্ণ হতে পারে।
আসল DB কে Chroma দিয়ে খোলা হয় না, শুধু file হিসেবে পড়া হয়; hash যাচাই হয় কপির উপর।

    python evals/tools/make_eval_db.py
"""

import argparse
import shutil
import socket
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.pipeline_env import EVAL_DB, PRODUCTION_DB  # noqa: E402
from ingestion.integrity import verify_chroma  # noqa: E402

BASELINE = ROOT / "data_quality" / "backups" / "chroma_phase2_baseline_exact.json"


def port_in_use(port: int = 8000) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(EVAL_DB))
    args = ap.parse_args()
    out = Path(args.out).resolve()
    if out == PRODUCTION_DB.resolve() or PRODUCTION_DB.resolve() in out.parents:
        raise SystemExit("refusing to write inside the production database")
    if port_in_use():
        raise SystemExit("something is listening on port 8000, probably the API. A copy of an open database may be incomplete, so stopping.")
    if out.exists():
        print(f"{out} already exists, verifying it")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".partial")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(PRODUCTION_DB, tmp)
        tmp.rename(out)
        print(f"copied to {out}")
    if not BASELINE.exists():  # gitignored, তাই নতুন clone এ থাকে না
        print(f"copy made, but not verified: {BASELINE.name} doesn't exist on this machine")
        return
    report = verify_chroma(out, BASELINE)
    if not report.get("ok"):
        raise SystemExit(f"the copy doesn't match the baseline: {report}")
    print("copy verified: all collections match the Phase 2 baseline by exact hash")


if __name__ == "__main__":
    main()
