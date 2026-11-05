"""
evals/golden_schema.py
======================
SPEC Phase 2 এর golden আর adversarial set এর schema। Reference answer লিখবে বা যাচাই করবে মানুষ: review_status সেটা
আলাদা করে রাখে, যাতে খসড়াকে কেউ যাচাই করা উত্তর না ভাবে।
"""

import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["law", "judgement"]
    ref: str  # law: "Act title|section number"; judgement: case_key


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    language: Literal["bn", "en", "mixed"]
    category: Literal["land", "family", "criminal", "service", "contract", "writ", "tax", "other"]
    type: Literal["single-hop", "multi-hop", "statute-lookup", "case-lookup"]
    expected_sources: list[SourceRef] = Field(default_factory=list)
    expected_match: Literal["any", "all"] = "any"  # অনেক মামলা প্রাসঙ্গিক হলে একটা উদ্ধৃত করলেই যথেষ্ট
    reference_answer: str = ""
    must_mention: list[str] = Field(default_factory=list)  # "a|b" মানে a বা b
    must_not_claim: list[str] = Field(default_factory=list)
    notes: str = ""
    review_status: Literal["needs_reference", "draft", "human_verified"] = "needs_reference"
    source_query_id: str = ""

    @field_validator("question")
    @classmethod
    def question_is_nfc(cls, v: str) -> str:
        if unicodedata.normalize("NFC", v) != v:
            raise ValueError(f"question not NFC: {v!r}")
        return v


class AdversarialItem(GoldenItem):
    adv_type: Literal["no_answer", "false_premise", "injection", "vague_prediction"]
    expected_behavior: Literal["abstain", "correct_premise", "resist", "clarify"]
    planted_snippet: str = ""  # পরীক্ষার সময় শুধু memory তে context এ যোগ হয়, কোনো collection এ নয়
    canary: str = ""  # উত্তরে এই লেখা এলে injection সফল

    @model_validator(mode="after")
    def injection_has_a_canary(self) -> "AdversarialItem":
        if self.adv_type == "injection" and not self.canary:
            raise ValueError(f"{self.id}: injection needs a canary")
        return self


def load_golden(path: Path) -> list[GoldenItem]:
    rows = [GoldenItem.model_validate_json(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len({r.id for r in rows}) != len(rows):
        raise ValueError("duplicate golden id")
    return rows


def load_adversarial(path: Path) -> list[AdversarialItem]:
    rows = [AdversarialItem.model_validate_json(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len({r.id for r in rows}) != len(rows):
        raise ValueError("duplicate adversarial id")
    return rows
