"""
evals/metrics/generation.py
===========================
উত্তরের মান। must_mention আর must_not_claim এ model লাগে না। Faithfulness আর correctness এ LLM judge লাগে, যে
llm/client.py দিয়ে চলে, আর generator এর provider বাদ দিয়ে (SPEC: সম্ভব হলে ভিন্ন provider)। Prompt: llm/prompts/।
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from legal_rules import BN_DIGITS

PROMPTS = Path(__file__).resolve().parent.parent.parent / "llm" / "prompts"


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text)).translate(BN_DIGITS).casefold().split())


def flatten_answer(value) -> str:
    """Structured উত্তরের সব লেখা এক সাথে।"""
    if isinstance(value, dict):
        return " ".join(flatten_answer(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(flatten_answer(v) for v in value)
    return "" if value is None else str(value)


def phrase_present(normalized_text: str, spec: str) -> bool:
    """spec "a|b" মানে a বা b।"""
    return any(normalize_text(alt) in normalized_text for alt in spec.split("|") if alt.strip())


def must_mention_score(text: str, items: list[str]) -> dict:
    normalized = normalize_text(text)
    missing = [m for m in items if not phrase_present(normalized, m)]
    return {"score": (len(items) - len(missing)) / len(items) if items else None, "missing": missing}


def must_not_claim_violations(text: str, items: list[str]) -> list[str]:
    normalized = normalize_text(text)
    return [m for m in items if phrase_present(normalized, m)]


class ClaimCheck(BaseModel):
    claim: str
    verdict: Literal["supported", "contradicted", "not_in_sources"]


class FaithfulnessVerdict(BaseModel):
    claims: list[ClaimCheck] = Field(default_factory=list)


class CorrectnessVerdict(BaseModel):
    verdict: Literal["correct", "partial", "wrong"]
    missing: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


CORRECTNESS_SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}


def parse_json_model(text: str, model: type[BaseModel]) -> BaseModel:
    """কিছু model JSON এর চারপাশে ``` বসায়।"""
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        return model.model_validate_json(cleaned)
    except ValidationError:
        found = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not found:
            raise
        return model.model_validate(json.loads(found.group()))


def faithfulness_from(verdict: FaithfulnessVerdict) -> dict:
    claims = verdict.claims
    supported = sum(c.verdict == "supported" for c in claims)
    contradicted = sum(c.verdict == "contradicted" for c in claims)
    return {"score": supported / len(claims) if claims else None, "claims": len(claims), "supported": supported, "contradicted": contradicted, "not_in_sources": len(claims) - supported - contradicted}


def _tagged(**parts: str) -> str:
    """লেখার ভেতরের কোনো tag, এমনকি অন্য অংশের, যেন data থেকে বেরিয়ে নতুন অংশ সাজাতে না পারে।"""

    def clean(value: str) -> str:
        for name in parts:
            value = value.replace(f"</{name}>", f"<_/{name}>").replace(f"<{name}>", f"<_{name}>")
        return value

    return "\n".join(f"<{k}>\n{clean(str(v))}\n</{k}>" for k, v in parts.items())


def judge_faithfulness(client, question: str, answer: str, sources: str, generator_provider: str | None, temperature: float = 0.0) -> dict:
    prompt = (PROMPTS / "judge_faithfulness_v1.md").read_text(encoding="utf-8")
    return _judge(client, prompt, _tagged(question=question, answer=answer, sources=sources), FaithfulnessVerdict, faithfulness_from, generator_provider, temperature, "judge_faithfulness_v1")


def judge_correctness(client, question: str, answer: str, reference: str, generator_provider: str | None, temperature: float = 0.0) -> dict:
    prompt = (PROMPTS / "judge_correctness_v1.md").read_text(encoding="utf-8")
    to_row = lambda v: {"score": CORRECTNESS_SCORE[v.verdict], "verdict": v.verdict, "missing": v.missing, "conflicts": v.conflicts}  # noqa: E731
    return _judge(client, prompt, _tagged(question=question, answer=answer, reference=reference), CorrectnessVerdict, to_row, generator_provider, temperature, "judge_correctness_v1")


def _judge(client, prompt: str, user: str, model: type[BaseModel], to_row, generator_provider: str | None, temperature: float, version: str) -> dict:
    """Judge ভিন্ন provider এ যায়। শুধু একটা provider চালু থাকলে সেটাই ব্যবহার হয়, আর judge_independent মিথ্যা লেখা থাকে।"""
    available = {p.name for p in client.providers}
    exclude = (generator_provider,) if generator_provider and available - {generator_provider} else ()
    try:
        result = client.complete(prompt, user, json_mode=True, schema=model, temperature=temperature, exclude=exclude)
        row = to_row(parse_json_model(result.text, model))
    except Exception as e:  # noqa: BLE001 judge ব্যর্থ হলে score নেই, চুপচাপ শূন্য নয়
        return {"score": None, "judge_error": f"{type(e).__name__}: {str(e)[:160]}", "prompt_version": version}
    return {**row, "judge_provider": result.provider, "judge_model": result.model, "judge_independent": result.provider != generator_provider, "prompt_version": version, "judge_cost_usd": result.cost_usd,
            "judge_tokens": (result.input_tokens or 0) + (result.output_tokens or 0)}


def mean_defined(values: list[float | None]) -> float | None:
    got = [v for v in values if v is not None]
    return sum(got) / len(got) if got else None
