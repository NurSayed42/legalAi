"""
evals/run_experiment.py
=======================
Phase 3 এর পরীক্ষা, পরিকল্পনা evals/experiments/phase3_plan.json এ। Benchmark আর label প্রস্তুত না হলে --run থেমে যায় (D-209)।
Collection এ শুধু read, ফল evals/reports/phase3_<id>.json এ। কোনো default বা production কোড বদলায় না।

    python evals/run_experiment.py --list
    python evals/run_experiment.py --status
    python evals/run_experiment.py --plan E3
    python evals/run_experiment.py --run E4
"""

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.experiment_gate import readiness  # noqa: E402
from evals.metrics.retrieval import holm_adjust, judged_at_k, ndcg_at_k, paired_bootstrap_ci, sign_flip_p  # noqa: E402
from evals.pipeline_env import guard_db_path  # noqa: E402
from evals.retrieval_schema import (  # noqa: E402
    RetrievalEvalConfig,
    apply_human_decisions,
    grades_by_need,
    load_decisions,
    load_qrels,
    load_queries,
)
from retrieval.hybrid import RankedChunk, collapse_to_cases, rrf_chunks  # noqa: E402

PLAN = ROOT / "evals" / "experiments" / "phase3_plan.json"
GOLDEN = ROOT / "evals" / "golden"
RUNS = ROOT / "evals" / "runs"
REPORTS = ROOT / "evals" / "reports"


def load_plan(path: Path = PLAN) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    ids = [e["id"] for e in plan["experiments"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate experiment id")
    return plan


def ranked_from(row: dict) -> list[RankedChunk]:
    return [RankedChunk(chunk_id=c, filename=key, case_key=key, score=s) for c, key, s in row["chunks"]]


def top_cases(ranked: list[RankedChunk], k: int) -> list[str]:
    return [c.case_key for c in collapse_to_cases(ranked)[:k]]


def compare_variants(variants: dict[str, dict[str, list[str]]], baseline: dict[str, list[str]], queries, grades: dict, cfg: RetrievalEvalConfig) -> list[dict]:
    """প্রতিটা variant প্রতিটা ভাষায় baseline এর সাথে। Holm এই পরীক্ষার সব তুলনা জুড়ে। Unjudged বেশি হলে রায় নেই।"""
    rows, raw = [], {}
    for name, lists in variants.items():
        for language in ("bn", "en", "mixed"):
            subset = [q for q in queries if q.language == language and q.group != "no_answer" and q.id in lists and q.id in baseline]
            if not subset:
                continue
            clusters = [q.need_id for q in subset]
            xs = [ndcg_at_k(lists[q.id], grades.get(q.need_id, {}), cfg.k) or 0.0 for q in subset]
            ys = [ndcg_at_k(baseline[q.id], grades.get(q.need_id, {}), cfg.k) or 0.0 for q in subset]
            diff, low, high = paired_bootstrap_ci(xs, ys, clusters, cfg.bootstrap_samples, cfg.seed)
            pooled = [q for q in subset if q.group != "known_item"]
            judged = sum(judged_at_k(lists[q.id], grades.get(q.need_id, {}), cfg.k) for q in pooled) / len(pooled) if pooled else 1.0
            label = f"{name} [{language}]"
            raw[label] = sign_flip_p(xs, ys, clusters, cfg.permutation_samples, cfg.seed)
            rows.append({"variant": name, "language": language, "queries": len(subset), "variant_ndcg": round(sum(xs) / len(xs), 3), "baseline_ndcg": round(sum(ys) / len(ys), 3), "diff": round(diff, 3),
                         "ci": [round(low, 3), round(high, 3)], "judged": round(judged, 3), "label": label})
    adjusted = holm_adjust(raw)
    for r in rows:
        r["p_holm"] = round(adjusted[r["label"]], 4)
        better = (r["ci"][0] > 0 or r["ci"][1] < 0) and r["p_holm"] < cfg.alpha
        verdict = ("better than baseline" if r["diff"] > 0 else "worse than baseline") if better else "no measured difference"
        r["verdict"] = f"needs grading: {r['judged']:.0%} of results graded" if r["judged"] < cfg.min_judged_for_claim else verdict
        del r["label"]
    return rows


def passage_hits(passages: list[str], terms: list[str]) -> bool:
    from evals.tools.corpus import normalize

    wanted = [normalize(t) for t in terms]
    return any(t in normalize(p) for p in passages for t in wanted)


def _load(args_collection_db: str):
    import chromadb

    from retrieval.hybrid import ChunkStore

    return ChunkStore.from_collection(chromadb.PersistentClient(path=str(guard_db_path(Path(args_collection_db)))).get_collection("bangladesh_judgements_v3"))


def _context(with_corrections: bool = True):
    run = json.loads((RUNS / "v3.json").read_text(encoding="utf-8"))
    lay = json.loads((RUNS / "lay_v3.json").read_text(encoding="utf-8"))
    cfg = RetrievalEvalConfig.model_validate(run["config"])
    queries = load_queries(GOLDEN / "retrieval_queries.jsonl") + load_queries(GOLDEN / "lay_queries.jsonl")
    qrels = load_qrels(GOLDEN / "retrieval_qrels.jsonl") + load_qrels(GOLDEN / "lay_qrels.jsonl")
    if with_corrections:
        qrels, _ = apply_human_decisions(qrels, load_decisions(GOLDEN / "human_corrections.jsonl"))
    results = {**run["results"], **lay["results"]}
    baseline = {q.id: top_cases(ranked_from(results[q.id]["bm25"]), cfg.k) for q in queries}
    return cfg, queries, grades_by_need(qrels), results, baseline


def run_e4(db: str) -> dict:
    from retrieval.hybrid import BM25Index
    from retrieval.normalize import make_tokenizer

    cfg, queries, grades, _, baseline = _context()
    store = _load(db)
    variants = {}
    for level in ("nfc", "nfc_digits", "nfc_digits_stem"):
        index = BM25Index(store, make_tokenizer(level))
        variants[level] = {q.id: top_cases(index.search(q.query, cfg.fetch_depth), cfg.k) for q in queries}
    return {"comparison": compare_variants(variants, baseline, queries, grades, cfg)}


def run_e3(db: str) -> dict:
    from evals.retrieval_run import CACHE, rerank_chunks
    from retrieval.rerank import CrossEncoderReranker

    cfg, queries, grades, results, baseline = _context()
    store = _load(db)
    reranker = CrossEncoderReranker(cfg.rerank_model, cfg.rerank_max_length, cfg.rerank_text_chars, CACHE)
    variants = {}
    for depth in (10, 20, 50):
        lists = {}
        for q in queries:
            lists[q.id] = top_cases(rerank_chunks(reranker, store, q.query, ranked_from(results[q.id]["bm25"]), depth), cfg.k)
        variants[f"bm25_rerank_{depth}"] = lists
    return {"comparison": compare_variants(variants, baseline, queries, grades, cfg)}


def run_e1(db: str, use_llm: bool) -> dict:
    from retrieval.hybrid import BM25Index

    cfg, queries, grades, results, baseline = _context()
    english_by_need = {q.need_id: q for q in queries if q.language == "en" and q.group != "known_item"}
    targets = [q for q in queries if q.language in ("bn", "mixed") and q.group != "no_answer"]
    variants: dict[str, dict[str, list[str]]] = {"hand_en": {}, "bn_plus_hand_en": {}}
    for q in targets:
        other = english_by_need.get(q.need_id)
        if other is None:
            continue
        original, english = ranked_from(results[q.id]["bm25"]), ranked_from(results[other.id]["bm25"])
        variants["hand_en"][q.id] = top_cases(english, cfg.k)
        variants["bn_plus_hand_en"][q.id] = top_cases(rrf_chunks([original, english], cfg.rrf_k), cfg.k)
    translations = {}
    if use_llm:
        from llm.client import LLMClient, load_prices, providers_from_env
        from retrieval.query_translation import translate_query

        client = LLMClient(providers_from_env(), cache_path=ROOT / "evals" / "cache" / "llm_cache.jsonl", log_path=ROOT / "evals" / "cache" / "llm_calls.jsonl", prices=load_prices())
        index = BM25Index(_load(db))
        variants["llm_en"], variants["bn_plus_llm_en"] = {}, {}
        for q in targets:
            english_text, _ = translate_query(client, q.query)
            translations[q.id] = english_text
            ranked = index.search(english_text, cfg.fetch_depth)
            variants["llm_en"][q.id] = top_cases(ranked, cfg.k)
            variants["bn_plus_llm_en"][q.id] = top_cases(rrf_chunks([ranked_from(results[q.id]["bm25"]), ranked], cfg.rrf_k), cfg.k)
    return {"comparison": compare_variants(variants, baseline, targets, grades, cfg), "translations": translations, "note": "hand_en is an upper bound: a person wrote those translations."}


def run_e5(db: str) -> dict:
    from collections import Counter

    from evals.tools.corpus import TEXT_DIR
    from retrieval.case_search import CaseTextIndex, limit_per_case

    cfg, queries, grades, results, baseline = _context()
    pooled = [q for q in queries if q.group not in ("known_item", "no_answer")]
    chunk_limits = {}
    for n in (1, 2, 3):
        slots = []
        for q in pooled:
            top = limit_per_case(ranked_from(results[q.id]["bm25"]), n)[: cfg.k]
            slots.append(Counter(c.case_key for c in top).most_common(1)[0][1] if top else 0)
        chunk_limits[f"limit_{n}"] = {"mean_most_slots_one_case": round(sum(slots) / len(slots), 2), "worst": max(slots)}
    full = "\f".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(TEXT_DIR.glob("769454_DeathRef58of2013_*.txt")))
    index = CaseTextIndex(full)
    store = _load(db)
    rows = []
    for q in [q for q in queries if q.group == "dr58"]:
        indexed = [store.docs[store.index_of[c[0]]] for c in results[q.id]["bm25"]["chunks"] if c[1] == "death_reference_58_of_2013"][:5]
        found = index.search(q.query, 5)
        rows.append({"query": q.query, "language": q.language, "term_in_top5_indexed": passage_hits(indexed, q.key_terms), "term_in_top5_full_text": passage_hits([p.text for p in found], q.key_terms)})
    return {"chunk_limits": chunk_limits, "passage_search": rows}


def e3_cached_share(run: dict, depths=(10, 20, 50)) -> dict:
    """BM25 এর কতগুলো chunk আগেই reranker দেখেছে (hybrid এর উপরের ৫০ থেকে)। মডেল ছাড়াই খরচের হিসাব।"""
    out = {}
    for depth in depths:
        total = cached = 0
        for row in run["results"].values():
            seen = {c[0] for c in row["hybrid_rerank"]["chunks"] if c[2] >= 0}
            ids = [c[0] for c in row["bm25"]["chunks"][:depth]]
            total += len(ids)
            cached += sum(i in seen for i in ids)
        out[f"depth_{depth}"] = {"pairs": total, "already_cached": cached, "new_pairs": total - cached, "estimated_minutes_at_0.7s": round((total - cached) * 0.7 / 60, 1)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan")
    ap.add_argument("--run")
    ap.add_argument("--llm", action="store_true", help="E1: সত্যিকার LLM অনুবাদও চালায়")
    ap.add_argument("--db", default=str(ROOT / "evals" / "cache" / "chroma_eval_copy"))
    args = ap.parse_args()
    plan = load_plan()
    by_id = {e["id"]: e for e in plan["experiments"]}
    if args.list:
        for e in plan["experiments"]:
            print(f"{e['id']:4} {'expensive' if e['expensive'] else 'cheap':10} {e['name']} | runner: {e['runner']}")
    if args.status:
        print(json.dumps(readiness(), ensure_ascii=False, indent=1))
    if args.plan:
        print(json.dumps(by_id[args.plan], ensure_ascii=False, indent=1))
        if args.plan == "E3" and (RUNS / "v3.json").exists():
            print(json.dumps(e3_cached_share(json.loads((RUNS / "v3.json").read_text(encoding="utf-8"))), indent=1))
    if args.run:
        gate = readiness()
        if not gate["ready"]:
            print("Not ready, so nothing was run:")
            print("\n".join(f"- {u}" for u in gate["unmet"]))
            raise SystemExit(3)
        runners = {"E1": lambda: run_e1(args.db, args.llm), "E3": lambda: run_e3(args.db), "E4": lambda: run_e4(args.db), "E5": lambda: run_e5(args.db)}
        if args.run not in runners:
            raise SystemExit(f"{args.run} has no runner here. See `--plan {args.run}`.")
        report = {"experiment": by_id[args.run], "decision_rule": plan["decision_rule"], "results": runners[args.run]()}
        out = REPORTS / f"phase3_{args.run}.json"
        out.write_bytes((json.dumps(report, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
