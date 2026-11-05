"""
retrieval/case_search.py
========================
বড় মামলার জন্য Phase 3 এর পরীক্ষা (D-209)। Cap ছাড়াই: index এ হাত না দিয়ে মামলা পাওয়ার পরে তার পূর্ণ text এ খোঁজা, আর এক মামলা
থেকে কয়টা chunk যাবে তার সীমা। Collection এ কিছু লেখে না, text_v3 শুধু পড়ে। SPEC এর get_case_text এর ভিত্তি।
"""

from collections import Counter
from collections.abc import Callable

from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from retrieval.hybrid import RankedChunk, tokenize


class Passage(BaseModel):
    start_word: int
    score: float
    text: str


def word_windows(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    """(শুরুর শব্দ, লেখা)। enrich_judgements এর chunk এর মতো আকার, যাতে তুলনা চলে।"""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    words = text.split()
    step = size - overlap
    return [(i, " ".join(words[i : i + size])) for i in range(0, max(len(words) - overlap, 1), step)]


class CaseTextIndex:
    def __init__(self, text: str, size: int = 600, overlap: int = 80, tokenizer: Callable[[str], list[str]] = tokenize):
        self.windows = word_windows(text, size, overlap)
        self.tokenizer = tokenizer
        self.bm25 = BM25Okapi([tokenizer(w) for _, w in self.windows])

    def search(self, query: str, n: int = 5) -> list[Passage]:
        scores = self.bm25.get_scores(self.tokenizer(query))
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        return [Passage(start_word=self.windows[i][0], score=float(scores[i]), text=self.windows[i][1]) for i in order[:n] if scores[i] > 0]


def limit_per_case(ranked: list[RankedChunk], n: int) -> list[RankedChunk]:
    """এক মামলা থেকে সর্বোচ্চ n টা chunk, ক্রম একই। n=1 হলে collapse_to_cases এর সমান।"""
    if n < 1:
        raise ValueError("n must be at least 1")
    seen: Counter[str] = Counter()
    out = []
    for item in ranked:
        if seen[item.case_key] < n:
            seen[item.case_key] += 1
            out.append(item)
    return out
