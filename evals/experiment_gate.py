"""
evals/experiment_gate.py
========================
Benchmark আর label প্রস্তুত না হওয়া পর্যন্ত ব্যয়বহুল Phase 3 পরীক্ষা চলবে না (D-209)। দুটো শর্ত, দুটোই file থেকে হিসাব হয়, কেউ হাতে
"ready" লিখতে পারে না:

  1. Claude এর সব positive label মানুষ দেখেছে (human_corrections.jsonl)
  2. Lay প্রশ্নের চারটা system এর top-10 এর যথেষ্ট অংশ বিচার করা (evals/runs/lay_v3.json আর lay_qrels.jsonl)
"""

import json
from pathlib import Path

from evals.metrics.retrieval import judged_at_k
from evals.retrieval_schema import grades_by_need, latest_decisions, load_decisions, load_qrels, load_queries

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "evals" / "golden"
RUNS = ROOT / "evals" / "runs"
LAY_SYSTEMS = ("dense", "bm25", "hybrid", "hybrid_rerank")
MIN_JUDGED = 0.9


def review_gate(golden: Path = GOLDEN) -> dict:
    positive = {(r.need_id, r.case_key) for r in load_qrels(golden / "retrieval_qrels.jsonl") if r.grade >= 1}
    latest = latest_decisions(load_decisions(golden / "human_corrections.jsonl"))
    reviewed = sum(key in latest and latest[key].decision != "unsure" for key in positive)
    return {"ready": reviewed == len(positive), "reviewed": reviewed, "positive_labels": len(positive)}


def lay_gate(golden: Path = GOLDEN, runs: Path = RUNS, minimum: float = MIN_JUDGED) -> dict:
    path = runs / "lay_v3.json"
    if not path.exists():
        return {"ready": False, "reason": "lay queries haven't been run through all four systems"}
    run = json.loads(path.read_text(encoding="utf-8"))
    missing = [s for s in LAY_SYSTEMS if s not in run["systems"]]
    if missing:
        return {"ready": False, "reason": f"lay run lacks {missing}"}
    grades = grades_by_need(load_qrels(golden / "retrieval_qrels.jsonl") + load_qrels(golden / "lay_qrels.jsonl"))
    queries = load_queries(golden / "lay_queries.jsonl")
    shares = {}
    for system in LAY_SYSTEMS:
        values = []
        for q in queries:
            seen, top = set(), []
            for _, case, _score in run["results"][q.id][system]["chunks"]:
                if case not in seen:
                    seen.add(case)
                    top.append(case)
                if len(top) == run["config"]["k"]:
                    break
            values.append(judged_at_k(top, grades.get(q.need_id, {}), run["config"]["k"]))
        shares[system] = round(sum(values) / len(values), 3)
    return {"ready": min(shares.values()) >= minimum, "judged_share": shares, "minimum": minimum}


def readiness(golden: Path = GOLDEN, runs: Path = RUNS) -> dict:
    review = review_gate(golden)
    lay = lay_gate(golden, runs)
    unmet = []
    if not review["ready"]:
        unmet.append(f"human review: {review['reviewed']} of {review['positive_labels']} positive labels decided")
    if not lay["ready"]:
        unmet.append(f"lay labels: {lay.get('reason') or 'judged share ' + str(lay['judged_share'])}")
    return {"ready": not unmet, "unmet": unmet, "review": review, "lay": lay}
