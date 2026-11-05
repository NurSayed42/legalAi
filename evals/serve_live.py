"""
evals/serve_live.py
===================
আসল UI (legal_ui_v2.html) আর আসল pipeline এক port এ দেখার জন্য, production না ছুঁয়ে:

  - production DB খোলে না: যাচাই করা কপি, read-only (evals/pipeline_env.py)
  - port 8000 নেয় না, আর শুধু এই computer থেকে দেখা যায় (127.0.0.1)
  - legal_ui_v2.html আর api_v3.py বদলায় না: API এর ঠিকানা বদলায় শুধু browser এ পাঠানো কপিতে
  - LLM call আসল: কেউ প্রশ্ন করলে তবেই হয়, প্রশ্ন প্রতি প্রায় ২-৩ টা। Search আর stats এ LLM লাগে না।

    python evals/serve_live.py                   # v1, production এর একই data, http://localhost:8001
    python evals/serve_live.py --collection v3   # শুধু দেখার জন্য v3, কোনো migration নয়
"""

import argparse
import html as html_module
import socket
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UI_FILE = ROOT / "legal_ui_v2.html"
API_LINE = 'const API = "http://localhost:8000";'
SAME_ORIGIN_LINE = 'const API = "";'
HOST = "127.0.0.1"
DEFAULT_PORT = 8001
PRODUCTION_PORT = 8000
# Header এর ফাঁকা জায়গায়, যাতে প্রশ্নের ঘর বা অন্য কিছু না ঢাকে। লম্বা ব্যাখ্যা title এ।
BANNER = ('<div title="Not the production API. Questions make real LLM calls." style="position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:9999;'
          'padding:3px 12px;border-radius:999px;font:12px system-ui,sans-serif;background:#123;color:#cde;white-space:nowrap">Local test copy ({collection}), not production</div>')


def served_ui(source: str, collection: str) -> str:
    """Browser এ পাঠানোর কপি। UI এর ঠিকানার লাইন বদলে গেলে চুপচাপ ভুল server এ না গিয়ে থামে।"""
    if source.count(API_LINE) != 1:
        raise ValueError(f"expected exactly one {API_LINE!r} in the UI file")
    page = source.replace(API_LINE, SAME_ORIGIN_LINE)
    if page.count("</body>") != 1:
        raise ValueError("expected exactly one </body> in the UI file")
    return page.replace("</body>", BANNER.format(collection=html_module.escape(collection)) + "</body>")


def check_port(port: int) -> None:
    if port == PRODUCTION_PORT:
        raise SystemExit(f"port {PRODUCTION_PORT} belongs to the production API, so this won't use it")
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex((HOST, port)) == 0:
            raise SystemExit(f"something is already listening on port {port}")


def build_app(api_app, page: str, info: dict):
    """/ হলো UI, /api/* আসল pipeline। কোনটা production নয় তা /live-info বলে।"""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    outer = FastAPI(title="Bangladesh Legal AI, local test instance", docs_url=None, redoc_url=None, openapi_url=None)

    @outer.get("/", response_class=HTMLResponse)
    def ui() -> str:
        return page

    @outer.get("/live-info")
    def live_info() -> dict:
        return info

    outer.mount("/", api_app)
    return outer


def main() -> None:
    from evals.pipeline_env import COLLECTIONS, EVAL_DB, load_pipeline

    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", choices=sorted(COLLECTIONS), default="v1", help="v1 is what production uses")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default=str(EVAL_DB))
    args = ap.parse_args()
    check_port(args.port)
    page = served_ui(UI_FILE.read_text(encoding="utf-8"), args.collection)

    print("Loading the pipeline, about 2-3 minutes...", flush=True)
    api = load_pipeline(args.collection, Path(args.db), llm=None)  # llm=None: api_v3 এর নিজের আসল LLM call
    info = {"instance": "local test", "production_api": False, "collection": args.collection, "chroma_collection": COLLECTIONS[args.collection], "database": "evaluation copy, read-only", "chunks": api.judgements_col.count(), "llm": "real, only when asked"}
    app = build_app(api.app, page, info)

    import uvicorn

    print(f"READY http://localhost:{args.port}", flush=True)
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
