"""
retrieval/hybrid.py
===================
Dense, BM25 আর fusion, মাপার জন্য আলাদা করা (SPEC Phase 2-3)। api_v3.py এখনো নিজের কপি চালায়, এই module তাকে
বদলায় না; tests/test_retrieval_parity.py দেখে দুটো একই আচরণ করছে কিনা।

সব কিছু chunk ধরে rank করে, মামলায় নামানো হয় শেষে (collapse_to_cases)। Collection এ শুধু read: এখানে কোনো
লেখার call নেই, আর test সেটাও পরীক্ষা করে।
"""

import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from ingestion.chunk_cap import case_key

# api_v3._TOKEN_RE এর সমান: \w একা বাংলা কার/চিহ্ন ধরে না, তাই পুরো Bengali block আর ZWNJ/ZWJ আলাদা করে।
TOKEN_RE = re.compile("[\\wঀ-৿‌‍]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class RankedChunk(BaseModel):
    chunk_id: str
    filename: str
    case_key: str
    score: float


class ChunkStore:
    """একটা collection এর সব chunk, id ধরে সাজানো, যাতে একই input এ একই ফল আসে।"""

    def __init__(self, ids: list[str], docs: list[str], metas: list[dict[str, Any]]):
        order = sorted(range(len(ids)), key=ids.__getitem__)
        self.ids = [ids[i] for i in order]
        self.docs = [docs[i] for i in order]
        self.metas = [metas[i] for i in order]
        self.index_of = {cid: i for i, cid in enumerate(self.ids)}

    @classmethod
    def from_collection(cls, collection, batch: int = 2000) -> "ChunkStore":
        ids, docs, metas = [], [], []
        offset = 0
        while True:
            got = collection.get(limit=batch, offset=offset, include=["documents", "metadatas"])
            if not got["ids"]:
                break
            ids += got["ids"]
            docs += got["documents"]
            metas += got["metadatas"]
            offset += len(got["ids"])
        return cls(ids, docs, metas)

    def ranked(self, index: int, score: float) -> RankedChunk:
        name = self.metas[index]["filename"]
        return RankedChunk(chunk_id=self.ids[index], filename=name, case_key=case_key(name), score=float(score))


class BM25Index:
    """api_v3._bm25_search এর মতো: score 0 বা তার নিচের chunk ফেরত দেয় না।"""

    def __init__(self, store: ChunkStore, tokenizer: Callable[[str], list[str]] | None = None):
        self.store = store
        self.tokenizer = tokenizer or tokenize  # Phase 3 এর পরীক্ষার জন্য বদলানো যায়, default baseline
        self.bm25 = BM25Okapi([self.tokenizer(d) for d in store.docs])

    def search(self, query: str, n: int) -> list[RankedChunk]:
        scores = self.bm25.get_scores(self.tokenizer(query))
        # সমান score এ id ধরে, api_v3 এ stable sort করে collection এর ক্রমে
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], self.store.ids[i]))
        out = []
        for i in order[:n]:
            if scores[i] <= 0:
                break
            out.append(self.store.ranked(i, scores[i]))
        return out


def dense_search(collection, store: ChunkStore, query: str, n: int) -> list[RankedChunk]:
    """score = cosine similarity: তিনটা judgements collection ই hnsw:space=cosine।"""
    r = collection.query(query_texts=[query], n_results=n, include=["distances"])
    return [store.ranked(store.index_of[cid], 1 - dist) for cid, dist in zip(r["ids"][0], r["distances"][0])]


def rrf_chunks(ranked_lists: list[list[RankedChunk]], k: int) -> list[RankedChunk]:
    """Chunk ধরে RRF। প্রতিটা chunk তার নিজের rank এর score পায়, তাই বড় মামলা chunk এর সংখ্যায় বাড়তি পায় না।"""
    scores: dict[str, float] = defaultdict(float)
    items: dict[str, RankedChunk] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item.chunk_id] += 1 / (k + rank + 1)
            items.setdefault(item.chunk_id, item)
    order = sorted(items, key=lambda cid: (-scores[cid], cid))
    return [items[cid].model_copy(update={"score": scores[cid]}) for cid in order]


def precedent_weight(meta: dict[str, Any]) -> float:
    """api_v3._precedent_weight এর সমান, ওজনগুলো এখনো সেখানে hardcoded (Phase 3 এ config এ যাবে)।"""
    division_w = 1.15 if meta.get("division") == "AD" else 1.0
    year = meta.get("year") or 2000
    recency_w = 1.0 + max(0, min(year, 2026) - 1950) / (2026 - 1950) * 0.2
    return division_w * recency_w


def rrf_as_api(ranked_lists: list[list[RankedChunk]], k: int, store: ChunkStore) -> list[RankedChunk]:
    """api_v3._fuse_cases যেভাবে মেলায়: একই file এর সব chunk এর score যোগ হয়, তারপর precedent weight।
    যোগ হওয়ার কারণে যে file এর বেশি chunk তালিকায় থাকে সে এগিয়ে যায়, এটাই মাপার বিষয়।"""
    scores: dict[str, float] = defaultdict(float)
    first: dict[str, RankedChunk] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item.filename] += 1 / (k + rank + 1)
            first.setdefault(item.filename, item)
    weighted = {}
    for name, item in first.items():
        weighted[name] = scores[name] * precedent_weight(store.metas[store.index_of[item.chunk_id]])
    order = sorted(first, key=lambda name: (-weighted[name], name))
    return [first[name].model_copy(update={"score": weighted[name]}) for name in order]


def collapse_to_cases(ranked: list[RankedChunk]) -> list[RankedChunk]:
    """প্রতিটা মামলার সবচেয়ে ভালো chunk টা রেখে বাকি বাদ, ক্রম একই।"""
    seen: set[str] = set()
    out = []
    for item in ranked:
        if item.case_key not in seen:
            seen.add(item.case_key)
            out.append(item)
    return out
