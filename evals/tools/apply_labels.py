"""
evals/tools/apply_labels.py
===========================
evals/judging/<need>.labels (প্রতি লাইনে `card grade [a|b] [note]`) থেকে evals/golden/retrieval_qrels.jsonl বানায়।
Known-item এর লক্ষ্য known_targets.json থেকে, প্রমাণ key term এর চারপাশের হুবহু text।

একটা card ও বাদ গেলে বা প্রমাণ না মিললে থেমে যায়: আধা-বিচার করা benchmark চুপচাপ ভুল সংখ্যা দিত।

    python evals/tools/apply_labels.py --judge claude-opus-5-5
"""

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.retrieval_schema import Qrel, load_queries, write_jsonl  # noqa: E402
from evals.tools.build_pool import JUDGED_GROUPS, JUDGING, QUERIES, passages, squeeze  # noqa: E402
from evals.tools.corpus import load_corpus  # noqa: E402

GOLDEN = ROOT / "evals" / "golden"


def parse_labels(path: Path) -> dict[str, tuple[int, str, str]]:
    labels = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        parts = line.split(maxsplit=2)
        card, grade = parts[0], int(parts[1])
        passage, note = "a", ""
        if len(parts) == 3:
            head, _, tail = parts[2].partition(" ")
            if head in ("a", "b"):
                passage, note = head, tail
            else:
                note = parts[2]
        if card in labels:
            raise ValueError(f"{path.name}: card {card} labelled twice")
        labels[card] = (grade, passage, note)
    return labels


def need_qrels(need: str, judge: str) -> list[Qrel]:
    mapping = json.loads((JUDGING / f"{need}.map.json").read_text(encoding="utf-8"))
    labels = parse_labels(JUDGING / f"{need}.labels")
    if set(labels) != set(mapping):
        raise ValueError(f"{need}: missing {sorted(set(mapping) - set(labels))[:8]}, extra {sorted(set(labels) - set(mapping))[:8]}")
    out = []
    for card, (grade, passage, note) in labels.items():
        row = mapping[card]
        evidence = row["passages"].get(passage, "") if grade >= 1 else ""
        out.append(Qrel(need_id=need, case_key=row["case_key"], grade=grade, evidence=evidence, judged_by=judge, notes=note))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", required=True)
    ap.add_argument("--allow-partial", action="store_true", help="labels না থাকা need বাদ দিয়ে লেখে, শুধু অগ্রগতি দেখার জন্য")
    args = ap.parse_args()

    queries = load_queries(QUERIES)
    corpus = load_corpus()
    targets = json.loads((GOLDEN / "known_targets.json").read_text(encoding="utf-8"))
    rows: dict[tuple[str, str], Qrel] = {}
    missing = []
    for need in sorted({q.need_id for q in queries if q.group in JUDGED_GROUPS}):
        if not (JUDGING / f"{need}.labels").exists():
            missing.append(need)
            continue
        for r in need_qrels(need, args.judge):
            rows[(r.need_id, r.case_key)] = r
    if missing and not args.allow_partial:
        raise SystemExit(f"labels missing for {missing}")
    # লক্ষ্য মামলা সব সময় 2: পুলের বিচারের উপর নির্ভর করে না, আর প্রথম পাতা পড়ে আগেই নিশ্চিত করা
    for q in queries:
        if q.need_id in targets:
            for case in targets[q.need_id]:
                found, _ = passages(squeeze(corpus[case]), q.key_terms, 100)
                if not found:
                    raise SystemExit(f"{q.id}: no key term in target {case}")
                prior = rows.get((q.need_id, case))
                if prior and prior.grade != 2:
                    print(f"note: {q.need_id}/{case[:30]} card graded {prior.grade}, target forces 2")
                rows[(q.need_id, case)] = Qrel(need_id=q.need_id, case_key=case, grade=2, evidence=found[0], judged_by=args.judge, notes="known target")
    ordered = [rows[k] for k in sorted(rows)]
    write_jsonl(GOLDEN / "retrieval_qrels.jsonl", ordered)
    grades = [r.grade for r in ordered]
    print(f"{len(ordered)} qrels, grades 0/1/2: {grades.count(0)}/{grades.count(1)}/{grades.count(2)}, missing needs: {missing}")


if __name__ == "__main__":
    main()
