"""
evals/retrieval_schema.py
=========================
Retrieval benchmark এর schema আর config (CLAUDE.md নিয়ম ৭, ৯)। ভুল benchmark চুপচাপ ভুল সংখ্যা দেয়, তাই পড়ার
সময়ই সব যাচাই হয়। SPEC এর golden_set schema র সাথে মেলে: expected_sources = এই qrels এর grade >= 1 ধাপ।
"""

import json
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BenchmarkQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    need_id: str  # একই তথ্যের প্রয়োজন, বাংলা আর ইংরেজি প্রশ্ন একই qrels ভাগ করে
    query: str
    language: Literal["bn", "en", "mixed"]
    group: Literal["known_item", "legal_term", "topic", "dr58", "no_answer", "lay"]
    category: Literal["land", "family", "criminal", "service", "contract", "writ", "tax", "other"]
    type: Literal["single-hop", "multi-hop", "statute-lookup", "case-lookup"]
    key_terms: list[str] = Field(default_factory=list)  # judging card আর exact-term মাপের জন্য, relevance এর প্রমাণ নয়
    seen_before: bool = False  # এই প্রশ্নের retrieval ফল আগে দেখা হয়েছে কিনা (Phase 1 এর smoke test)
    notes: str = ""
    style: str = ""  # lay প্রশ্নের লেখার ধরন, যেমন bn_colloquial, banglish
    uses_legal_terms: bool | None = None  # lay প্রশ্নে আইনি পরিভাষা আছে কিনা

    @field_validator("query")
    @classmethod
    def query_is_nfc(cls, v: str) -> str:
        if unicodedata.normalize("NFC", v) != v:
            raise ValueError(f"query not NFC: {v!r}")
        return v


class Qrel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    need_id: str
    case_key: str
    grade: Literal[0, 1, 2]
    evidence: str = ""  # text_v3 এর হুবহু অংশ, grade >= 1 হলে বাধ্যতামূলক
    judged_by: str
    human_verified: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def positive_needs_evidence(self) -> "Qrel":
        if self.grade >= 1 and not self.evidence.strip():
            raise ValueError(f"{self.need_id}/{self.case_key}: grade {self.grade} needs evidence")
        return self


class HumanDecision(BaseModel):
    """একজন মানুষের সিদ্ধান্ত। qrels এ হাত দেয় না: আলাদা log এ জমা থাকে, আর score এ লাগে শুধু চাইলে (D-207)।"""

    model_config = ConfigDict(extra="forbid")

    review_id: str
    need_id: str
    case_key: str
    proposed_grade: Literal[0, 1, 2]
    decision: Literal["agree", "change", "unsure"]
    human_grade: Literal[0, 1, 2] | None = None
    reviewer: str
    reviewed_on: str
    notes: str = ""

    @model_validator(mode="after")
    def grade_fits_decision(self) -> "HumanDecision":
        if not self.reviewer.strip():
            raise ValueError(f"{self.review_id}: reviewer is required")
        if self.decision == "change" and (self.human_grade is None or self.human_grade == self.proposed_grade):
            raise ValueError(f"{self.review_id}: change needs a grade different from {self.proposed_grade}")
        if self.decision == "agree" and self.human_grade not in (None, self.proposed_grade):
            raise ValueError(f"{self.review_id}: agree can't carry a different grade")
        if self.decision == "unsure" and self.human_grade is not None:
            raise ValueError(f"{self.review_id}: unsure can't carry a grade")
        return self


def latest_decisions(decisions: list[HumanDecision]) -> dict[tuple[str, str], HumanDecision]:
    """Log শুধু বাড়ে। একই label এ পরের সিদ্ধান্তটাই চলে।"""
    return {(d.need_id, d.case_key): d for d in decisions}


def apply_human_decisions(qrels: list[Qrel], decisions: list[HumanDecision]) -> tuple[list[Qrel], dict]:
    """নতুন তালিকা ফেরত দেয়, মূল qrels অপরিবর্তিত। unsure গুলো Claude এর grade এই থাকে আর আলাদা গোনা হয়।"""
    latest = latest_decisions(decisions)
    out = []
    stats = {"agreed": 0, "changed": 0, "unsure": 0, "unknown_labels": sorted(f"{n}/{c}" for n, c in latest if not any(q.need_id == n and q.case_key == c for q in qrels))}
    for q in qrels:
        d = latest.get((q.need_id, q.case_key))
        if d is None or d.decision == "unsure":
            stats["unsure"] += d is not None
            out.append(q)
        elif d.decision == "agree":
            stats["agreed"] += 1
            out.append(q.model_copy(update={"human_verified": True}))
        else:
            stats["changed"] += 1
            out.append(q.model_copy(update={"grade": d.human_grade, "human_verified": True, "notes": d.notes or q.notes}))
    return out, stats


def load_decisions(path: Path) -> list[HumanDecision]:
    if not path.exists():
        return []
    return [HumanDecision.model_validate_json(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class RetrievalEvalConfig(BaseModel):
    """প্রতিটা সংখ্যা ব্যাখ্যাসহ docs/DECISIONS.md এ। report এ পুরোটা লেখা হয়।"""

    model_config = ConfigDict(extra="forbid")

    embed_model: str
    fetch_depth: int  # প্রতি প্রশ্নে কত chunk; মামলায় নামানোর পরও ১০টা থাকার জন্য যথেষ্ট বড়
    fusion_depth: int  # hybrid এ প্রতি পদ্ধতি থেকে কত chunk
    rrf_k: int
    rerank_model: str
    rerank_max_length: int
    rerank_text_chars: int
    rerank_candidates: int
    api_retrieve_n: int  # hybrid_api: api_v3.N_RETRIEVE
    k: int
    relevant_threshold: int  # এই grade বা বেশি = প্রাসঙ্গিক
    strict_threshold: int
    primary_metric: str
    bootstrap_samples: int
    permutation_samples: int
    seed: int
    alpha: float
    large_case_top_share: float  # chunk সংখ্যায় উপরের এই ভাগ মামলা = "বড় মামলা"
    similarity_thresholds: list[float]
    rerank_api_threshold: float  # api_v3.RERANK_THRESHOLD, পরিবর্তন নয়, শুধু মাপা হয়
    min_judged_for_claim: float = 0.9  # top-10 এর এর চেয়ে কম বিচার করা থাকলে তুলনা "not ready", জয়-পরাজয় নয় (D-206)


def load_config(path: Path) -> RetrievalEvalConfig:
    return RetrievalEvalConfig.model_validate_json(path.read_text(encoding="utf-8"))


def load_queries(path: Path) -> list[BenchmarkQuery]:
    rows = [BenchmarkQuery.model_validate_json(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len({r.id for r in rows}) != len(rows):
        raise ValueError("duplicate query id")
    if len({r.query for r in rows}) != len(rows):
        raise ValueError("duplicate query text")
    return rows


def load_qrels(path: Path) -> list[Qrel]:
    rows = [Qrel.model_validate_json(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len({(r.need_id, r.case_key) for r in rows}) != len(rows):
        raise ValueError("duplicate qrel")
    return rows


def grades_by_need(qrels: list[Qrel]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in qrels:
        out.setdefault(r.need_id, {})[r.case_key] = r.grade
    return out


def write_jsonl(path: Path, rows: list[BaseModel]) -> None:
    body = "".join(json.dumps(r.model_dump(), ensure_ascii=False) + "\n" for r in rows)
    path.write_bytes(body.encode("utf-8"))
