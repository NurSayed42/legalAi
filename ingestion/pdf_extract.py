"""
ingestion/pdf_extract.py
========================
Judgement PDF → text, extraction cascade (SPEC Phase 1.4)। PyPDF2 এর তিনটা সমস্যা ঠিক করে:
ভাঙা শব্দ ('DIVISIO N'), legacy-font বাংলা ('q¡C−L¡VÑ' = 'হাইকোর্ট'), আর text layer ছাড়া scan
(extract_text.py এগুলো চুপচাপ বাদ দিত)।

প্রতি page এ:
  1. PyMuPDF text (blocks, reading order)।
  2. PyMuPDF খুলতে/পড়তে না পারলে pdfplumber।
  3. OCR (Tesseract ben+eng):
       - page এ কোনো text নেই                         → পুরো page
       - legacy-font text page এর অর্ধেকের বেশি         → পুরো page (একটা call, কম overhead)
       - কিছু block legacy font এ                       → শুধু ওই block গুলোর অংশ
       - text layer এর বাংলা নষ্ট (D-117)                → বাংলা আছে এমন block, অর্ধেকের বেশি হলে পুরো page
     Legacy span চেনা হয় font নাম (AdarshaLipi*, SutonnyMJ …) অথবা mojibake character দিয়ে।
     SPEC এ "OCR only for pages with no extractable text" — কিন্তু legacy-font page এ text আছে,
     সেটাই mojibake; তাই trigger টা "page/block quality check fail" (docs/DECISIONS.md D-103)।

Crash-safety (docs/DECISIONS.md D-107):
  - .txt আর checkpoint atomic (temp + fsync + rename), তাই আধা-লেখা file কখনো থাকে না।
  - Resume: .txt তখনই "হয়েছে" যখন manifest row আছে আর character count মেলে। না মিললে quarantine
    folder এ সরানো হয় (delete না), তারপর আবার extract। ঠিক থাকা output কখনো লেখা হয় না।
  - shard_threshold_pages এর বেশি page হলে shard_pages করে checkpoint (text_v2_parts/) —
    Death Reference 58/2013 এর একটা PDF ই 16,560 page; crash এ সর্বোচ্চ এক shard হারায়।
  - Worker মরে গেলে শুধু ওই batch এর অসমাপ্ত unit আবার চলে, একা একা; একা চলেও দুইবার মরলে
    failures.jsonl এ যায়। run বন্ধ হয় না।
  - Worker process প্রতি batch এ নতুন (Python 3.10 এ max_tasks_per_child নেই)।

    python ingestion/pdf_extract.py --status                 # read-only: কী হয়েছে, কী বাকি
    python ingestion/pdf_extract.py --workers 2              # downloaded_pdfs/ → text_v2/
    python ingestion/pdf_extract.py --retry-failed

প্রতি file এর method/সময় text_v2/_manifest.jsonl এ (append-only, শেষ row টাই গণ্য), ব্যর্থতা
data_quality/failures.jsonl এ। Page গুলো form feed (\\f) দিয়ে আলাদা।
"""

import argparse
import ctypes
import io
import json
import os
import re
import sys
import time
import traceback
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.quality_audit import BN_LETTER_RE, BN_SIGN_RE, LEGACY_RE, junk_stats, text_layer_problem  # noqa: E402

CONFIG = {
    "tesseract_cmd": os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    "tessdata_dir": os.getenv("TESSDATA_PREFIX", str(ROOT / "tessdata")),
    "ocr_lang": "ben+eng",
    "ocr_dpi": 200,                 # probe এ 300 dpi র চেয়ে দ্রুত (3.7s vs 6.3s/page) আর কম junk
    "ocr_timeout_s": 300,           # এক OCR call এর সীমা; আটকে গেলে page টা ব্যর্থ, run চলতে থাকে
    # Default এ Tesseract বাংলার কিছু লাইনকে "noise" ধরে চুপচাপ ফেলে দেয় — Death Ref 58 এ প্রতি ৬ লাইনে
    # ১টা হারিয়েছিল, কোনো error ছাড়া (docs/DECISIONS.md D-114)। এই দুইটা flag এ 179/179 লাইন থাকে।
    "ocr_config": "-c textord_noise_rejrows=0 -c textord_noise_rejwords=0",
    # টাইট এক-লাইনের crop এ default layout mode কখনো কিছুই ফেরত দেয় না (S48 p118: ১২ token এর লাইন → ফাঁকা)।
    # 116 block: ফাঁকা এক-লাইন 3/57 → 0/57, full-page এর সাথে মিল 87.2% → 93.2%, গতি একই (D-118)।
    "ocr_block_config": "--psm 6",
    "ocr_block_border_px": 20,      # pad না করে সাদা border, নইলে পাশের লাইন ঢুকে যায় (985 শব্দ বনাম 907)
    "min_page_chars": 20,           # এর কম text থাকলে page "text ছাড়া" ধরা হয়
    "full_page_ocr_share": 0.5,     # legacy-font character এর ভাগ এর বেশি হলে পুরো page OCR
    "span_legacy_char_ratio": 0.1,  # font নাম না মিললেও span এ এত mojibake থাকলে legacy
    "span_control_char_ratio": 0.3, # এর বেশি control code = character map ছাড়া embed করা font
    "junk_block_ratio": 0.5,        # নষ্ট page এ বাংলা ছাড়া এমন block ও OCR হবে — অনুমান
    "junk_block_min_tokens": 3,
    "shard_threshold_pages": 200,   # এর বেশি page হলে page-range checkpoint
    "shard_pages": 50,              # OCR এ ~3.8 s/page, তাই crash এ সর্বোচ্চ ~৩ মিনিটের কাজ হারায়
    "pool_units_files": 25,         # এতগুলো file এর পর worker process নতুন
    "pool_units_shards": 10,
    "stall_timeout_s": 3600,        # এতক্ষণ কোনো unit শেষ না হলে worker আটকে গেছে ধরা হয়
    "max_solo_crashes": 2,          # একা চলেও এতবার worker মারলে unit টা failures.jsonl এ
    "low_ram_mb": 1000,             # available RAM এর নিচে হলে 1 worker
    "critical_ram_mb": 400,         # এর নিচে হলে নতুন batch শুরুর আগে অপেক্ষা
    "ram_wait_max_s": 600,
}
LEGACY_FONT_RE = re.compile(r"AdarshaLipi|SutonnyMJ|MJ\b|Bijoy|Sulekha|Lekhoni|Boishakhi|ChandrabatiMJ|Kalpurush ANSI",
                            re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f]")
PAGE_SEP = "\f"
MANIFEST_NAME = "_manifest.jsonl"
# এই stage গুলোর entry মানে file টা নিজেই ব্যর্থ — পরের run এ --retry-failed ছাড়া skip
FILE_FAILURE_STAGES = {"pdf_extract", "worker_crash"}


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ─── SAFE WRITES ──────────────────────────────────────────

def atomic_write_text(path: Path, text: str) -> None:
    """Temp এ লিখে fsync করে rename — মাঝপথে বন্ধ হলে পুরনো file অক্ষত, আধা-লেখা file থাকে না।"""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_jsonl(path: Path, row: dict) -> None:
    """এক row করে লিখে disk এ flush — hard kill এও আগের row হারায় না।"""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def repair_jsonl_tail(path: Path) -> None:
    """আগের run মাঝ-row এ মারা গেলে শেষ লাইনে newline থাকে না; পরের row যেন তার সাথে জুড়ে না যায়।"""
    if not path.exists() or path.stat().st_size == 0:
        return
    with open(path, "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        ends_clean = fh.read(1) == b"\n"
    if not ends_clean:
        with open(path, "ab") as fh:
            fh.write(b"\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue   # মাঝপথে থেমে যাওয়া row — সেই file manifest এ নেই ধরে আবার যাচাই হবে
    return rows


def acquire_lock(path: Path):
    """একসাথে দুইটা run যেন একই output এ না লেখে। OS process মরলে lock নিজে ছেড়ে দেয়, তাই stale
    lock হয় না। ফেরত আসা handle run শেষ পর্যন্ত ধরে রাখতে হবে; আরেকটা run চললে None।"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def keep_awake() -> None:
    """Run চলাকালীন idle sleep আটকায়; process শেষ হলে নিজে উঠে যায়। lid বন্ধ বা নিজে sleep দেওয়া
    আটকায় না — তবে সেক্ষেত্রেও checkpoint থেকে আবার চালানো যায়।"""
    if sys.platform == "win32":
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def available_ram_mb() -> int | None:
    if sys.platform != "win32":
        return None

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullAvailPhys // (1024 * 1024)


def workers_for_ram(requested: int) -> int:
    """RAM কম থাকলে worker কমায়, খুব কম থাকলে নতুন batch এর আগে অপেক্ষা করে। এই machine এ 7.7 GB।"""
    available = available_ram_mb()
    if available is None:
        return requested
    waited = 0
    while available < CONFIG["critical_ram_mb"] and waited < CONFIG["ram_wait_max_s"]:
        log(f"⚠️ available RAM {available} MB — অপেক্ষা করছি")
        time.sleep(30)
        waited += 30
        available = available_ram_mb()
    return 1 if available < CONFIG["low_ram_mb"] else requested


# ─── EXTRACTION ───────────────────────────────────────────

def _is_legacy_span(span: dict) -> bool:
    """Text layer থেকে পড়া যায় না এমন span — legacy-font mojibake, অথবা ToUnicode map ছাড়া
    embed করা font (sample এ Vrinda: বাংলা অক্ষরের বদলে '\\x01\\x02\\x03' glyph id আসে)।"""
    text = span.get("text", "")
    if LEGACY_FONT_RE.search(span.get("font", "")):
        return bool(text.strip())
    if text.strip() and len(CONTROL_RE.findall(text)) / len(text) > CONFIG["span_control_char_ratio"]:
        return True
    letters = sum(c.isalpha() for c in text) or 1
    return len(text) >= 5 and len(LEGACY_RE.findall(text)) / letters > CONFIG["span_legacy_char_ratio"]


def _ocr_image(img, extra_config: str = "") -> str:
    import pytesseract
    config = f'{CONFIG["ocr_config"]} {extra_config}'.strip()
    return pytesseract.image_to_string(img, lang=CONFIG["ocr_lang"], config=config, timeout=CONFIG["ocr_timeout_s"])


def _ocr_pymupdf(page, clip=None) -> str:
    from PIL import Image, ImageOps
    pix = page.get_pixmap(dpi=CONFIG["ocr_dpi"], clip=clip)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    if clip is None:
        return _ocr_image(img)
    return _ocr_image(ImageOps.expand(img, border=CONFIG["ocr_block_border_px"], fill="white"), CONFIG["ocr_block_config"])


@dataclass
class PagePlan:
    """এক page নিয়ে কী করা হবে। OCR চালায় না, তাই audit একই সিদ্ধান্ত দেখে OCR খরচ ছাড়াই গুনতে পারে।"""
    method: str
    blocks: list = field(default_factory=list)      # (block, text, ocr করতে হবে কিনা)
    layer_problem: str = ""

    @property
    def ocr_blocks(self) -> int:
        return sum(1 for _, _, needs_ocr in self.blocks if needs_ocr)


def page_text_blocks(page) -> list:
    return [b for b in page.get_text("dict", sort=True)["blocks"] if b.get("type") == 0]


def _block_is_unreadable(text: str) -> bool:
    """নষ্ট বলে ধরা page এর কোন block OCR হবে: বাংলা অক্ষর আছে, অথবা নিজেই প্রতীকের junk।"""
    if BN_LETTER_RE.search(text) or BN_SIGN_RE.search(text):
        return True
    total, junk = junk_stats(text)
    return total >= CONFIG["junk_block_min_tokens"] and junk / total >= CONFIG["junk_block_ratio"]


def plan_blocks(blocks: list, check_layer: bool = True) -> PagePlan:
    """check_layer=False = v2 এর সিদ্ধান্ত (শুধু legacy font), audit এ তুলনার জন্য।"""
    total = legacy = corrupted = 0
    parsed = []
    for b in blocks:
        spans = [s for line in b["lines"] for s in line["spans"]]
        n = sum(len(s["text"]) for s in spans)
        n_legacy = sum(len(s["text"]) for s in spans if _is_legacy_span(s))
        total += n
        legacy += n_legacy
        # span শব্দের মাঝখানে ভাঙে, তাই লাইন জোড়া দেওয়া text এর উপরেই নষ্ট বাংলার হিসাব
        text = "\n".join("".join(s["text"] for s in line["spans"]) for line in b["lines"])
        parsed.append([b, text, n_legacy > 0, n])

    if total < CONFIG["min_page_chars"]:
        return PagePlan("ocr_page_no_text")

    problem = ""
    if check_layer:
        problem = text_layer_problem("\n".join(text for _, text, unreadable, _ in parsed if not unreadable))
        if problem == "junk_tokens":
            return PagePlan("ocr_page_corrupted", layer_problem=problem)
        if problem:
            for row in parsed:
                if not row[2] and _block_is_unreadable(row[1]):
                    row[2] = True
                    corrupted += row[3]

    if (legacy + corrupted) / total > CONFIG["full_page_ocr_share"]:
        return PagePlan("ocr_page_legacy" if legacy >= corrupted else "ocr_page_corrupted", layer_problem=problem)
    method = "pymupdf+ocr_bangla" if corrupted else "pymupdf+ocr_blocks" if legacy else "pymupdf"
    return PagePlan(method, [(b, text, needs_ocr) for b, text, needs_ocr, _ in parsed], problem)


def extract_page_pymupdf(page) -> tuple[str, str, int]:
    """(text, method, ocr_blocks)"""
    import pymupdf
    plan = plan_blocks(page_text_blocks(page))
    if plan.method.startswith("ocr_page"):
        return _ocr_pymupdf(page), plan.method, 0
    out = []
    for b, text, needs_ocr in plan.blocks:
        out.append(_ocr_pymupdf(page, clip=pymupdf.Rect(b["bbox"])).strip() if needs_ocr else text)
    return "\n".join(out), plan.method, plan.ocr_blocks


def extract_page_pdfplumber(pdf_path: Path, page_no: int) -> tuple[str, str]:
    import pdfplumber
    # pages= দিলে শুধু এই page load হয় — 16,000 page এর file এও সস্তা
    with pdfplumber.open(pdf_path, pages=[page_no + 1]) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        if len(text.strip()) >= CONFIG["min_page_chars"] and not LEGACY_RE.search(text) and not text_layer_problem(text):
            return text, "pdfplumber"
        return _ocr_image(page.to_image(resolution=CONFIG["ocr_dpi"]).original), "ocr_page_pdfplumber"


def page_count(pdf_path: Path) -> int:
    import pymupdf
    try:
        doc = pymupdf.open(pdf_path)
        try:
            return doc.page_count
        finally:
            doc.close()
    except Exception:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)


def extract_pages(pdf_path: Path, start: int = 0, end: int | None = None) -> dict:
    """[start, end) page এর text। একটা page ব্যর্থ হলে সেটা failed_pages এ যায়, বাকিগুলো চলে;
    সব page ব্যর্থ হলে exception — তখন file টাই ব্যর্থ।"""
    import pymupdf
    started = time.time()
    pages, methods, failed_pages, ocr_blocks = [], Counter(), [], 0
    doc = None
    try:
        try:
            doc = pymupdf.open(pdf_path)
            total = doc.page_count
        except Exception:
            doc = None
            total = page_count(pdf_path)
        end = total if end is None else min(end, total)
        for i in range(start, end):
            try:
                if doc is None:
                    raise RuntimeError("pymupdf could not open the file")
                text, method, n_ocr = extract_page_pymupdf(doc[i])
                ocr_blocks += n_ocr
            except Exception:
                try:
                    text, method = extract_page_pdfplumber(pdf_path, i)
                except Exception as e:
                    text, method = "", "failed_page"
                    failed_pages.append({"page": i, "error": f"{type(e).__name__}: {e}"[:300]})
            # text layer এর ছড়ানো control code (\f সহ) বাদ — \f শুধু page separator থাকে
            pages.append(CONTROL_RE.sub("", text).strip())
            methods[method] += 1
    finally:
        if doc is not None:
            doc.close()
    if pages and len(failed_pages) == len(pages):
        raise RuntimeError(f"all {len(pages)} pages failed; first: {failed_pages[0]['error']}")
    return {"pages": pages, "methods": dict(methods), "failed_pages": failed_pages,
            "ocr_blocks": ocr_blocks, "seconds": round(time.time() - started, 1)}


# ─── WORK UNITS ───────────────────────────────────────────

@dataclass(frozen=True)
class Unit:
    """পুরো একটা file, অথবা বড় file এর একটা page range।"""
    pdf: str
    start: int = 0
    end: int = 0
    shard: bool = False

    @property
    def name(self) -> str:
        return Path(self.pdf).name

    @property
    def label(self) -> str:
        return f"{self.name} p{self.start}-{self.end}" if self.shard else self.name


def shard_ranges(n_pages: int, size: int) -> list[tuple[int, int]]:
    return [(s, min(s + size, n_pages)) for s in range(0, n_pages, size)]


def shard_path(parts_dir: Path, stem: str, start: int, end: int) -> Path:
    return parts_dir / stem / f"p{start:06d}-{end:06d}.json"


def load_shard(path: Path, start: int, end: int) -> dict | None:
    """Checkpoint পড়ে যাচাই করে; ঠিক না থাকলে None (তখন shard টা আবার চলে)।"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    ok = data.get("start") == start and data.get("end") == end and len(data.get("pages", [])) == end - start
    return data if ok else None


def run_unit(unit: Unit, out_dir: str, parts_dir: str) -> dict:
    """Worker process এ চলে। Python exception এখানেই ধরা পড়ে; process মরলে supervisor সামলায়।"""
    path = Path(unit.pdf)
    try:
        if unit.shard:
            result = extract_pages(path, unit.start, unit.end)
            target = shard_path(Path(parts_dir), path.stem, unit.start, unit.end)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, json.dumps({"start": unit.start, "end": unit.end, **result}, ensure_ascii=False))
            return {"filename": path.name, "ok": True, "pages": len(result["pages"]), "methods": result["methods"],
                    "failed_pages": result["failed_pages"], "ocr_blocks": result["ocr_blocks"],
                    "seconds": result["seconds"]}

        result = extract_pages(path)
        text = PAGE_SEP.join(result["pages"])
        if not text.strip():
            raise ValueError("no text extracted")
        atomic_write_text(Path(out_dir) / f"{path.stem}.txt", text)
        return {"filename": path.name, "ok": True, "pages": len(result["pages"]), "methods": result["methods"],
                "failed_pages": result["failed_pages"], "ocr_blocks": result["ocr_blocks"],
                "seconds": result["seconds"], "chars": len(text)}
    except Exception as e:
        return {"filename": path.name, "ok": False, "error": f"{type(e).__name__}: {e}"[:500],
                "trace": traceback.format_exc(limit=3)}


def _init_worker() -> None:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = CONFIG["tesseract_cmd"]
    os.environ["TESSDATA_PREFIX"] = CONFIG["tessdata_dir"]
    os.environ["OMP_THREAD_LIMIT"] = "1"   # parallel process এর মধ্যে Tesseract thread কাড়াকাড়ি না করে


# ─── SUPERVISOR ───────────────────────────────────────────

def _run_batch(batch: list, workers: int, runner, on_result, stall_timeout: int) -> list:
    """একটা নতুন pool এ batch চালায়। যে unit এর ফলাফল আসেনি (worker মরেছে বা আটকে গেছে) তার list ফেরত।"""
    unfinished = set(batch)
    pool = ProcessPoolExecutor(workers, initializer=_init_worker)
    try:
        futures = {pool.submit(runner, unit): unit for unit in batch}
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=stall_timeout, return_when=FIRST_COMPLETED)
            if not done:
                log(f"⚠️ {stall_timeout}s ধরে কোনো unit শেষ হয়নি — worker বন্ধ করছি: {[u.label for u in unfinished][:5]}")
                for proc in list((pool._processes or {}).values()):
                    proc.terminate()
                break
            for fut in done:
                unit = futures[fut]
                try:
                    row = fut.result()
                except BrokenProcessPool:
                    continue
                unfinished.discard(unit)
                on_result(unit, row)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return [unit for unit in batch if unit in unfinished]


def run_units(units: list, workers: int, runner, on_result, on_gave_up, pool_units: int,
              stall_timeout: int | None = None, max_solo_crashes: int | None = None) -> None:
    """সব unit চালায়; worker মরলে run থামে না। অসমাপ্ত unit গুলো একা একা আবার চলে, কারণ pool এ বাকি
    unit গুলো নির্দোষ হতে পারে। একা চলেও max_solo_crashes বার worker মরলে on_gave_up।"""
    stall_timeout = stall_timeout or CONFIG["stall_timeout_s"]
    max_solo_crashes = max_solo_crashes or CONFIG["max_solo_crashes"]
    queue, suspects = deque(units), deque()
    crashes = Counter()
    while queue or suspects:
        if suspects:
            batch, n = [suspects.popleft()], 1
        else:
            batch = [queue.popleft() for _ in range(min(pool_units, len(queue)))]
            n = workers_for_ram(workers)
        unfinished = _run_batch(batch, n, runner, on_result, stall_timeout)
        if unfinished:
            log(f"⚠️ worker মারা গেছে; অসমাপ্ত {len(unfinished)} unit আবার চলবে: {[u.label for u in unfinished][:5]}")
        for unit in unfinished:
            if len(batch) == 1:   # শুধু এই unit চলছিল, তাই দায় এর
                crashes[unit] += 1
                if crashes[unit] >= max_solo_crashes:
                    on_gave_up(unit)
                    continue
            suspects.append(unit)


# ─── STATE ────────────────────────────────────────────────

def read_manifest(out_dir: Path) -> dict:
    return {r["filename"]: r for r in read_jsonl(out_dir / MANIFEST_NAME) if "filename" in r}


def read_file_failures(failures_path: Path) -> dict:
    return {r["filename"]: r for r in read_jsonl(failures_path)
            if r.get("stage") in FILE_FAILURE_STAGES and "filename" in r}


def output_is_valid(txt_path: Path, row: dict | None) -> tuple[bool, str]:
    """.txt আছে মানেই হয়েছে না — manifest row থাকতে হবে আর character count মিলতে হবে।"""
    if row is None or not row.get("ok"):
        return False, "no manifest row"
    try:
        text = txt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, f"unreadable: {type(e).__name__}"
    if not text.strip():
        return False, "empty"
    if len(text) != row.get("chars"):
        return False, f"chars {len(text)} != manifest {row.get('chars')}"
    return True, ""


def inventory(pdf_dir: Path, out_dir: Path, failures_path: Path, settle_seconds: int = 0) -> dict:
    """Disk দেখে অবস্থা বের করে — কোনো progress counter এর উপর ভরসা না করে। কিছু লেখে না।"""
    manifest = read_manifest(out_dir)
    failed = read_file_failures(failures_path)
    cutoff = time.time() - settle_seconds
    valid, todo, invalid = [], [], {}
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        if settle_seconds and pdf.stat().st_mtime >= cutoff:
            continue
        txt = out_dir / f"{pdf.stem}.txt"
        if not txt.exists():
            todo.append(pdf.name)
            continue
        ok, why = output_is_valid(txt, manifest.get(pdf.name))
        if ok:
            valid.append(pdf.name)
        else:
            invalid[pdf.name] = why
    return {"valid": valid, "todo": todo, "invalid": invalid,
            "failed": [n for n in todo if n in failed]}


def quarantine(txt_path: Path, quarantine_dir: Path, reason: str, failures_path: Path) -> Path:
    """সন্দেহজনক output delete না করে সরিয়ে রাখে, পরে দেখা যায়।"""
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    target = quarantine_dir / f"{txt_path.stem}.{datetime.now():%Y%m%d%H%M%S}.txt"
    os.replace(txt_path, target)
    append_jsonl(failures_path, {"stage": "quarantine", "filename": txt_path.stem + ".pdf", "reason": reason,
                                 "moved_to": str(target), "time": datetime.now().isoformat(timespec="seconds")})
    return target


def merge_shards(pdf: Path, n_pages: int, out_dir: Path, parts_dir: Path, shard_size: int) -> bool:
    """সব checkpoint থাকলে এক .txt আর এক manifest row বানায়। না থাকলে False, কিছু লেখে না।"""
    shards = []
    for start, end in shard_ranges(n_pages, shard_size):
        data = load_shard(shard_path(parts_dir, pdf.stem, start, end), start, end)
        if data is None:
            return False
        shards.append(data)
    text = PAGE_SEP.join(page for data in shards for page in data["pages"])
    if not text.strip():
        return False
    methods = Counter()
    for data in shards:
        methods.update(data["methods"])
    atomic_write_text(out_dir / f"{pdf.stem}.txt", text)
    append_jsonl(out_dir / MANIFEST_NAME, {
        "filename": pdf.name, "ok": True, "pages": n_pages, "methods": dict(methods),
        "failed_pages": [p for data in shards for p in data["failed_pages"]],
        "ocr_blocks": sum(data["ocr_blocks"] for data in shards),
        "seconds": round(sum(data["seconds"] for data in shards), 1),
        "chars": len(text), "shards": len(shards),
    })
    return True


# ─── MAIN LOOP ────────────────────────────────────────────

def process_all(pdf_dir: Path, out_dir: Path, parts_dir: Path, failures_path: Path, quarantine_dir: Path,
                workers: int = 2, limit: int = 0, retry_failed: bool = False, settle_seconds: int = 0,
                runner=None, shard_threshold: int | None = None, shard_size: int | None = None) -> dict:
    shard_threshold = shard_threshold or CONFIG["shard_threshold_pages"]
    shard_size = shard_size or CONFIG["shard_pages"]
    out_dir.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_NAME
    repair_jsonl_tail(manifest_path)
    repair_jsonl_tail(failures_path)

    state = inventory(pdf_dir, out_dir, failures_path, settle_seconds)
    for name, why in state["invalid"].items():
        target = quarantine(out_dir / f"{Path(name).stem}.txt", quarantine_dir, why, failures_path)
        log(f"⚠️ {name}: {why} → {target}")
    skip = set() if retry_failed else set(state["failed"])
    todo = [n for n in [*state["todo"], *state["invalid"]] if n not in skip]
    if limit:
        todo = todo[:limit]
    log(f"{len(state['valid'])} যাচাই করা output অক্ষত আছে, {len(todo)} বাকি, {len(skip)} আগে ব্যর্থ (skip) — {workers} worker")

    small, large = [], {}
    for name in sorted(todo):
        try:
            n_pages = page_count(pdf_dir / name)
        except Exception:
            n_pages = 0     # খোলা না গেলে worker এ গিয়ে failures.jsonl এ পড়বে
        if n_pages > shard_threshold:
            large[name] = n_pages
        else:
            small.append(name)

    counts = Counter()
    methods = Counter()
    runner = runner or partial(run_unit, out_dir=str(out_dir), parts_dir=str(parts_dir))
    started = time.time()

    def record_failed_pages(unit, row):
        for page in row.get("failed_pages") or []:
            append_jsonl(failures_path, {"stage": "page", "filename": unit.name, **page})

    def on_file(unit, row):
        if row["ok"]:
            append_jsonl(manifest_path, row)
            record_failed_pages(unit, row)
            methods.update(row["methods"])
            counts["extracted"] += 1
        else:
            append_jsonl(failures_path, {"stage": "pdf_extract", **row, "time": datetime.now().isoformat(timespec="seconds")})
            counts["failed"] += 1
            log(f"❌ {unit.label}: {row['error']}")
        done = counts["extracted"] + counts["failed"]
        if done % 25 == 0 or done == len(small):
            log(f"   files {done}/{len(small)} ({counts['failed']} failed, {(time.time() - started) / done:.1f}s/file) "
                f"pages by method: {dict(methods)} | RAM {available_ram_mb()} MB")

    def on_gave_up(unit):
        append_jsonl(failures_path, {"stage": "worker_crash", "filename": unit.name, "unit": unit.label,
                                     "error": "worker process died running this unit, including when run alone",
                                     "time": datetime.now().isoformat(timespec="seconds")})
        counts["failed"] += 1
        log(f"❌ {unit.label}: একা চলেও worker মারা গেল — failures.jsonl এ")

    run_units([Unit(str(pdf_dir / n)) for n in small], workers, runner, on_file, on_gave_up, CONFIG["pool_units_files"])

    for name, n_pages in large.items():
        pdf = pdf_dir / name
        pending = [Unit(str(pdf), s, e, shard=True) for s, e in shard_ranges(n_pages, shard_size)
                   if load_shard(shard_path(parts_dir, pdf.stem, s, e), s, e) is None]
        log(f"{name}: {n_pages} page, {n_pages // shard_size + 1} shard এর মধ্যে {len(pending)} বাকি")
        pages_left = sum(u.end - u.start for u in pending)
        shard_started = time.time()
        shard_pages_done = [0]

        def on_shard(unit, row):
            if row["ok"]:
                record_failed_pages(unit, row)
                shard_pages_done[0] += row["pages"]
                rate = (time.time() - shard_started) / shard_pages_done[0]
                log(f"   {unit.label} ✓ {row['seconds']}s | {shard_pages_done[0]}/{pages_left} page | {rate:.2f} s/page | "
                    f"এই file এ আর ~{rate * (pages_left - shard_pages_done[0]) / 3600:.1f} h | RAM {available_ram_mb()} MB")
            else:
                append_jsonl(failures_path, {"stage": "worker_crash", "filename": unit.name, "unit": unit.label,
                                             "error": row["error"], "time": datetime.now().isoformat(timespec="seconds")})
                counts["failed"] += 1
                log(f"❌ {unit.label}: {row['error']}")

        run_units(pending, workers, runner, on_shard, on_gave_up, CONFIG["pool_units_shards"])
        if merge_shards(pdf, n_pages, out_dir, parts_dir, shard_size):
            counts["extracted"] += 1
            log(f"✅ {name}: সব shard জোড়া লাগানো হলো")
        else:
            log(f"⚠️ {name}: কিছু shard বাকি — পরের run সেখান থেকে চলবে")

    log(f"✅ এই run এ {counts['extracted']} extracted, {counts['failed']} failed → {out_dir}")
    return dict(counts)


def status_report(pdf_dir: Path, out_dir: Path, parts_dir: Path, failures_path: Path) -> dict:
    state = inventory(pdf_dir, out_dir, failures_path)
    report = {"pdfs": len(state["valid"]) + len(state["todo"]) + len(state["invalid"]),
              "valid_outputs": len(state["valid"]), "remaining": len(state["todo"]),
              "invalid_outputs": state["invalid"], "failed": state["failed"], "large_files": {}}
    for name in state["todo"]:
        n_pages = page_count(pdf_dir / name)
        if n_pages > CONFIG["shard_threshold_pages"]:
            stem = Path(name).stem
            ranges = shard_ranges(n_pages, CONFIG["shard_pages"])
            done = sum(1 for s, e in ranges if load_shard(shard_path(parts_dir, stem, s, e), s, e) is not None)
            report["large_files"][name] = {"pages": n_pages, "shards": len(ranges), "shards_done": done}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default=str(ROOT / "downloaded_pdfs"))
    ap.add_argument("--out", default=str(ROOT / "text_v2"))
    ap.add_argument("--parts", default=str(ROOT / "text_v2_parts"))
    ap.add_argument("--failures", default=str(ROOT / "data_quality" / "failures.jsonl"))
    ap.add_argument("--quarantine", default=str(ROOT / "data_quality" / "quarantine"))
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--retry-failed", action="store_true", help="failures.jsonl এ থাকা file আবার চেষ্টা করো")
    ap.add_argument("--settle-seconds", type=int, default=30,
                    help="এর চেয়ে নতুন PDF বাদ — download চলাকালীন আধা-লেখা file না পড়তে")
    ap.add_argument("--status", action="store_true", help="read-only: কী হয়েছে আর কী বাকি দেখাও")
    args = ap.parse_args()

    paths = (Path(args.pdf_dir), Path(args.out), Path(args.parts), Path(args.failures))
    if args.status:
        print(json.dumps(status_report(*paths), ensure_ascii=False, indent=1))
        return 0

    lock = acquire_lock(ROOT / "data_quality" / "extract.lock")
    if lock is None:
        log("আরেকটা extraction run চলছে — এটা বন্ধ হলো")
        return 3
    keep_awake()
    process_all(*paths, Path(args.quarantine), workers=args.workers, limit=args.limit,
                retry_failed=args.retry_failed, settle_seconds=args.settle_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
