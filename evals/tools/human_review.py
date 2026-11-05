"""
evals/tools/human_review.py
===========================
Claude এর ৫৬৪ টা positive label মানুষ যাচাই করবে (D-202, D-207)। qrels কখনো বদলায় না: সিদ্ধান্ত যায় আলাদা, শুধু-বাড়ে
এমন evals/golden/human_corrections.jsonl এ, আর score এ লাগে শুধু `retrieval_score.py --with-corrections` দিলে।

    python evals/tools/human_review.py export                                   # CSV আর local review page বানায়
    python evals/tools/human_review.py import --from decisions.csv --reviewer "নাম"
    python evals/tools/human_review.py status

Review page (evals/review/review.html) একটা local file, কোথাও publish হয় না। সিদ্ধান্ত সেই browser এ থাকে, তাই
Export করে এই script দিয়ে import করতে হয়। কোন system কোন মামলা এনেছিল তা sheet এ নেই, যাতে বিচারে পক্ষপাত না আসে।
"""

import argparse
import bisect
import csv
import datetime
import hashlib
import json
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.retrieval_schema import HumanDecision, Qrel, latest_decisions, load_decisions, load_qrels, load_queries  # noqa: E402
from evals.tools.corpus import TEXT_DIR  # noqa: E402
from ingestion.chunk_cap import CAP_CONFIG  # noqa: E402

GOLDEN = ROOT / "evals" / "golden"
QRELS = GOLDEN / "retrieval_qrels.jsonl"
SHEET = GOLDEN / "human_review.csv"
LOG = GOLDEN / "human_corrections.jsonl"
PAGE = ROOT / "evals" / "review" / "review.html"
TEMPLATE = Path(__file__).with_name("review_template.html")
RUN = ROOT / "evals" / "runs" / "v3.json"
SYSTEMS = ("dense", "bm25", "hybrid", "hybrid_rerank", "hybrid_api")
SEED = 20261105
SHEET_FIELDS = ["review_id", "need_id", "queries", "judgement", "pdf_file", "pdf_page", "case_header", "proposed_grade", "grade_meaning", "graded_passage",
                "retrieved_passage", "passage_source", "decision", "human_grade", "reviewer_notes"]
GRADE_MEANING = {1: "Related: same statute or topic, but not this issue, or a minor part", 2: "On point: decides or substantially discusses the issue"}
RETRIEVED_CHARS = 1200


def review_id(need: str, case: str) -> str:
    return "rv-" + hashlib.sha256(f"{need}\0{case}".encode()).hexdigest()[:10]


def squeeze(text: str) -> str:
    import unicodedata

    return " ".join(unicodedata.normalize("NFC", text).split())


def case_files(case: str) -> list[Path]:
    """এক মামলার text file গুলো। Death Ref 58 তিনটা PDF এ ভাগ করা।"""
    for prefix, name in CAP_CONFIG["case_groups"].items():
        if case == name:
            return sorted(TEXT_DIR.glob(f"{prefix}*.txt"))
    return [TEXT_DIR / (case[:-4] + ".txt")]


def locate_page(pages: list[str], quote: str) -> int | None:
    """Quote কোন পাতায় (১ থেকে)। text_v3 এ \\f পাতা আলাদা করে। দুই পাতায় ছড়ানো হলে শুরুর পাতা।"""
    squeezed = [squeeze(p) for p in pages]
    offsets, joined, pos = [], [], 0
    for i, text in enumerate(squeezed):
        if text:
            offsets.append((pos, i))
            joined.append(text)
            pos += len(text) + 1
    start = " ".join(joined).find(squeeze(quote))
    if start < 0:
        return None
    return offsets[bisect.bisect_right([o for o, _ in offsets], start) - 1][1] + 1


def judgement_location(case: str, quote: str) -> tuple[str, int | None, str]:
    """(PDF, পাতা, মামলার শুরুর লেখা)।"""
    files = case_files(case)
    head = ""
    for path in files:
        pages = path.read_text(encoding="utf-8", errors="replace").split("\f")
        head = head or squeeze(" ".join(pages[:1]))[:260]
        page = locate_page(pages, quote)
        if page is not None:
            return path.stem + ".pdf", page, head
    return files[0].stem + ".pdf", None, head


def best_chunks(run: dict, queries_by_need: dict[str, list]) -> dict[tuple[str, str], str]:
    """(need, মামলা) → সবচেয়ে ভালো rank এ আসা chunk। কোন system তা রাখা হয় না।"""
    best: dict[tuple[str, str], tuple[int, str]] = {}
    for need, queries in queries_by_need.items():
        for q in queries:
            for system in SYSTEMS:
                row = run["results"].get(q.id, {}).get(system)
                if not row:
                    continue
                seen: set[str] = set()
                for cid, case, _ in row["chunks"]:
                    if case in seen:
                        continue
                    seen.add(case)
                    if len(seen) > run["config"]["k"]:
                        break
                    candidate = (len(seen), cid)
                    if (need, case) not in best or candidate < best[(need, case)]:
                        best[(need, case)] = candidate
    return {key: cid for key, (_, cid) in best.items()}


def build_rows(qrels: list[Qrel], queries, run: dict) -> list[dict]:
    by_need: dict[str, list] = {}
    for q in queries:
        by_need.setdefault(q.need_id, []).append(q)
    chunks = best_chunks(run, by_need)
    groups: dict[str, list[Qrel]] = {}
    for r in qrels:
        if r.grade >= 1:
            groups.setdefault(r.need_id, []).append(r)
    rows = []
    for need in sorted(groups):
        items = sorted(groups[need], key=lambda r: r.case_key)
        random.Random(f"{SEED}:{need}").shuffle(items)  # grade ধরে সাজানো থাকলে বিচারে প্রভাব পড়ত
        for r in items:
            pdf, page, head = judgement_location(r.case_key, r.evidence)
            cid = chunks.get((need, r.case_key))
            passage = " ".join(run["snippets"].get(cid, "").split())[:RETRIEVED_CHARS] if cid else ""
            rows.append({
                "review_id": review_id(need, r.case_key), "need_id": need, "queries": " || ".join(q.query for q in by_need[need]), "judgement": r.case_key, "pdf_file": pdf,
                "pdf_page": page or "", "case_header": head, "proposed_grade": r.grade, "grade_meaning": GRADE_MEANING[r.grade], "graded_passage": r.evidence,
                "retrieved_passage": passage, "passage_source": "retrieved chunk" if passage else "found by term search, no system's top 10", "decision": "", "human_grade": "", "reviewer_notes": "",
            })
    return rows


def key_terms_by_need(queries) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for q in queries:
        out.setdefault(q.need_id, q.key_terms)
    return out


def write_sheet(rows: list[dict]) -> None:
    # utf-8-sig: Excel বাংলা ঠিকভাবে দেখায়
    with SHEET.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHEET_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_page(rows: list[dict], terms: dict[str, list[str]]) -> None:
    data = json.dumps({"rows": rows, "terms": terms, "grades": {str(k): v for k, v in GRADE_MEANING.items()}}, ensure_ascii=False).replace("</", "<\\/")
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_bytes(TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data).encode("utf-8"))


def export() -> None:
    before = hashlib.sha256(QRELS.read_bytes()).hexdigest()
    queries = load_queries(GOLDEN / "retrieval_queries.jsonl")
    rows = build_rows(load_qrels(QRELS), queries, json.loads(RUN.read_text(encoding="utf-8")))
    write_sheet(rows)
    write_page(rows, key_terms_by_need(queries))
    assert hashlib.sha256(QRELS.read_bytes()).hexdigest() == before
    no_page = sum(not r["pdf_page"] for r in rows)
    no_chunk = sum(not r["retrieved_passage"] for r in rows)
    print(f"{len(rows)} positive labels -> {SHEET.name} and {PAGE.relative_to(ROOT)}; {no_page} without a located page, {no_chunk} not in any system's top 10")


def decisions_from_rows(rows: list[dict], known: dict[str, dict], reviewer: str, today: str) -> list[HumanDecision]:
    """সিদ্ধান্ত ফাঁকা হলে সেই সারি বাদ। অজানা বা ভুল সারিতে পুরো import থামে, আধা-import হয় না।"""
    out = []
    for row in rows:
        decision = (row.get("decision") or "").strip().lower()
        if not decision:
            continue
        rid = (row.get("review_id") or "").strip()
        if rid not in known:
            raise SystemExit(f"unknown review_id: {rid!r}")
        label = known[rid]
        grade = (row.get("human_grade") or "").strip()
        name = (row.get("reviewer") or reviewer or "").strip()
        out.append(HumanDecision(review_id=rid, need_id=label["need_id"], case_key=label["case_key"], proposed_grade=label["grade"], decision=decision, human_grade=int(grade) if grade else None,
                                 reviewer=name, reviewed_on=(row.get("reviewed_on") or today).strip(), notes=(row.get("reviewer_notes") or "").strip()))
    return out


def append_log(path: Path, decisions: list[HumanDecision]) -> int:
    """আগে থাকা হুবহু একই সিদ্ধান্ত আবার লেখে না, পুরনো সারি কখনো মোছে না।"""
    existing = {d.model_dump_json() for d in load_decisions(path)}
    fresh = [d for d in decisions if d.model_dump_json() not in existing]
    if fresh:
        with path.open("ab") as fh:
            for d in fresh:
                fh.write((json.dumps(d.model_dump(), ensure_ascii=False) + "\n").encode("utf-8"))
    return len(fresh)


def known_labels(qrels: list[Qrel]) -> dict[str, dict]:
    return {review_id(r.need_id, r.case_key): {"need_id": r.need_id, "case_key": r.case_key, "grade": r.grade} for r in qrels if r.grade >= 1}


def do_import(source: Path, reviewer: str) -> None:
    before = hashlib.sha256(QRELS.read_bytes()).hexdigest()
    with source.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    decisions = decisions_from_rows(rows, known_labels(load_qrels(QRELS)), reviewer, datetime.date.today().isoformat())
    added = append_log(LOG, decisions)
    assert hashlib.sha256(QRELS.read_bytes()).hexdigest() == before
    print(f"{len(decisions)} decisions read, {added} new added to {LOG.name}. retrieval_qrels.jsonl is unchanged.")
    status()


def review_status(qrels: list[Qrel], decisions: list[HumanDecision]) -> dict:
    positive = {(r.need_id, r.case_key): r.grade for r in qrels if r.grade >= 1}
    latest = {k: d for k, d in latest_decisions(decisions).items() if k in positive}
    moves: dict[str, int] = {}
    for d in latest.values():
        if d.decision == "change":
            key = f"{d.proposed_grade} to {d.human_grade}"
            moves[key] = moves.get(key, 0) + 1
    decided = [d for d in latest.values() if d.decision != "unsure"]
    agreed = sum(d.decision == "agree" for d in decided)
    return {"positive_labels": len(positive), "reviewed": len(latest), "agreed": agreed, "changed": len(decided) - agreed, "unsure": len(latest) - len(decided), "remaining": len(positive) - len(latest),
            "agreement_rate": round(agreed / len(decided), 3) if decided else None, "changes": moves, "reviewers": sorted({d.reviewer for d in latest.values()})}


def status() -> None:
    print(json.dumps(review_status(load_qrels(QRELS), load_decisions(LOG)), ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["export", "import", "status"])
    ap.add_argument("--from", dest="source", help="review page থেকে Export করা বা পূরণ করা CSV")
    ap.add_argument("--reviewer", default="")
    args = ap.parse_args()
    if args.command == "export":
        export()
    elif args.command == "status":
        status()
    else:
        if not args.source:
            ap.error("import needs --from")
        do_import(Path(args.source), args.reviewer)


if __name__ == "__main__":
    main()
