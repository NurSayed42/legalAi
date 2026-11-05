"""
evals/retrieval_score.py
========================
Run file আর qrels থেকে সব মাপ: metric, পূর্ব-ঘোষিত তুলনা (D-201), Death Ref 58, domination, exact match, false
similarity। ফল JSON আর markdown table এ, হাতে সংখ্যা কপি করতে হয় না। Model বা Chroma লাগে না।

    python evals/retrieval_score.py --out evals/reports/retrieval_v3
"""

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.metrics.retrieval import (  # noqa: E402
    auc,
    capped_recall_at_k,
    domination,
    holm_adjust,
    judged_at_k,
    ndcg_at_k,
    paired_bootstrap_ci,
    precision_at_k,
    recall_at_k,
    rr_at_k,
    sign_flip_p,
)
from evals.retrieval_schema import (  # noqa: E402
    BenchmarkQuery,
    RetrievalEvalConfig,
    apply_human_decisions,
    grades_by_need,
    load_decisions,
    load_qrels,
    load_queries,
)
from evals.tools.corpus import doc_script, load_corpus  # noqa: E402

GOLDEN = ROOT / "evals" / "golden"
DR58 = "death_reference_58_of_2013"
HEADLINE = ["dense", "bm25", "hybrid", "hybrid_rerank"]
# D-201: এই ৮ টাই পরীক্ষা, অন্য সব বর্ণনামূলক
DECLARED = [("bm25", "dense"), ("hybrid", "dense"), ("hybrid", "bm25"), ("hybrid_rerank", "hybrid")]
METRICS = ["recall", "capped_recall", "precision", "mrr", "ndcg", "strict_precision", "strict_mrr", "judged"]


def case_list(chunks: list[list]) -> list[list]:
    seen, out = set(), []
    for chunk_id, key, score in chunks:
        if key not in seen:
            seen.add(key)
            out.append([chunk_id, key, score])
    return out


def query_metrics(cases: list[str], grades: dict[str, int], cfg: RetrievalEvalConfig) -> dict:
    k = cfg.k
    return {
        "recall": recall_at_k(cases, grades, k, cfg.relevant_threshold),
        "capped_recall": capped_recall_at_k(cases, grades, k, cfg.relevant_threshold),
        "precision": precision_at_k(cases, grades, k, cfg.relevant_threshold) if grades else None,
        "mrr": rr_at_k(cases, grades, k, cfg.relevant_threshold),
        "ndcg": ndcg_at_k(cases, grades, k),
        "strict_precision": precision_at_k(cases, grades, k, cfg.strict_threshold) if grades else None,
        "strict_mrr": rr_at_k(cases, grades, k, cfg.strict_threshold),
        "judged": judged_at_k(cases, grades, k),
    }


def mean(values: list[float | None]) -> float | None:
    got = [v for v in values if v is not None]
    return sum(got) / len(got) if got else None


def score_run(run: dict, queries: list[BenchmarkQuery], grades: dict[str, dict[str, int]], cfg: RetrievalEvalConfig) -> dict:
    per_query: dict[str, dict] = {}
    for q in queries:
        if q.id not in run["results"]:
            continue
        rows = {}
        for system, row in run["results"][q.id].items():
            cases = case_list(row["chunks"])
            chunk_cases = [c[1] for c in row["chunks"]]
            keys = [c[1] for c in cases]
            need_grades = grades.get(q.need_id, {})
            rows[system] = {
                **query_metrics(keys, need_grades, cfg),
                **domination(chunk_cases, cfg.k),
                "returned_cases": len(keys),
                "returned_chunks": len(chunk_cases),
                "top_score": cases[0][2] if cases else None,
                "dr58_rank": keys.index(DR58) + 1 if DR58 in keys else None,
                "dr58_chunk_slots": chunk_cases[: cfg.k].count(DR58),
                "top_cases": [{"case": key, "score": score, "chunk": cid, "grade": need_grades.get(key)} for cid, key, score in cases[: cfg.k]],
                "seconds": row["seconds"],
                "term_hit_top_chunks": sum(row["term_hit_top_chunks"]),
                "term_hit_top_cases": sum(row["term_hit_top_cases"]),
            }
        per_query[q.id] = rows
    return per_query


def aggregate(per_query: dict, queries: list[BenchmarkQuery], systems: list[str], keep) -> dict:
    subset = [q for q in queries if q.id in per_query and keep(q)]
    out = {"n_queries": len(subset), "n_needs": len({q.need_id for q in subset})}
    for system in systems:
        rows = [per_query[q.id][system] for q in subset if system in per_query[q.id]]
        out[system] = {m: mean([r[m] for r in rows]) for m in METRICS}
    return out


def declared_tests(per_query: dict, queries: list[BenchmarkQuery], cfg: RetrievalEvalConfig) -> list[dict]:
    rows, raw = [], {}
    for language in ("bn", "en"):
        subset = [q for q in queries if q.language == language and q.group != "no_answer" and q.id in per_query]
        clusters = [q.need_id for q in subset]
        for a, b in DECLARED:
            xs = [per_query[q.id][a][cfg.primary_metric] or 0.0 for q in subset]
            ys = [per_query[q.id][b][cfg.primary_metric] or 0.0 for q in subset]
            diff, low, high = paired_bootstrap_ci(xs, ys, clusters, cfg.bootstrap_samples, cfg.seed)
            pooled = [q for q in subset if q.group != "known_item"]  # known-item এ লক্ষ্য জানা, বাকি ফল বিচার করার দরকার নেই
            judged = min((mean([per_query[q.id][s]["judged"] for q in pooled]) or 0.0 for s in (a, b)), default=0.0) if pooled else 1.0
            p = sign_flip_p(xs, ys, clusters, cfg.permutation_samples, cfg.seed)
            name = f"{a} vs {b} [{language}]"
            raw[name] = p
            wins = sum(x > y + 1e-9 for x, y in zip(xs, ys))
            losses = sum(y > x + 1e-9 for x, y in zip(xs, ys))
            rows.append({"name": name, "a": a, "b": b, "language": language, "n_queries": len(subset), "n_needs": len(set(clusters)), "mean_a": sum(xs) / len(xs), "mean_b": sum(ys) / len(ys),
                         "diff": diff, "ci_low": low, "ci_high": high, "p": p, "wins": wins, "losses": losses, "ties": len(subset) - wins - losses, "judged": judged})
    adjusted = holm_adjust(raw)
    for r in rows:
        r["p_holm"] = adjusted[r["name"]]
        excludes_zero = r["ci_low"] > 0 or r["ci_high"] < 0
        confirmed = excludes_zero and r["p_holm"] < cfg.alpha
        r["verdict"] = (f"{r['a']} better" if r["diff"] > 0 else f"{r['b']} better") if confirmed else "no measured difference"
        if r["judged"] < cfg.min_judged_for_claim:
            # D-206: অবিচারিত ফল miss হিসেবে গোনা হয়, তাই পুরো বিচার না হলে জয়-পরাজয় বলা যায় না
            r["verdict"] = f"not ready: only {r['judged']:.0%} of results graded"
    return rows


def false_similarity(per_query: dict, queries: list[BenchmarkQuery], cfg: RetrievalEvalConfig, snippets: dict) -> dict:
    """Score আর প্রাসঙ্গিকতা কতটা মেলে। শুধু বিচার করা ফল গোনা হয়, known-item বাদ: সেখানে বাকি সব অবিচারিত।"""
    out = {}
    for system, label in (("dense", "cosine"), ("hybrid_rerank", "reranker"), ("bm25", "bm25")):
        pos, neg, by_lang = [], [], defaultdict(lambda: ([], []))
        worst = []
        for q in queries:
            if q.id not in per_query or system not in per_query[q.id] or q.group not in ("legal_term", "topic", "dr58", "lay"):
                continue
            for r in per_query[q.id][system]["top_cases"]:
                if r["grade"] is None or r["score"] < 0:
                    continue
                (pos if r["grade"] >= 1 else neg).append(r["score"])
                by_lang[q.language][0 if r["grade"] >= 1 else 1].append(r["score"])
                if r["grade"] == 0:
                    worst.append({"query": q.query, "language": q.language, "score": r["score"], "case": r["case"], "snippet": " ".join(snippets.get(r["chunk"], "").split())[:220]})
        no_answer = [per_query[q.id][system]["top_score"] for q in queries if q.group == "no_answer" and q.id in per_query and system in per_query[q.id]]
        answerable = [per_query[q.id][system]["top_score"] for q in queries if q.group in ("legal_term", "topic", "dr58", "lay") and q.id in per_query and system in per_query[q.id]]
        na_by_lang = {lang: [per_query[q.id][system]["top_score"] for q in queries if q.group == "no_answer" and q.language == lang and q.id in per_query and system in per_query[q.id]] for lang in ("bn", "en")}
        thresholds = cfg.similarity_thresholds if system == "dense" else ([cfg.rerank_api_threshold, 0.1, 0.3, 0.5, 0.7, 0.9] if system == "hybrid_rerank" else [])
        table = []
        for t in thresholds:
            p, n = sum(s >= t for s in pos), sum(s >= t for s in neg)
            passed_na = sum((s or 0) >= t for s in no_answer)
            table.append({"threshold": t, "relevant_at_or_above": p, "not_relevant_at_or_above": n, "precision": p / (p + n) if p + n else None, "relevant_lost": (len(pos) - p) / len(pos) if pos else None,
                          "no_answer_queries_passing": passed_na, "no_answer_queries": len(no_answer)})
        out[system] = {
            "score": label, "n_relevant": len(pos), "n_not_relevant": len(neg), "auc": auc(pos, neg),
            "auc_bn": auc(*by_lang["bn"]), "auc_en": auc(*by_lang["en"]),
            "median_relevant": statistics.median(pos) if pos else None, "median_not_relevant": statistics.median(neg) if neg else None,
            "max_not_relevant": max(neg) if neg else None, "thresholds": table,
            "no_answer_top": {"median": statistics.median([x for x in no_answer if x is not None]) if no_answer else None, "max": max([x for x in no_answer if x is not None], default=None), "by_language": na_by_lang},
            "answerable_top_median": statistics.median([x for x in answerable if x is not None]) if answerable else None,
            "worst_examples": sorted(worst, key=lambda w: -w["score"])[:6],
        }
    return out


def domination_table(per_query: dict, queries: list[BenchmarkQuery], systems: list[str], run: dict, cfg: RetrievalEvalConfig) -> dict:
    sizes = run["case_chunks"]
    ordered = sorted(sizes.values(), reverse=True)
    n_large = max(1, int(len(ordered) * cfg.large_case_top_share))
    cutoff = ordered[n_large - 1]
    large = {c for c, n in sizes.items() if n >= cutoff}
    total = sum(ordered)
    baseline = {"cutoff_chunks": cutoff, "large_cases": len(large), "share_of_cases": len(large) / len(sizes), "share_of_chunks": sum(sizes[c] for c in large) / total}
    out = {"baseline": baseline, "systems": {}}
    pool = [q for q in queries if q.id in per_query and q.group != "known_item"]
    for system in systems:
        rows = []
        for q in pool:
            if system in per_query[q.id]:
                r = per_query[q.id][system]
                rows.append((q, r))
        if not rows:
            continue
        hubs = Counter()
        hub_rel = Counter()
        slots_large, slots_total, slots_nonrel_large, slots_nonrel = 0, 0, 0, 0
        dr58_slots_other = 0
        for q, r in rows:
            for item in r["top_cases"]:
                slots_total += 1
                is_large = item["case"] in large
                slots_large += is_large
                hubs[item["case"]] += 1
                if item["grade"] is not None and item["grade"] >= 1:
                    hub_rel[item["case"]] += 1
                else:
                    slots_nonrel += 1
                    slots_nonrel_large += is_large
            if q.group != "dr58":
                dr58_slots_other += r["dr58_chunk_slots"]
        other = [r for q, r in rows if q.group != "dr58"]
        by_lang = {}
        for language in ("bn", "en"):
            # Death Ref 58 এর নিজের প্রশ্নে সে জায়গা নিলে সেটা দখল নয়
            sub = [r for q, r in rows if q.language == language and q.group != "dr58"]
            by_lang[language] = {"mean_max_slots": mean([r["max_slots_one_case"] for r in sub]), "worst_max_slots": max((r["max_slots_one_case"] for r in sub), default=0), "mean_distinct_cases_in_top_chunks": mean([r["distinct_cases"] for r in sub])}
        out["systems"][system] = {
            "queries": len(rows),
            "mean_max_slots": mean([r["max_slots_one_case"] for r in other]),
            "worst_max_slots": max((r["max_slots_one_case"] for r in other), default=0),
            "mean_distinct_cases_in_top_chunks": mean([r["distinct_cases"] for r in other]),
            "by_language": by_lang,
            "large_case_share_of_case_slots": slots_large / slots_total if slots_total else None,
            "large_case_share_of_nonrelevant_slots": slots_nonrel_large / slots_nonrel if slots_nonrel else None,
            "dr58_chunk_slots_on_other_queries": dr58_slots_other,
            "other_query_count": len(other),
            "hubs": [{"case": c, "queries": n, "relevant_in": hub_rel[c], "chunks": sizes.get(c)} for c, n in hubs.most_common(5)],
        }
    return out


def dr58_coverage(queries: list[BenchmarkQuery], per_query: dict, systems: list[str], corpus: dict[str, str] | None, run: dict) -> list[dict]:
    rows = []
    for q in queries:
        if q.group != "dr58" or q.id not in per_query:
            continue
        rows.append({"id": q.id, "need": q.need_id, "language": q.language, "query": q.query,
                     "rank": {s: per_query[q.id][s]["dr58_rank"] for s in systems if s in per_query[q.id]},
                     "chunk_slots": {s: per_query[q.id][s]["dr58_chunk_slots"] for s in systems if s in per_query[q.id]}})
    return rows


def exact_match(per_query: dict, queries: list[BenchmarkQuery], systems: list[str], targets: dict, scripts: dict[str, str]) -> dict:
    """Known-item: ১ নম্বরে, top-10 এ, প্রশ্ন আর document এর ভাষার জোড়া ধরে। আর legal term: top-10 chunk এ শব্দটা আছে কিনা।"""
    buckets: dict[str, list[BenchmarkQuery]] = defaultdict(list)
    for q in queries:
        if q.group != "known_item" or q.id not in per_query:
            continue
        kind = "case number" if q.need_id.startswith("k-") else "party name"
        doc = scripts.get(targets[q.need_id][0], "?")
        doc = "English" if doc == "en" else ("mixed" if doc == "mixed" else "Bangla")
        lang = "Bangla" if q.language == "bn" else "English"
        buckets[f"{kind}: {lang} query, {doc} judgement"].append(q)
    known = {}
    for label, qs in sorted(buckets.items()):
        known[label] = {"queries": len(qs)}
        for s in systems:
            rows = [per_query[q.id][s] for q in qs if s in per_query[q.id]]
            if rows:
                known[label][s] = {"at_1": sum(r["mrr"] == 1.0 for r in rows), "at_10": sum((r["mrr"] or 0) > 0 for r in rows), "mrr": mean([r["mrr"] for r in rows])}
    terms = {}
    for language in ("bn", "en"):
        qs = [q for q in queries if q.group == "legal_term" and q.language == language and q.id in per_query]
        terms[language] = {s: {"term_in_top_chunks_of_100": 100 * mean([per_query[q.id][s]["term_hit_top_chunks"] / 10 for q in qs if s in per_query[q.id]]),
                               "term_in_top_case_chunks_of_100": 100 * mean([per_query[q.id][s]["term_hit_top_cases"] / 10 for q in qs if s in per_query[q.id]]),
                               "empty_queries": sum(per_query[q.id][s]["returned_chunks"] == 0 for q in qs if s in per_query[q.id])} for s in systems if any(s in per_query[q.id] for q in qs)}
    return {"known_item": known, "legal_term": terms}


def script_breakdown(per_query: dict, queries: list[BenchmarkQuery], systems: list[str], grades: dict, scripts: dict[str, str], cfg: RetrievalEvalConfig) -> dict:
    """বাংলা প্রশ্ন বাংলা রায় খুঁজে পায়, নাকি ইংরেজিও? Relevant মামলার script ধরে recall।"""
    out = {}
    for language in ("bn", "en"):
        out[language] = {}
        qs = [q for q in queries if q.language == language and q.group in ("topic", "legal_term", "lay") and q.id in per_query]
        for bucket in ("en", "mixed", "bn"):
            row = {}
            for s in systems:
                found, total = 0, 0
                for q in qs:
                    if s not in per_query[q.id]:
                        continue
                    wanted = {c for c, g in grades.get(q.need_id, {}).items() if g >= cfg.relevant_threshold and scripts.get(c) == bucket}
                    top = {x["case"] for x in per_query[q.id][s]["top_cases"]}
                    found += len(wanted & top)
                    total += len(wanted)
                row[s] = {"found_in_top10": found, "known_relevant": total}
            out[language][bucket] = row
    return out


def fmt(x, digits: int = 3) -> str:
    return "–" if x is None else (f"{x:.{digits}f}" if isinstance(x, float) else str(x))


def table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def tables_md(rep: dict, systems: list[str]) -> str:
    labels = rep["qrels"]
    out = [f"Run: `{rep['collection']}`, queries sha256 `{rep['queries_sha256'][:12]}`, qrels sha256 `{rep['qrels_sha256'][:12]}`.\n",
           f"Labels: {labels['human_verified']} of {labels['total']} reviewed by a person. Human corrections applied: {'yes' if labels['corrections_applied'] else 'no'}.\n"]
    for key, label in (("overall", "All queries with known relevant cases"), ("bn", "Bangla queries"), ("en", "English queries"), ("lay_bn", "Lay, Bangla"), ("lay_en", "Lay, English"), ("lay_mixed", "Lay, Banglish and mixed"), ("known_item_bn", "Known-item, Bangla"), ("known_item_en", "Known-item, English"),
                       ("legal_term_bn", "Legal term, Bangla"), ("legal_term_en", "Legal term, English"), ("topic_bn", "Topic, Bangla"), ("topic_en", "Topic, English"), ("dr58_bn", "Death Ref 58, Bangla"), ("dr58_en", "Death Ref 58, English")):
        block = rep["aggregates"][key]
        if not block["n_queries"]:
            continue
        out.append(f"\n### {label} ({block['n_queries']} queries, {block['n_needs']} needs)\n")
        out.append(table(["System", "Recall@10", "Capped recall", "Precision@10", "MRR@10", "nDCG@10", "Strict P@10", "Strict MRR", "Judged@10"],
                         [[s, *[fmt(block[s][m]) for m in ("recall", "capped_recall", "precision", "mrr", "ndcg", "strict_precision", "strict_mrr", "judged")]] for s in systems if s in block]))
    if rep.get("declared"):
        out.append("\n### Declared comparisons, nDCG@10 (D-201)\n")
        out.append(table(["Comparison", "Queries", "Mean A", "Mean B", "Difference", "95% CI", "p (Holm)", "Win/loss/tie", "Verdict"],
                         [[r["name"], r["n_queries"], fmt(r["mean_a"]), fmt(r["mean_b"]), f"{r['diff']:+.3f}", f"{r['ci_low']:+.3f} to {r['ci_high']:+.3f}", fmt(r["p_holm"]), f"{r['wins']}/{r['losses']}/{r['ties']}", r["verdict"]] for r in rep["declared"]]))
    out.append("\n### Domination, raw top 10 chunks, excluding known-item queries\n")
    dom = rep["domination"]
    out.append(f"Large case: at least {dom['baseline']['cutoff_chunks']} chunks, {dom['baseline']['large_cases']} cases, {dom['baseline']['share_of_cases']:.1%} of cases and {dom['baseline']['share_of_chunks']:.1%} of chunks.\n")
    out.append(table(["System", "Mean most slots, one case", "Worst", "Distinct cases in top 10 chunks", "Large-case share of top-10 cases", "Large-case share of non-relevant", "DR58 slots, other queries"],
                     [[s, fmt(v["mean_max_slots"], 2), v["worst_max_slots"], fmt(v["mean_distinct_cases_in_top_chunks"], 2), f"{v['large_case_share_of_case_slots']:.1%}", f"{v['large_case_share_of_nonrelevant_slots']:.1%}", f"{v['dr58_chunk_slots_on_other_queries']} of {v['other_query_count'] * 10}"] for s, v in dom["systems"].items()]))
    out.append("\n### Exact case and party lookup: found at rank 1 / in top 10\n")
    known = rep["exact_match"]["known_item"]
    out.append(table(["Kind", "Queries", *systems], [[label, row["queries"], *[f"{row[s]['at_1']} / {row[s]['at_10']}" if s in row else "–" for s in systems]] for label, row in known.items()]))
    out.append("\n### Death Ref 58: rank of the case, by query\n")
    out.append(table(["Query", "Language", *systems], [[r["query"], r["language"], *[fmt(r["rank"].get(s)) for s in systems]] for r in rep["dr58"]]))
    out.append("\n### Score as a relevance signal\n")
    fs = rep["false_similarity"]
    out.append(table(["System", "Score", "AUC", "AUC Bangla", "AUC English", "Median, relevant", "Median, not relevant", "Max, not relevant", "Top score, answerable (median)", "Top score, no-answer (median / max)"],
                     [[s, v["score"], fmt(v["auc"]), fmt(v["auc_bn"]), fmt(v["auc_en"]), fmt(v["median_relevant"]), fmt(v["median_not_relevant"]), fmt(v["max_not_relevant"]), fmt(v["answerable_top_median"]), f"{fmt(v['no_answer_top']['median'])} / {fmt(v['no_answer_top']['max'])}"] for s, v in fs.items()]))
    for s, v in fs.items():
        if v["thresholds"]:
            out.append(f"\n{s} thresholds:\n")
            out.append(table(["Score at or above", "Relevant", "Not relevant", "Precision", "Relevant lost", "No-answer queries passing"],
                             [[t["threshold"], t["relevant_at_or_above"], t["not_relevant_at_or_above"], fmt(t["precision"]), fmt(t["relevant_lost"]), f"{t['no_answer_queries_passing']} of {t['no_answer_queries']}"] for t in v["thresholds"]]))
    out.append("\n### Latency, seconds per query, CPU\n")
    out.append(table(["System", "Median", "p95"], [[s, fmt(v["median"], 2), fmt(v["p95"], 2)] for s, v in rep["latency"].items()]))
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "evals" / "runs" / "v3.json"))
    ap.add_argument("--qrels", default=str(GOLDEN / "retrieval_qrels.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "evals" / "reports" / "retrieval_v3"))
    ap.add_argument("--queries", default=str(GOLDEN / "retrieval_queries.jsonl"))
    ap.add_argument("--extra-qrels", nargs="*", default=[], help="যেমন evals/golden/lay_qrels.jsonl")
    ap.add_argument("--with-corrections", action="store_true", help="human_corrections.jsonl লাগায়; না দিলে Claude এর grade ই চলে (D-207)")
    args = ap.parse_args()

    run = json.loads(Path(args.runs).read_text(encoding="utf-8"))
    query_path = Path(args.queries)
    if hashlib.sha256(query_path.read_bytes()).hexdigest() != run["queries_sha256"]:
        raise SystemExit("queries changed since this run")
    cfg = RetrievalEvalConfig.model_validate(run["config"])
    queries = load_queries(query_path)
    qrels = load_qrels(Path(args.qrels))
    for extra in args.extra_qrels:
        qrels += load_qrels(Path(extra))
    correction_stats = None
    if args.with_corrections:
        qrels, correction_stats = apply_human_decisions(qrels, load_decisions(GOLDEN / "human_corrections.jsonl"))
    grades = grades_by_need(qrels)
    targets = json.loads((GOLDEN / "known_targets.json").read_text(encoding="utf-8"))
    corpus = load_corpus()
    scripts = {k: doc_script(v) for k, v in corpus.items()}
    systems = [s for s in ["dense", "bm25", "hybrid", "hybrid_rerank", "hybrid_api"] if s in run["systems"]]

    per_query = score_run(run, queries, grades, cfg)
    answerable = lambda q: q.group != "no_answer"  # noqa: E731
    aggregates = {
        "overall": aggregate(per_query, queries, systems, answerable),
        "bn": aggregate(per_query, queries, systems, lambda q: answerable(q) and q.language == "bn"),
        "en": aggregate(per_query, queries, systems, lambda q: answerable(q) and q.language == "en"),
        "unseen_only": aggregate(per_query, queries, systems, lambda q: answerable(q) and not q.seen_before),
    }
    for group in ("known_item", "legal_term", "topic", "dr58", "lay"):
        for language in ("bn", "en", "mixed"):
            aggregates[f"{group}_{language}"] = aggregate(per_query, queries, systems, lambda q, g=group, la=language: q.group == g and q.language == la)

    has_all = all(s in systems for s in HEADLINE)
    latency = {}
    for s in systems:
        secs = sorted(per_query[q][s]["seconds"] for q in per_query if s in per_query[q])
        latency[s] = {"median": statistics.median(secs), "p95": secs[int(0.95 * (len(secs) - 1))]}
    report = {
        "collection": run["collection"], "query_form": run["query_form"], "queries_sha256": run["queries_sha256"],
        "qrels_sha256": hashlib.sha256(Path(args.qrels).read_bytes()).hexdigest(),
        "config": run["config"], "systems": systems,
        "qrels": {"total": len(qrels), "grades": dict(Counter(str(r.grade) for r in qrels)), "human_verified": sum(r.human_verified for r in qrels), "judged_by": sorted({r.judged_by for r in qrels}),
                  "corrections_applied": bool(args.with_corrections), "correction_stats": correction_stats},
        "aggregates": aggregates,
        "declared": declared_tests(per_query, queries, cfg) if has_all else [],
        "domination": domination_table(per_query, queries, systems, run, cfg),
        "dr58": dr58_coverage(queries, per_query, systems, corpus, run),
        "exact_match": exact_match(per_query, queries, systems, targets, scripts),
        "scripts": script_breakdown(per_query, queries, systems, grades, scripts, cfg),
        "false_similarity": false_similarity(per_query, queries, cfg, run["snippets"]),
        "latency": latency,
        "per_query": {q: {s: {k: v for k, v in r.items() if k != "seconds"} for s, r in rows.items()} for q, rows in per_query.items()},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_bytes((json.dumps(report, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    out.with_suffix(".tables.md").write_bytes(tables_md(report, systems).encode("utf-8"))
    print(tables_md(report, systems))


if __name__ == "__main__":
    main()
