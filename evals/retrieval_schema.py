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
    group: Literal["known_item", "legal_term", "topic", "dr58", "no_answer"]
    category: Literal["land", "family", "criminal", "service", "contract", "writ", "tax", "other"]
    type: Literal["single-hop", "multi-hop", "statute-lookup", "case-lookup"]
    key_terms: list[str] = Field(default_factory=list)  # judging card আর exact-term মাপের জন্য, relevance এর প্রমাণ নয়
    seen_before: bool = False  # এই প্রশ্নের retrieval ফল আগে দেখা হয়েছে কিনা (Phase 1 এর smoke test)
    notes: str = ""

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
