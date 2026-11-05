"""
evals/report_eval.py
====================
Pipeline eval এর JSON থেকে markdown report, চাইলে একটা baseline এর সাথে তুলনা। Fake LLM এর run কে baseline হতে দেয় না।

    python evals/report_eval.py --run evals/reports/pipeline_v1.json --out evals/reports/pipeline_v1.md
    python evals/report_eval.py --run a.json --baseline b.json --out a.md
"""

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

METRICS = [
    ("generation", "must_mention", "Must-mention score"),
    ("generation", "faithfulness", "Faithfulness (LLM judge)"),
    ("generation", "correctness", "Correctness against reference (LLM judge)"),
    ("generation", "questions_with_a_must_not_claim_violation", "Questions with a must-not-claim violation"),
    ("citation", "precision", "Citation precision"),
    ("citation", "recall", "Citation recall"),
    ("citation", "fabricated_rate", "Fabricated-citation rate"),
    ("citation", "unsupported_rate", "Cited but not retrieved"),
    ("ops", "latency_p50_s", "Latency p50, seconds"),
    ("ops", "latency_p95_s", "Latency p95, seconds"),
    ("ops", "llm_calls_per_question", "LLM calls per question"),
    ("ops", "tokens_per_question", "Tokens per question"),
    ("ops", "cost_usd_per_question", "Cost per question, USD"),
]
BEHAVIOR = [("no_answer_abstention", "Says nothing relevant found, no-answer questions"), ("false_abstention_on_answerable", "Wrongly stops on answerable questions"),
            ("false_premise_corrected", "Corrects a false premise"), ("injection_resisted", "Resists injection"), ("clarified_vague_prediction", "Asks for details on vague predictions")]


def fmt(value) -> str:
    if value is None:
        return "not measured"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def render(run: dict, baseline: dict | None = None) -> str:
    fake = run["llm"] == "fake"
    lines = [f"# Pipeline evaluation: {run['collection']}, {run['dataset']}", ""]
    if fake:
        lines += ["**Environment test only.** This run used a fake LLM, so these figures show that the harness works and say nothing about answer quality.", ""]
    elif not run.get("is_baseline_candidate"):
        lines += ["**Partial run,** limited to some of the questions, so it isn't a baseline.", ""]
    s = run["summary"]
    lines += [f"{s['questions']} questions, {s['errors']} errors. LLM: {run['llm']}. Judge used: {'yes' if run['judge_used'] else 'no'}. Load time {run['load_seconds']} s, peak memory {run.get('peak_memory_mb')} MB.", ""]
    head = "| Metric | This run |" + (" Baseline | Change |" if baseline else "")
    lines += [head, "|---|---:|" + ("---:|---:|" if baseline else "")]

    def row(label: str, value, base) -> str:
        cell = f"| {label} | {fmt(value)} |"
        if baseline:
            change = f"{value - base:+.3f}" if isinstance(value, (int, float)) and isinstance(base, (int, float)) else "–"
            cell += f" {fmt(base)} | {change} |"
        return cell

    b = baseline["summary"] if baseline else {}
    for group, key, label in METRICS:
        lines.append(row(label, s[group].get(key), b.get(group, {}).get(key)))
    for key, label in BEHAVIOR:
        lines.append(row(f"{label} (n={s['behavior'][key]['n']})", s["behavior"][key]["rate"], b.get("behavior", {}).get(key, {}).get("rate")))
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--baseline")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
    if baseline is not None and not baseline.get("is_baseline_candidate"):
        raise SystemExit(f"{args.baseline} used a fake LLM or only part of the questions, so it can't be a baseline")
    Path(args.out).write_bytes(render(run, baseline).encode("utf-8"))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
