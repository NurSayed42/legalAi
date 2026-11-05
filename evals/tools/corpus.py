"""
evals/tools/corpus.py
=====================
text_v3 এর পূর্ণ text, retriever ছাড়া। Index এ যা নেই (Death Ref 58 এর cap করা অংশ) তাও এখানে আছে, তাই benchmark
এর প্রমাণ আর স্বাধীন pool arm এখান থেকে আসে। শুধু পড়ে।
"""

import re
import unicodedata
from pathlib import Path

from ingestion.chunk_cap import case_key

ROOT = Path(__file__).resolve().parent.parent.parent
TEXT_DIR = ROOT / "text_v3"
BN = re.compile("[ঀ-৿]")
LATIN = re.compile("[A-Za-z]")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def doc_script(text: str) -> str:
    """Probe এর একই নিয়ম: বাংলা অক্ষর বেশি হলে bn, লাতিনের ১০% এর বেশি হলে mixed, নইলে en।"""
    bn, latin = len(BN.findall(text)), len(LATIN.findall(text))
    return "bn" if bn > latin else ("mixed" if bn > 0.1 * latin else "en")


def load_corpus(text_dir: Path = TEXT_DIR) -> dict[str, str]:
    """{case_key: পূর্ণ text}। একই মামলার কয়েকটা PDF নামের ক্রমে জোড়া।"""
    parts: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(text_dir.glob("*.txt")):
        pdf = path.stem + ".pdf"
        parts.setdefault(case_key(pdf), []).append((pdf, path.read_text(encoding="utf-8", errors="replace")))
    return {key: "\f".join(t for _, t in sorted(rows)) for key, rows in parts.items()}


def term_pattern(term: str) -> re.Pattern:
    """লাতিন আর অঙ্কে পুরো শব্দ ('bail' যেন 'bailiff' না ধরে), বাংলায় যেকোনো জায়গায়, কারণ বিভক্তি শব্দের সাথে লাগে।"""
    norm = re.escape(normalize(term))
    if BN.search(term):
        return re.compile(norm)
    return re.compile(rf"(?<![a-z0-9]){norm}(?![a-z0-9])")


def term_counts(text_norm: str, patterns: dict[str, re.Pattern]) -> dict[str, int]:
    return {t: len(p.findall(text_norm)) for t, p in patterns.items()}
