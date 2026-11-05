"""
evals/retrieval_run.py
======================
Benchmark এর প্রতিটা প্রশ্ন প্রতিটা retrieval system এ চালিয়ে ranked list লেখে (evals/runs/, gitignored)। Score
আলাদা করে evals/retrieval_score.py, তাই label বদলালে আবার চালাতে হয় না। Collection এ শুধু read।

    python evals/retrieval_run.py --collection bangladesh_judgements_v3 --out evals/runs/v3.json
    python evals/retrieval_run.py --collection bangladesh_judgements --systems dense bm25 hybrid hybrid_api --out evals/runs/v1.json
    python evals/retrieval_run.py --collection bangladesh_judgements_v3 --systems dense bm25 hybrid --query-form precomposed --language bn --out evals/runs/v3_precomposed.json

Reranker এর score evals/cache/ এ জমে: মাঝপথে থেমে গেলে একই command আবার চালালেই হয়।
"""

import argparse
import hashlib
import json
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.retrieval_schema import BenchmarkQuery, RetrievalEvalConfig, load_config, load_queries  # noqa: E402
from ingestion.chunk_cap import case_key  # noqa: E402
from retrieval.hybrid import BM25Index, ChunkStore, RankedChunk, collapse_to_cases, dense_search, rrf_as_api, rrf_chunks  # noqa: E402
from retrieval.rerank import CrossEncoderReranker  # noqa: E402

ALL_SYSTEMS = ["dense", "bm25", "hybrid", "hybrid_rerank", "hybrid_api"]
QUERIES = ROOT / "evals" / "golden" / "retrieval_queries.jsonl"
CONFIG = ROOT / "evals" / "retrieval_config.json"
CACHE = ROOT / "evals" / "cache" / "rerank_scores.jsonl"
SNIPPET_CHARS = 1500
# NFC এগুলোকে ভেঙে রাখে; কিছু keyboard এক অক্ষরে লেখে। corpus এ প্রায় সবই ভাঙা (D-203)।
PRECOMPOSED = {"য়": "য়", "ড়": "ড়", "ঢ়": "ঢ়"}


def to_precomposed(text: str) -> str:
    for decomposed, single in PRECOMPOSED.items():
        text = text.replace(decomposed, single)
    return text


def rerank_chunks(reranker: CrossEncoderReranker, store: ChunkStore, query: str, hybrid: list[RankedChunk], n: int) -> list[RankedChunk]:
    """Hybrid এর প্রথম n টা chunk reranker এ, বাকিগুলো তাদের নিচে আগের ক্রমে। Tail এর score -1: reranker দেখেনি।"""
    head, tail = hybrid[:n], hybrid[n:]
    scores = reranker.score(query, [store.docs[store.index_of[c.chunk_id]] for c in head])
    ranked = sorted(zip(head, scores), key=lambda p: (-p[1], p[0].chunk_id))
    return [c.model_copy(update={"score": s}) for c, s in ranked] + [c.model_copy(update={"score": -1.0}) for c in tail]


def run_query(q: BenchmarkQuery, text: str, systems: list[str], collection, store: ChunkStore, bm25: BM25Index,
              reranker: CrossEncoderReranker | None, cfg: RetrievalEvalConfig) -> dict[str, tuple[list[RankedChunk], float]]:
    out: dict[str, tuple[list[RankedChunk], float]] = {}
    t = time.perf_counter()
    dense = dense_search(collection, store, text, cfg.fetch_depth)
    dense_s = time.perf_counter() - t
    t = time.perf_counter()
    sparse = bm25.search(text, cfg.fetch_depth)
    sparse_s = time.perf_counter() - t
    fuse = dense_s + sparse_s
    if "dense" in systems:
        out["dense"] = (dense, dense_s)
    if "bm25" in systems:
        out["bm25"] = (sparse, sparse_s)
    t = time.perf_counter()
    hybrid = rrf_chunks([dense[: cfg.fusion_depth], sparse[: cfg.fusion_depth]], cfg.rrf_k)
    hybrid_s = fuse + time.perf_counter() - t
    if "hybrid" in systems:
        out["hybrid"] = (hybrid, hybrid_s)
    if "hybrid_rerank" in systems:
        t = time.perf_counter()
        reranked = rerank_chunks(reranker, store, text, hybrid, cfg.rerank_candidates)
        out["hybrid_rerank"] = (reranked, hybrid_s + time.perf_counter() - t)
    if "hybrid_api" in systems:
        t = time.perf_counter()
        api = rrf_as_api([dense[: cfg.api_retrieve_n], sparse[: cfg.api_retrieve_n]], cfg.rrf_k, store)
        out["hybrid_api"] = (api, fuse + time.perf_counter() - t)
    return out


def term_flags(chunks: list[RankedChunk], store: ChunkStore, terms: list[str]) -> list[bool]:
    wanted = [unicodedata.normalize("NFC", t).casefold() for t in terms]
    flags = []
    for c in chunks:
        doc = unicodedata.normalize("NFC", store.docs[store.index_of[c.chunk_id]]).casefold()
        flags.append(any(t in doc for t in wanted))
    return flags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", required=True)
    ap.add_argument("--systems", nargs="+", default=ALL_SYSTEMS, choices=ALL_SYSTEMS)
    ap.add_argument("--query-form", choices=["nfc", "precomposed"], default="nfc")
    ap.add_argument("--language", choices=["bn", "en"], default=None)
    ap.add_argument("--db", default=str(ROOT / "legal_chroma_db"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import chromadb
    from chromadb.utils import embedding_functions

    cfg = load_config(CONFIG)
    queries = load_queries(QUERIES)
    if args.language:
        queries = [q for q in queries if q.language == args.language]
    started = time.time()
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=cfg.embed_model)
    client = chromadb.PersistentClient(path=args.db)
    collection = client.get_collection(args.collection, embedding_function=ef)
    store = ChunkStore.from_collection(collection)
    print(f"{args.collection}: {len(store.ids):,} chunks loaded in {time.time() - started:.0f}s", flush=True)
    t = time.time()
    bm25 = BM25Index(store)
    print(f"BM25 built in {time.time() - t:.0f}s", flush=True)
    reranker = None
    if "hybrid_rerank" in args.systems:
        reranker = CrossEncoderReranker(cfg.rerank_model, cfg.rerank_max_length, cfg.rerank_text_chars, CACHE)
        print(f"reranker cache: {len(reranker.cache):,} pairs", flush=True)

    results: dict[str, dict] = {}
    snippets: dict[str, str] = {}
    for i, q in enumerate(queries, 1):
        text = to_precomposed(q.query) if args.query_form == "precomposed" else q.query
        t = time.time()
        runs = run_query(q, text, args.systems, collection, store, bm25, reranker, cfg)
        row = {}
        for name, (ranked, seconds) in runs.items():
            cases = collapse_to_cases(ranked)
            top_chunks, top_cases = ranked[: cfg.k], cases[: cfg.k]
            for c in top_cases:
                snippets[c.chunk_id] = store.docs[store.index_of[c.chunk_id]][:SNIPPET_CHARS]
            row[name] = {
                "chunks": [[c.chunk_id, c.case_key, round(c.score, 6)] for c in ranked[: cfg.fetch_depth]],
                "seconds": round(seconds, 3),
                "term_hit_top_chunks": term_flags(top_chunks, store, q.key_terms),
                "term_hit_top_cases": term_flags(top_cases, store, q.key_terms),
            }
        results[q.id] = row
        print(f"[{i}/{len(queries)}] {q.id} {q.group} {q.language} {time.time() - t:.1f}s", flush=True)

    out = {
        "collection": args.collection,
        "query_form": args.query_form,
        "systems": args.systems,
        "config": cfg.model_dump(),
        "queries_sha256": hashlib.sha256(QUERIES.read_bytes()).hexdigest(),
        "seconds_total": round(time.time() - started, 1),
        "case_chunks": dict(Counter(case_key(m["filename"]) for m in store.metas)),
        "snippets": snippets,
        "results": results,
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    tmp.replace(path)
    print(f"RUN_DONE {path} in {out['seconds_total']}s", flush=True)


if __name__ == "__main__":
    main()
