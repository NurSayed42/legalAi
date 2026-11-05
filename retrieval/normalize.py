"""
retrieval/normalize.py
======================
Phase 3 এর পরীক্ষার জন্য tokenizer এর ধাপ (D-209)। Production এ ব্যবহার হয় না, আর baseline tokenizer এ হাত দেয় না।

  baseline         api_v3 এর মতো: কোনো normalization নেই
  nfc              `য়` এর দুই রূপ এক হয়
  nfc_digits       বাংলা অঙ্ক ASCII হয়, তাই ৫৪৫৬/২০১৫ আর 5456 of 2015 এর সংখ্যা মেলে
  nfc_digits_stem  সাথে হালকা বাংলা stemming: দেনমোহরের → দেনমোহর

Stemming এর নিয়মগুলো আন্দাজ, শব্দভাণ্ডার ছাড়া। Document আর প্রশ্নে একই নিয়ম চলে, তাই ভুল stem ক্ষতিকর নয় যতক্ষণ না দুটো ভিন্ন শব্দ এক হয়।
"""

import json
import unicodedata
from collections.abc import Callable
from pathlib import Path

from retrieval.hybrid import tokenize

BN_TO_ASCII = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
LEVELS = ("baseline", "nfc", "nfc_digits", "nfc_digits_stem")
_CONFIG = json.loads(Path(__file__).with_name("normalize_config.json").read_text(encoding="utf-8"))
_BN_VOWEL_SIGNS = "ািীুূৃেৈোৌ"


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def digits_to_ascii(text: str) -> str:
    return text.translate(BN_TO_ASCII)


def _is_bangla(token: str) -> bool:
    return any("ঀ" <= ch <= "৿" for ch in token)


def _letters(token: str) -> int:
    """স্বরচিহ্ন আর হসন্ত বাদে অক্ষর, খুব ছোট stem ঠেকাতে।"""
    return sum(unicodedata.category(ch) == "Lo" for ch in token)


def light_stem(token: str, config: dict | None = None) -> str:
    config = config or _CONFIG
    if not _is_bangla(token):
        return token
    minimum = config["min_stem_letters"]
    for suffix in config["bn_suffixes"]:
        suffix = nfc(suffix)
        if token.endswith(suffix) and _letters(token[: -len(suffix)]) >= minimum:
            return token[: -len(suffix)]
    # জমিতে → জমি (স্বরের পরে তে), কিন্তু আদালতে → আদালত (ব্যঞ্জনের পরে শুধু এ-কার)
    if token.endswith("তে") and len(token) > 2 and token[-3] in _BN_VOWEL_SIGNS and _letters(token[:-2]) >= minimum - 1:
        return token[:-2]
    if token.endswith("ে") and _letters(token[:-1]) >= minimum:
        return token[:-1]
    if token.endswith("ির") and _letters(token[:-1]) >= minimum - 1:  # জমির → জমি
        return token[:-1]
    return token


def make_tokenizer(level: str) -> Callable[[str], list[str]]:
    if level not in LEVELS:
        raise ValueError(f"unknown level {level}; use one of {LEVELS}")
    if level == "baseline":
        return tokenize
    if level == "nfc":
        return lambda text: tokenize(nfc(text))
    if level == "nfc_digits":
        return lambda text: tokenize(digits_to_ascii(nfc(text)))
    return lambda text: [light_stem(t) for t in tokenize(digits_to_ascii(nfc(text)))]
