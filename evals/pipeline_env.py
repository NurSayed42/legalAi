"""
evals/pipeline_env.py
=====================
Production API না ছুঁয়ে আসল pipeline চালানোর আলাদা পরিবেশ (D-208)। api_v3.py অপরিবর্তিত থাকে:

  - server বা port নেই: api_v3 module import করে সরাসরি graph চালানো হয়
  - production DB না খুলে তার একটা যাচাই করা কপি (evals/tools/make_eval_db.py); আসল path দিলে থেমে যায়
  - collection এ শুধু read: বাকি সব call এ PermissionError
  - api_v3 এ collection এর নাম hardcoded, তাই এই process এ read-only client নামটা v1/v2/v3 এ ঘুরিয়ে দেয়
  - LLM call শুধু এই process এ বদলায়, যাতে token আর খরচ মাপা যায় বা fake চালানো যায়
"""

import ctypes
import importlib
import os
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_DB = ROOT / "legal_chroma_db"
EVAL_DB = ROOT / "evals" / "cache" / "chroma_eval_copy"
COLLECTIONS = {"v1": "bangladesh_judgements", "v2": "bangladesh_judgements_v2", "v3": "bangladesh_judgements_v3"}
API_JUDGEMENTS_NAME = "bangladesh_judgements"
READ_METHODS = ("get", "query", "count")


class ReadOnlyCollection:
    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        if name in READ_METHODS or name in ("name", "metadata"):
            return getattr(self._inner, name)
        raise PermissionError(f"evaluation is read-only: collection.{name} is blocked")

    def __setattr__(self, name, value):
        raise PermissionError("evaluation is read-only")


class ReadOnlyClient:
    """api_v3 যে নাম চায় তার বদলে পরীক্ষার collection দেয়। অন্য collection (laws) একই থাকে।"""

    def __init__(self, inner, judgements_collection: str):
        self._inner = inner
        self._target = judgements_collection

    def get_collection(self, name: str, embedding_function=None):
        target = self._target if name == API_JUDGEMENTS_NAME else name
        return ReadOnlyCollection(self._inner.get_collection(name=target, embedding_function=embedding_function))

    def __getattr__(self, name):
        raise PermissionError(f"evaluation is read-only: client.{name} is blocked")


def guard_db_path(db_path: Path, production: Path = PRODUCTION_DB) -> Path:
    """Production DB বা তার ভেতরের কিছু হলে থামে, কারণ Chroma খুললেও file এ লিখতে পারে।"""
    resolved = db_path.resolve()
    prod = production.resolve()
    if resolved == prod or prod in resolved.parents:
        raise PermissionError(f"refusing to open the production database: {resolved}")
    if not resolved.exists():
        raise FileNotFoundError(f"{resolved} doesn't exist. Create it with: python evals/tools/make_eval_db.py")
    return resolved


def free_ram_mb() -> int | None:
    if sys.platform != "win32":
        return None

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_avail", ctypes.c_ulonglong),
                    ("virt_total", ctypes.c_ulonglong), ("virt_avail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]

    status = Status()
    status.length = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return int(status.avail / 1024 / 1024)


def peak_memory_mb() -> int | None:
    if sys.platform != "win32":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong), ("peak_ws", ctypes.c_size_t), ("ws", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t), ("c", ctypes.c_size_t),
                    ("d", ctypes.c_size_t), ("e", ctypes.c_size_t), ("f", ctypes.c_size_t)]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return int(counters.peak_ws / 1024 / 1024)


def load_pipeline(collection: str, db_path: Path, llm: Callable | None):
    """api_v3 কে এই process এ load করে। ২-৩ মিনিট লাগে: BM25 তৈরি আর reranker load হয়।"""
    import chromadb

    if collection not in COLLECTIONS:
        raise ValueError(f"unknown collection {collection}; use one of {sorted(COLLECTIONS)}")
    resolved = guard_db_path(db_path)
    # .env এও CHROMA_DB_PATH আছে; load_dotenv আগে থেকে থাকা মান বদলায় না, তাই এটাই চলে। নিচে যাচাই হয়।
    os.environ["CHROMA_DB_PATH"] = str(resolved)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    real_client = chromadb.PersistentClient
    chromadb.PersistentClient = lambda path: ReadOnlyClient(real_client(path=path), COLLECTIONS[collection])
    try:
        api = importlib.import_module("api_v3")
    finally:
        chromadb.PersistentClient = real_client
    if Path(api.CHROMA_DB_PATH).resolve() != resolved:
        raise PermissionError(f"api_v3 opened {api.CHROMA_DB_PATH}, not the evaluation copy")
    if not isinstance(api.judgements_col, ReadOnlyCollection) or not isinstance(api.laws_col, ReadOnlyCollection):
        raise PermissionError("api_v3 isn't using the read-only client")
    if llm is not None:
        api.call_llm = llm
    return api
