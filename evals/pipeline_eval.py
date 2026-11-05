"""
evals/pipeline_eval.py
======================
Golden আর adversarial set আসল pipeline এ চালিয়ে generation, citation, abstention আর ops মাপে (SPEC Phase 2)। Server চালু বা
বন্ধ করে না, production DB খোলে না (evals/pipeline_env.py)। `--llm fake` শুধু পরিবেশ পরীক্ষা, উত্তরের মান নয়।
"""

import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path

from evals.golden_schema import AdversarialItem, GoldenItem, load_adversarial, load_golden
from evals.metrics.abstention import behavior_row, behavior_summary
from evals.metrics.citation import aggregate_citations, build_registry, citation_metrics
from evals.metrics.generation import (
    flatten_answer,
    judge_correctness,
    judge_faithfulness,
    mean_defined,
    must_mention_score,
    must_not_claim_violations,
)
from evals.pipeline_env import COLLECTIONS, free_ram_mb, load_pipeline, peak_memory_mb

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "evals" / "golden"
CONFIG = ROOT / "evals" / "generation_config.json"
CACHE = ROOT / "evals" / "cache"


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


class RecordingLLM:
    """api_v3.call_llm এর জায়গায় বসে। প্রতিটা call গোনে; আসল LLM হলে llm/client.py দিয়ে যায়, তাই retry, cache আর খরচ পাওয়া যায়।"""

    def __init__(self, fake: Callable | None = None, client=None, temperature: float = 0.1):
        self.fake = fake
        self.client = client
        self.temperature = temperature
        self.calls: list[dict] = []

    def __call__(self, system_prompt: str, user_content: str, json_mode: bool = True, schema=None) -> str:
        if self.fake is not None:
            self.calls.append({"provider": "fake", "input_tokens": None, "output_tokens": None, "cost_usd": None, "cached": False})
            return self.fake(system_prompt, user_content, json_mode, schema)
        result = self.client.complete(system_prompt, user_content, json_mode=json_mode, schema=schema, temperature=self.temperature)
        self.calls.append({"provider": result.provider, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "cost_usd": result.cost_usd, "cached": result.cached})
        return result.text

    def take(self) -> list[dict]:
        calls, self.calls = self.calls, []
        return calls


class planted_context:
    """শুধু এই প্রশ্নের জন্য, শুধু এই process এর memory তে sources এ একটা বানানো অংশ যোগ করে। কোনো collection এ যায় না।"""

    def __init__(self, api, snippet: str):
        self.api, self.snippet, self.original = api, snippet, api.build_sources_block

    def __enter__(self):
        if self.snippet:
            self.api.build_sources_block = lambda retrieved: self.original(retrieved) + f"\n\n### Case: Planted test document\n{self.snippet}\n"
        return self

    def __exit__(self, *exc):
        self.api.build_sources_block = self.original


def extract_citations(state: dict) -> tuple[list[str], list[dict]]:
    """LLM আসলে কী উদ্ধৃত করেছিল, finalize সরানোর আগের সহ। fabricated এই দিয়ে গোনা হয়।"""
    answer = state.get("structured_answer") or {}
    issues = state.get("citation_issues") or {}
    cases = [c.get("case_no") for c in (answer.get("relevant_cases") or []) if isinstance(c, dict)] + [c.get("case_no") for c in issues.get("bad_cases") or [] if isinstance(c, dict)]
    laws = [c for c in (answer.get("applicable_laws") or []) if isinstance(c, dict)] + [c for c in issues.get("bad_laws") or [] if isinstance(c, dict)]
    return [c for c in dict.fromkeys(cases) if c], _unique_laws(laws)


def _unique_laws(laws: list[dict]) -> list[dict]:
    seen, out = set(), []
    for law in laws:
        key = (str(law.get("act")), str(law.get("section")))
        if key not in seen:
            seen.add(key)
            out.append(law)
    return out


def run_item(api, item: GoldenItem | AdversarialItem, llm: RecordingLLM, registry: dict, config: dict, judge_client=None) -> dict:
    expected_behavior = getattr(item, "expected_behavior", "answer")
    started = time.perf_counter()
    llm.take()
    row = {"id": item.id, "question": item.question, "language": item.language, "category": item.category, "type": item.type, "adv_type": getattr(item, "adv_type", None), "expected_behavior": expected_behavior, "status": "ok"}
    try:
        with planted_context(api, getattr(item, "planted_snippet", "")):
            state = api.legal_graph.invoke({"question": item.question, "history": [], "filters": None, "forced_intent": None})
    except Exception as e:  # noqa: BLE001 একটা প্রশ্ন ব্যর্থ হলে পুরো run থামে না, কিন্তু error গোনা হয়
        row.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}", latency_s=round(time.perf_counter() - started, 2), llm_calls=llm.take())
        return row
    answer = state.get("structured_answer") or {}
    retrieved = state.get("retrieved") or {"laws": [], "judgements": []}
    cited_cases, cited_laws = extract_citations(state)
    text = flatten_answer(answer)
    mention = must_mention_score(text, item.must_mention)
    violations = must_not_claim_violations(text, item.must_not_claim)
    citations = citation_metrics(cited_cases, cited_laws, [j["meta"] for j in retrieved["judgements"]], [law["meta"] for law in retrieved["laws"]], registry, [s.model_dump() for s in item.expected_sources], item.expected_match)
    sufficient = bool(state.get("retrieval_sufficient"))
    row.update(
        latency_s=round(time.perf_counter() - started, 2),
        intent=state.get("intent"),
        retrieval_sufficient=sufficient,
        max_rerank_score=state.get("max_rerank_score"),
        retrieval_attempts=state.get("retrieval_retry"),
        answer_attempts=state.get("answer_retry"),
        citation_valid=state.get("citation_valid"),
        unverified_citations=len(state.get("unverified_citations") or []),
        answer_preview=str(answer.get("short_answer") or "")[:300],
        cited_cases=cited_cases,
        cited_laws=[f"{x.get('act')} | {x.get('section')}" for x in cited_laws],
        must_mention=mention,
        must_not_claim_violations=violations,
        citations=citations,
        behavior=behavior_row(expected_behavior, answer, sufficient, citations["cited"], mention["missing"], violations, getattr(item, "canary", ""), config),
        llm_calls=llm.take(),
    )
    if judge_client is not None:
        providers = [c["provider"] for c in row["llm_calls"]]
        generator = providers[-1] if providers else None
        sources = api.build_sources_block(retrieved)
        row["faithfulness"] = judge_faithfulness(judge_client, item.question, text, sources, generator, config["judge_temperature"])
        if item.reference_answer:
            row["correctness"] = judge_correctness(judge_client, item.question, text, item.reference_answer, generator, config["judge_temperature"])
    return row


def percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int(share * (len(ordered) - 1))]


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    calls = [c for r in rows for c in r.get("llm_calls", [])]
    tokens = [(c["input_tokens"] or 0) + (c["output_tokens"] or 0) for c in calls if c["input_tokens"] is not None]
    costs = [c["cost_usd"] for c in calls]
    latency = [r["latency_s"] for r in ok]
    mentions = [r["must_mention"]["score"] for r in ok]
    return {
        "questions": len(rows),
        "errors": len(rows) - len(ok),
        "generation": {
            "must_mention": mean_defined(mentions),
            "questions_with_must_mention": sum(m is not None for m in mentions),
            "questions_with_a_must_not_claim_violation": sum(bool(r["must_not_claim_violations"]) for r in ok),
            "faithfulness": mean_defined([r.get("faithfulness", {}).get("score") for r in ok]),
            "contradicted_claims": sum(r.get("faithfulness", {}).get("contradicted", 0) for r in ok),
            "correctness": mean_defined([r.get("correctness", {}).get("score") for r in ok]),
            "questions_with_a_reference": sum("correctness" in r for r in ok),
            "judge_failures": sum("judge_error" in r.get("faithfulness", {}) or "judge_error" in r.get("correctness", {}) for r in ok),
            "judge_not_independent": sum(r.get("faithfulness", {}).get("judge_independent") is False for r in ok),
        },
        "citation": aggregate_citations([r["citations"] for r in ok]),
        "behavior": behavior_summary([r["behavior"] for r in ok]),
        "ops": {
            "latency_p50_s": statistics.median(latency) if latency else None,
            "latency_p95_s": percentile(latency, 0.95),
            "llm_calls": len(calls),
            "llm_calls_per_question": round(len(calls) / len(rows), 2) if rows else None,
            "tokens_per_question": round(sum(tokens) / len(rows), 1) if tokens else None,
            # একটা call এর দামও অজানা হলে মোট অজানা: শূন্য দেখালে ভুল ধারণা হত
            "cost_usd_per_question": round(sum(costs) / len(rows), 6) if costs and all(c is not None for c in costs) else None,
        },
    }


def load_items(dataset: str) -> list[GoldenItem | AdversarialItem]:
    items: list[GoldenItem | AdversarialItem] = []
    if dataset in ("golden", "both"):
        items += load_golden(GOLDEN_DIR / "golden_set.jsonl")
    if dataset in ("adversarial", "both"):
        items += load_adversarial(GOLDEN_DIR / "adversarial_set.jsonl")
    return items


def run_pipeline_eval(collection: str, dataset: str, llm_kind: str, db_path: Path, out: Path, limit: int | None = None, use_judge: bool = False, ids: list[str] | None = None) -> dict:
    from evals.fake_llm import fake_call_llm

    config = load_config()
    free = free_ram_mb()
    if free is not None and free < config["min_free_ram_mb"]:
        print(f"warning: only {free} MB of RAM free; the pipeline needs several GB and may run slowly or fail", flush=True)
    items = load_items(dataset)
    if ids:
        items = [i for i in items if i.id in set(ids)]
    if limit:
        items = items[:limit]
    client = judge_client = None
    if llm_kind == "real":
        from llm.client import LLMClient, load_prices, providers_from_env

        providers = providers_from_env()
        client = LLMClient(providers, cache_path=CACHE / "llm_cache.jsonl", log_path=CACHE / "llm_calls.jsonl", timeout_s=config["llm_timeout_s"], max_retries=config["llm_max_retries"], min_interval_s=config["llm_min_interval_s"], prices=load_prices())
        judge_client = client if use_judge else None
    llm = RecordingLLM(fake=fake_call_llm if llm_kind == "fake" else None, client=client, temperature=config["pipeline_temperature"])
    started = time.time()
    api = load_pipeline(collection, db_path, llm)
    load_seconds = round(time.time() - started, 1)
    print(f"pipeline loaded in {load_seconds}s on {COLLECTIONS[collection]}, {len(items)} items", flush=True)
    registry = build_registry(api.judgements_bm25["metas"], api.laws_bm25["metas"])
    rows = []
    for i, item in enumerate(items, 1):
        rows.append(run_item(api, item, llm, registry, config, judge_client))
        print(f"[{i}/{len(items)}] {item.id} {rows[-1]['status']} {rows[-1].get('latency_s')}s", flush=True)
    report = {
        "system": "pipeline", "collection": collection, "dataset": dataset, "llm": llm_kind, "is_baseline_candidate": llm_kind == "real" and not limit and not ids, "judge_used": bool(judge_client), "load_seconds": load_seconds,
        "peak_memory_mb": peak_memory_mb(), "free_ram_mb_at_start": free, "chunks_in_collection": api.judgements_col.count(), "generation_config": config, "summary": summarize(rows), "rows": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(report, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    print(f"EVAL_DONE {out}", flush=True)
    return report
