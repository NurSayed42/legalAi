"""
evals/retrieval_explore.py
==========================
D-201 এর ৮ টা তুলনার বাইরের অনুসন্ধান। এখান থেকে কোনো দাবি প্রমাণ হয় না, শুধু ফলাফল কতটা টেকসই আর Phase 3 এ কী পরীক্ষা
করা উচিত সেটা বোঝা যায়। Collection এ শুধু read।

    python evals/retrieval_explore.py --out evals/reports/retrieval_v3_exploratory.json
"""

import argparse
import json
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.metrics.retrieval import judged_at_k, ndcg_at_k, paired_bootstrap_ci, sign_flip_p  # noqa: E402
from evals.retrieval_schema import RetrievalEvalConfig, grades_by_need, load_qrels, load_queries  # noqa: E402
from evals.retrieval_score import DR58  # noqa: E402
from evals.tools.corpus import TEXT_DIR, normalize  # noqa: E402
from retrieval.hybrid import ChunkStore, RankedChunk, collapse_to_cases, rrf_chunks, tokenize  # noqa: E402

GOLDEN = ROOT / "evals" / "golden"
RUNS = ROOT / "evals" / "runs"


def ranked_from(row: dict) -> list[RankedChunk]:
    return [RankedChunk(chunk_id=c, filename=key, case_key=key, score=s) for c, key, s in row["chunks"]]


def cases_of(ranked: list[RankedChunk], k: int) -> list[str]:
    return [c.case_key for c in collapse_to_cases(ranked)[:k]]


def ndcg_table(lists: dict[str, list[str]], queries, grades: dict, k: int) -> dict:
    out = {}
    for language in ("bn", "en"):
        subset = [q for q in queries if q.language == language and q.group != "no_answer" and q.id in lists]
        vals = [ndcg_at_k(lists[q.id], grades.get(q.need_id, {}), k) or 0.0 for q in subset]
        pooled = [q for q in subset if q.group != "known_item"]
        judged = [judged_at_k(lists[q.id], grades.get(q.need_id, {}), k) for q in pooled]
        out[language] = {"ndcg": round(sum(vals) / len(vals), 3), "judged_pooled_groups": round(sum(judged) / len(judged), 3) if judged else None, "queries": len(vals)}
    return out


def undeclared_pairs(run: dict, queries, grades: dict, cfg: RetrievalEvalConfig) -> list[dict]:
    rows = []
    for a, b in [("hybrid_rerank", "bm25"), ("hybrid_api", "hybrid"), ("hybrid_api", "bm25")]:
        for language in ("bn", "en"):
            subset = [q for q in queries if q.language == language and q.group != "no_answer"]
            xs, ys = [], []
            for q in subset:
                g = grades.get(q.need_id, {})
                xs.append(ndcg_at_k(cases_of(ranked_from(run["results"][q.id][a]), cfg.k), g, cfg.k) or 0.0)
                ys.append(ndcg_at_k(cases_of(ranked_from(run["results"][q.id][b]), cfg.k), g, cfg.k) or 0.0)
            clusters = [q.need_id for q in subset]
            diff, low, high = paired_bootstrap_ci(xs, ys, clusters, cfg.bootstrap_samples, cfg.seed)
            rows.append({"a": a, "b": b, "language": language, "mean_a": round(sum(xs) / len(xs), 3), "mean_b": round(sum(ys) / len(ys), 3), "diff": round(diff, 3), "ci": [round(low, 3), round(high, 3)],
                         "p_unadjusted": round(sign_flip_p(xs, ys, clusters, cfg.permutation_samples, cfg.seed), 4), "wins": sum(x > y + 1e-9 for x, y in zip(xs, ys)), "losses": sum(y > x + 1e-9 for x, y in zip(xs, ys))})
    return rows


def group_pairs(run: dict, queries, grades: dict, cfg: RetrievalEvalConfig) -> list[dict]:
    rows = []
    for group in ("known_item", "legal_term", "topic", "dr58"):
        for language in ("bn", "en"):
            subset = [q for q in queries if q.group == group and q.language == language]
            xs = [ndcg_at_k(cases_of(ranked_from(run["results"][q.id]["hybrid_rerank"]), cfg.k), grades.get(q.need_id, {}), cfg.k) or 0.0 for q in subset]
            ys = [ndcg_at_k(cases_of(ranked_from(run["results"][q.id]["bm25"]), cfg.k), grades.get(q.need_id, {}), cfg.k) or 0.0 for q in subset]
            rows.append({"group": group, "language": language, "n": len(subset), "rerank": round(sum(xs) / len(xs), 3), "bm25": round(sum(ys) / len(ys), 3),
                         "rerank_wins": sum(x > y + 1e-9 for x, y in zip(xs, ys)), "bm25_wins": sum(y > x + 1e-9 for x, y in zip(xs, ys))})
    return rows


def fusion_sensitivity(run: dict, queries, grades: dict, cfg: RetrievalEvalConfig) -> list[dict]:
    """fusion_depth আর rrf_k আন্দাজ: hybrid কি শুধু আমার বেছে নেওয়া মানের জন্য খারাপ?"""
    rows = []
    for depth in (10, 25, 50, 100, 200):
        for k in (10, 60):
            lists = {}
            for q in queries:
                row = run["results"][q.id]
                lists[q.id] = cases_of(rrf_chunks([ranked_from(row["dense"])[:depth], ranked_from(row["bm25"])[:depth]], k), cfg.k)
            rows.append({"fusion_depth": depth, "rrf_k": k, **ndcg_table(lists, queries, grades, cfg.k)})
    return rows


def bilingual_bm25(run: dict, queries, grades: dict, cfg: RetrievalEvalConfig) -> dict:
    """প্রতিটা বাংলা প্রশ্নের সাথে তার হাতে লেখা ইংরেজি রূপের BM25 মেলানো: LLM অনুবাদ কতটা সাহায্য করতে পারে তার উপরের সীমা, পরিমাপ নয়।"""
    by_need: dict[str, dict] = {}
    for q in queries:
        if q.group in ("topic", "legal_term"):
            by_need.setdefault(q.need_id, {})[q.language] = q
    out = {}
    for group in ("topic", "legal_term"):
        bn_only, en_only, both, judged = [], [], [], []
        for need, forms in by_need.items():
            if forms["bn"].group != group:
                continue
            g = grades.get(need, {})
            bn_rank = ranked_from(run["results"][forms["bn"].id]["bm25"])
            en_rank = ranked_from(run["results"][forms["en"].id]["bm25"])
            bn_only.append(ndcg_at_k(cases_of(bn_rank, cfg.k), g, cfg.k) or 0.0)
            en_only.append(ndcg_at_k(cases_of(en_rank, cfg.k), g, cfg.k) or 0.0)
            merged = cases_of(rrf_chunks([bn_rank, en_rank], cfg.rrf_k), cfg.k)
            both.append(ndcg_at_k(merged, g, cfg.k) or 0.0)
            judged.append(judged_at_k(merged, g, cfg.k))
        out[group] = {"needs": len(both), "bangla_query_only": round(statistics.mean(bn_only), 3), "english_query_only": round(statistics.mean(en_only), 3), "both_fused": round(statistics.mean(both), 3), "judged": round(statistics.mean(judged), 3)}
    return out


def flip_sensitivity(run: dict, queries, grades: dict, cfg: RetrievalEvalConfig) -> list[dict]:
    """গ্রেডিং BM25 এর পক্ষে ঝুঁকে থাকলে? শুধু dense এর আনা (BM25 আনেনি) 0 গ্রেডের ফলের এই ভাগ আসলে প্রাসঙ্গিক ধরে হিসাব।"""
    rows = []
    answerable = [q for q in queries if q.group in ("legal_term", "topic", "dr58")]
    base_lists = {q.id: {s: cases_of(ranked_from(run["results"][q.id][s]), cfg.k) for s in ("dense", "bm25", "hybrid_rerank")} for q in answerable}
    candidates = set()
    for q in answerable:
        for case in set(base_lists[q.id]["dense"]) - set(base_lists[q.id]["bm25"]):
            if grades.get(q.need_id, {}).get(case, 0) == 0:
                candidates.add((q.need_id, case))
    for p in (0.1, 0.3, 0.5, 1.0):
        rng = random.Random(cfg.seed)
        diffs = {"bn": [], "en": []}
        for _ in range(200 if p < 1 else 1):
            flipped = {(n, c) for n, c in candidates if rng.random() < p}
            for language in ("bn", "en"):
                xs = []
                for q in [q for q in answerable if q.language == language]:
                    g = dict(grades.get(q.need_id, {}))
                    for n, c in flipped:
                        if n == q.need_id:
                            g[c] = 1
                    xs.append((ndcg_at_k(base_lists[q.id]["bm25"], g, cfg.k) or 0.0) - (ndcg_at_k(base_lists[q.id]["dense"], g, cfg.k) or 0.0))
                diffs[language].append(sum(xs) / len(xs))
        rows.append({"p": p, "dense_only_zero_results": len(candidates), "bm25_minus_dense_bn_mean": round(statistics.mean(diffs["bn"]), 3), "bm25_minus_dense_bn_min": round(min(diffs["bn"]), 3),
                     "bm25_minus_dense_en_mean": round(statistics.mean(diffs["en"]), 3), "bm25_minus_dense_en_min": round(min(diffs["en"]), 3)})
    return rows


def chunk_length(run: dict, queries, store: ChunkStore, grades: dict, cfg: RetrievalEvalConfig) -> dict:
    """ছোট chunk (স্বাক্ষরের লাইন) কি dense এর ভুল ফলের কারণ? আর সেগুলো সরালে কী হয়?"""
    words = {cid: len(doc.split()) for cid, doc in zip(store.ids, store.docs)}
    total = len(words)
    out = {"corpus": {"under_50_words": round(sum(v < 50 for v in words.values()) / total, 4), "under_100_words": round(sum(v < 100 for v in words.values()) / total, 4), "median": statistics.median(words.values())}}
    subset = [q for q in queries if q.group in ("legal_term", "topic", "no_answer")]
    for system in ("dense", "bm25", "hybrid_rerank"):
        for language in ("bn", "en"):
            tops = [c[0] for q in subset if q.language == language for c in run["results"][q.id][system]["chunks"][: cfg.k]]
            out[f"{system}_{language}"] = {"under_50_words": round(sum(words[c] < 50 for c in tops) / len(tops), 3), "under_100_words": round(sum(words[c] < 100 for c in tops) / len(tops), 3), "median_words": statistics.median(words[c] for c in tops)}
    for min_words in (50, 100, 200):
        lists = {}
        for q in queries:
            keep = [c for c in ranked_from(run["results"][q.id]["dense"]) if words[c.chunk_id] >= min_words]
            lists[q.id] = cases_of(keep, cfg.k)
        out[f"dense_without_chunks_under_{min_words}_words"] = ndcg_table(lists, queries, grades, cfg.k)
    return out


def dr58_detail(queries, run: dict, store: ChunkStore, cfg: RetrievalEvalConfig) -> dict:
    full_text = {}
    for path in sorted(TEXT_DIR.glob("769454_DeathRef58of2013_*.txt")):
        full_text[path.stem] = path.read_text(encoding="utf-8", errors="replace")
    full = normalize("\f".join(full_text.values()))
    indexed_docs = [normalize(d) for d, m in zip(store.docs, store.metas) if m["filename"].startswith("769454_DeathRef58of2013_")]
    indexed = "\n".join(indexed_docs)
    terms = {}
    for q in queries:
        if q.group == "dr58":
            for t in q.key_terms:
                t = normalize(t)
                terms[t] = {"mentions_full_text": full.count(t), "mentions_in_indexed_chunks": indexed.count(t)}
    full_tokens = Counter(t for t in tokenize(full) if len(t) >= 5 and not t.isdigit())
    rare = sorted(t for t, n in full_tokens.items() if n <= 3)
    sample = random.Random(cfg.seed).sample(rare, min(3000, len(rare)))
    indexed_tokens = set(tokenize(indexed))
    hits = {}
    for q in queries:
        if q.group == "dr58":
            hits[q.query] = {s: {"term_in_top10_chunks": sum(run["results"][q.id][s]["term_hit_top_chunks"]), "dr58_slots_top10": [c[1] for c in run["results"][q.id][s]["chunks"][:10]].count(DR58)} for s in ("dense", "bm25", "hybrid_rerank")}
    return {"indexed_chunks": len(indexed_docs), "full_chars": len(full), "indexed_chars_with_overlap": len(indexed), "terms": terms, "rare_tokens_sampled": len(sample),
            "rare_tokens_found_in_index": round(sum(t in indexed_tokens for t in sample) / len(sample), 3), "per_query": hits}


def precomposed_effect(queries, grades: dict, cfg: RetrievalEvalConfig) -> dict:
    path = RUNS / "v3_precomposed.json"
    base = json.loads((RUNS / "v3.json").read_text(encoding="utf-8"))
    alt = json.loads(path.read_text(encoding="utf-8"))
    bn = [q for q in queries if q.language == "bn" and q.group != "no_answer"]
    affected = [q for q in bn if any(x in q.query for x in ("য়", "ড়", "ঢ়"))]
    rows = {}
    for system in ("dense", "bm25"):
        a, b, changed, judged = [], [], 0, []
        for q in affected:
            g = grades.get(q.need_id, {})
            base_list = cases_of(ranked_from(base["results"][q.id][system]), cfg.k)
            alt_list = cases_of(ranked_from(alt["results"][q.id][system]), cfg.k)
            a.append(ndcg_at_k(base_list, g, cfg.k) or 0.0)
            b.append(ndcg_at_k(alt_list, g, cfg.k) or 0.0)
            changed += base_list != alt_list
            if q.group != "known_item":
                judged.append(judged_at_k(alt_list, g, cfg.k))
        rows[system] = {"ndcg_decomposed": round(statistics.mean(a), 3), "ndcg_precomposed": round(statistics.mean(b), 3), "queries_with_changed_top10": changed, "judged_after": round(statistics.mean(judged), 3) if judged else None}
    return {"bangla_queries": len(bn), "queries_containing_these_letters": len(affected), **rows}


def main() -> None:
    import chromadb

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "evals" / "reports" / "retrieval_v3_exploratory.json"))
    args = ap.parse_args()
    run = json.loads((RUNS / "v3.json").read_text(encoding="utf-8"))
    cfg = RetrievalEvalConfig.model_validate(run["config"])
    queries = load_queries(GOLDEN / "retrieval_queries.jsonl")
    grades = grades_by_need(load_qrels(GOLDEN / "retrieval_qrels.jsonl"))
    client = chromadb.PersistentClient(path=str(ROOT / "legal_chroma_db"))
    store = ChunkStore.from_collection(client.get_collection("bangladesh_judgements_v3"))
    report = {
        "note": "Exploratory, outside the 8 comparisons declared in D-201. p-values are unadjusted and support no claim.",
        "undeclared_pairs": undeclared_pairs(run, queries, grades, cfg),
        "rerank_vs_bm25_by_group": group_pairs(run, queries, grades, cfg),
        "fusion_sensitivity": fusion_sensitivity(run, queries, grades, cfg),
        "bilingual_bm25": bilingual_bm25(run, queries, grades, cfg),
        "label_flip_sensitivity": flip_sensitivity(run, queries, grades, cfg),
        "chunk_length": chunk_length(run, queries, store, grades, cfg),
        "dr58": dr58_detail(queries, run, store, cfg),
        "precomposed": precomposed_effect(queries, grades, cfg),
    }
    out = Path(args.out)
    out.write_bytes((json.dumps(report, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main()
