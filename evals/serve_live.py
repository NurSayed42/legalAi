"""
evals/serve_live.py
===================
আসল UI আর আসল pipeline এক port এ দেখার জন্য, production না ছুঁয়ে:

  - production DB খোলে না: যাচাই করা কপি, read-only (evals/pipeline_env.py)
  - port 8000 নেয় না, আর শুধু এই computer থেকে দেখা যায় (127.0.0.1)
  - legal_ui_v2.html আর api_v3.py বদলায় না
  - frontend/dist থাকলে নতুন UI দেখায় (D-212), না থাকলে বা --ui legacy দিলে পুরনো page
  - LLM call আসল: কেউ প্রশ্ন করলে তবেই হয়, প্রশ্ন প্রতি প্রায় ২-৩ টা। Search আর stats এ LLM লাগে না।

    python evals/serve_live.py                   # v1, production এর একই data, http://localhost:8001
    python evals/serve_live.py --collection v3   # শুধু দেখার জন্য v3, কোনো migration নয়
    python evals/serve_live.py --ui legacy       # পুরনো legal_ui_v2.html
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
DIST_DIR = ROOT / "frontend" / "dist"
NO_CACHE = {"Cache-Control": "no-cache"}
IMMUTABLE = "public, max-age=31536000, immutable"
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


def served_build(index_html: str, collection: str) -> str:
    """নতুন UI: এই tag দেখে header এ "পরীক্ষার কপি" দেখায়। Production build এ tag নেই, তাই সেখানে দেখায় না।"""
    if index_html.count("</head>") != 1:
        raise ValueError("expected exactly one </head> in the built index.html")
    meta = f'<meta name="legalai-instance" content="{html_module.escape(collection, quote=True)}">'
    return index_html.replace("</head>", meta + "</head>")


def check_port(port: int) -> None:
    if port == PRODUCTION_PORT:
        raise SystemExit(f"port {PRODUCTION_PORT} belongs to the production API, so this won't use it")
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex((HOST, port)) == 0:
            raise SystemExit(f"something is already listening on port {port}")


class SpaFallback:
    """অচেনা page এর ঠিকানায় (API ছাড়া) index.html। Pure ASGI, যাতে লম্বা API request এর পথে কিছু না বসে।"""

    def __init__(self, app, page: str):
        self.app = app
        self.page = page

    async def __call__(self, scope, receive, send):
        wants_page = (
            scope["type"] == "http"
            and scope["method"] == "GET"
            and not scope["path"].startswith("/api")
            and b"text/html" in dict(scope["headers"]).get(b"accept", b"")
        )
        if not wants_page:
            await self.app(scope, receive, send)
            return
        replaced = False

        async def guarded(message):
            nonlocal replaced
            if message["type"] == "http.response.start" and message["status"] == 404:
                replaced = True
            if not replaced:
                await send(message)

        await self.app(scope, receive, guarded)
        if replaced:
            from fastapi.responses import HTMLResponse

            await HTMLResponse(self.page, headers=NO_CACHE)(scope, receive, send)


def mount_build(outer, dist: Path) -> None:
    """Hash করা file এক বছর cache, বাকিগুলো (sw.js, manifest) প্রতিবার যাচাই। শুধু এগুলোই gzip, API নয়।"""
    from fastapi.responses import FileResponse
    from starlette.middleware.gzip import GZipMiddleware
    from starlette.staticfiles import StaticFiles

    class ImmutableFiles(StaticFiles):
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if response.status_code == 200:
                response.headers["Cache-Control"] = IMMUTABLE
            return response

    outer.mount("/assets", GZipMiddleware(ImmutableFiles(directory=dist / "assets"), minimum_size=512))
    if (dist / "icons").is_dir():
        outer.mount("/icons", StaticFiles(directory=dist / "icons"))
    media = {".webmanifest": "application/manifest+json", ".js": "text/javascript"}
    for file in sorted(dist.iterdir()):
        if file.is_file() and file.name != "index.html":
            outer.add_api_route(
                f"/{file.name}",
                lambda f=file: FileResponse(f, media_type=media.get(f.suffix), headers=NO_CACHE),
                include_in_schema=False,
            )


def build_app(api_app, page: str, info: dict, dist: Path | None = None):
    """/ হলো UI, /api/* আসল pipeline। কোনটা production নয় তা /live-info বলে।"""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    outer = FastAPI(title="Bangladesh Legal AI, local test instance", docs_url=None, redoc_url=None, openapi_url=None)

    @outer.get("/", response_class=HTMLResponse)
    def ui() -> HTMLResponse:
        return HTMLResponse(page, headers=NO_CACHE)

    @outer.get("/live-info")
    def live_info() -> dict:
        return info

    if dist is not None:
        mount_build(outer, dist)
        outer.add_middleware(SpaFallback, page=page)
    outer.mount("/", api_app)
    return outer


def main() -> None:
    from evals.pipeline_env import COLLECTIONS, EVAL_DB, load_pipeline

    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", choices=sorted(COLLECTIONS), default="v1", help="v1 is what production uses")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default=str(EVAL_DB))
    ap.add_argument("--ui", choices=["auto", "new", "legacy"], default="auto", help="auto: frontend/dist if built, else legal_ui_v2.html")
    args = ap.parse_args()
    check_port(args.port)
    built = (DIST_DIR / "index.html").exists()
    if args.ui == "new" and not built:
        raise SystemExit("frontend/dist isn't built: run npm run build in frontend/")
    dist = DIST_DIR if built and args.ui != "legacy" else None
    if dist:
        page = served_build((dist / "index.html").read_text(encoding="utf-8"), args.collection)
    else:
        page = served_ui(UI_FILE.read_text(encoding="utf-8"), args.collection)

    print("Loading the pipeline, about 2-3 minutes...", flush=True)
    api = load_pipeline(args.collection, Path(args.db), llm=None)  # llm=None: api_v3 এর নিজের আসল LLM call
    info = {"instance": "local test", "production_api": False, "collection": args.collection, "chroma_collection": COLLECTIONS[args.collection], "database": "evaluation copy, read-only", "chunks": api.judgements_col.count(), "llm": "real, only when asked", "ui": "frontend/dist" if dist else UI_FILE.name}
    app = build_app(api.app, page, info, dist)

    import uvicorn

    print(f"READY http://localhost:{args.port}", flush=True)
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
