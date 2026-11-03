"""
api_v3.py
=========
Bangladesh Legal AI — v3 (LangGraph, Gemini + Groq)

Pipeline (প্রতিটা node নিচে আলাদা function):
  understanding     → intent + LLM rewrite (২-৩টা English/বাংলা sub-query) + মিসিং তথ্যের প্রশ্ন
  query_expansion   → আইনি alias dictionary (চেক ডিসঅনার → NI Act s.138 …), "Section N" lookup
  retrieval         → প্রতিটা query তে vector + BM25, সব ranking Reciprocal Rank Fusion এ মিলানো
  rerank            → bge-reranker-v2-m3 cross-encoder; threshold এর নিচের chunk বাদ
  sufficiency_check → কিছুই পাস না করলে broader query দিয়ে একবার retry
  generation        → <user_question>/<retrieved_sources> prompt, সব intent এ একই answer structure
  verifier          → citation (structured আর text এর ভেতরের), persona দাবি, procedure/case completeness;
                      সমস্যা থাকলে একবার regenerate
  finalize          → unverified citation সরানো, answer normalize, sources
Deterministic নিয়মগুলো (alias, intent, persona, schema) legal_rules.py তে।
"""

import json
import os
import re
import sys
import time
import warnings
from collections import Counter, defaultdict
from typing import Optional, TypedDict

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json as _json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class UTF8JSONResponse(JSONResponse):
    """ensure_ascii=False — Bangla characters সঠিকভাবে render হবে।"""
    def render(self, content) -> bytes:
        return _json.dumps(
            content, ensure_ascii=False, allow_nan=False, indent=None
        ).encode("utf-8")


import chromadb
import torch
from chromadb.utils import embedding_functions
from groq import Groq
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

from langgraph.graph import END, StateGraph

from legal_rules import (
    BN_DIGITS,
    PRIORITY_ACTS,
    detect_acts,
    detect_section_request,
    expand_query,
    find_persona_claims,
    inline_case_refs,
    inline_section_mentions,
    is_only_question,
    normalize_answer,
    procedure_gaps,
    resolve_intent,
    section_label,
    section_numbers_in,
    strip_persona_sentences,
    validate_follow_up_options,
)

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
CHROMA_DB_PATH  = os.getenv("CHROMA_DB_PATH", "./legal_chroma_db")
EMBED_MODEL     = "paraphrase-multilingual-MiniLM-L12-v2"
GROQ_MODEL      = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")   # llama-3.3-70b-versatile Groq থেকে সরানো হয়েছে
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
LLM_TEMPERATURE = 0.1

RERANK_MODEL      = "BAAI/bge-reranker-v2-m3"
RERANK_MAX_LENGTH = 256
# English rewrite এর বিপরীতে probe এ প্রাসঙ্গিক law/judgement 0.29–0.74, অপ্রাসঙ্গিক ≤ 0.03 পেয়েছিল
RERANK_THRESHOLD  = 0.05
RERANK_CANDIDATES = 6    # fused ranking থেকে প্রতি collection এ এতগুলো reranker এ যায় (CPU তে ~0.4s/pair)

N_RETRIEVE      = 12   # প্রতি query, প্রতি method (vector/BM25)
N_RETRIEVE_WIDE = 25   # retry তে
N_LAWS_FINAL    = 5
N_CASES_FINAL   = 4    # LLM context এ; answer এ সর্বোচ্চ MAX_CASES_IN_ANSWER
MAX_CASES_IN_ANSWER = 3
MAX_QUERIES     = 5    # rewritten + ≤3 sub-query + alias expansion
MAX_CHUNK_LEN   = 1200
MAX_HISTORY     = 6
RRF_K           = 60   # reciprocal rank fusion constant
MAX_RETRIEVAL_RETRY = 1
MAX_ANSWER_RETRY    = 1
# ──────────────────────────────────────────────────────────

app = FastAPI(
    title="Bangladesh Legal AI v3",
    version="3.1.0",
    default_response_class=UTF8JSONResponse,
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ─── SETUP: EMBEDDINGS + CHROMA ───────────────────────────
embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

judgements_col = chroma_client.get_collection(name="bangladesh_judgements", embedding_function=embedding_fn)
laws_col = chroma_client.get_collection(name="bangladesh_laws", embedding_function=embedding_fn)

# ─── SETUP: RERANKER ──────────────────────────────────────
reranker = CrossEncoder(RERANK_MODEL, max_length=RERANK_MAX_LENGTH)
if not torch.cuda.is_available():
    # CPU তে fp32 এ ~2.1s/pair; int8 dynamic quantization এ ~0.4s/pair, probe এ score প্রায় একই
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        torch.ao.quantization.quantize_dynamic(reranker.model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)

# ─── SETUP: LLM CLIENTS ───────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

gemini_client = None
if GEMINI_AVAILABLE and GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

print(f"✅ Judgements: {judgements_col.count()} chunks")
print(f"✅ Laws: {laws_col.count()} chunks")
print(f"✅ Reranker: {RERANK_MODEL} ({'cuda' if torch.cuda.is_available() else 'cpu int8'})")
print(f"✅ Gemini: {'ready (' + GEMINI_MODEL + ')' if gemini_client else 'not configured — Groq fallback only'}")
print(f"✅ Groq: {'ready (' + GROQ_MODEL + ')' if groq_client else 'not configured'}")


# ─── SETUP: BM25 (in-memory, built from Chroma contents at startup) ───

# \w একা বাংলা কার/চিহ্ন (ে, া, ্) ধরে না — 'দেনমোহর' ভেঙে 'দ', 'নম', 'হর' হয়ে যেত।
# তাই পুরো Bengali block (U+0980–09FF) আর ZWNJ/ZWJ আলাদা করে যোগ করা।
_TOKEN_RE = re.compile(r"[\wঀ-৿‌‍]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Punctuation বাদ দিয়ে token বের করে — plain .split() হলে '302.' আর user এর
    লেখা '302' কখনো match করে না (BM25 exact string comparison করে), সব
    section-number lookup fail করে।"""
    return _TOKEN_RE.findall(text.lower())


def _load_bm25_index(collection) -> dict:
    """Batch করে load করো — 45K+ chunks এ SQLite variable limit (999) exceed হয়।"""
    all_docs = []
    all_metas = []
    batch_size = 500
    offset = 0
    total = collection.count()

    print(f"   Loading {total} documents in batches of {batch_size}...")
    while offset < total:
        batch = collection.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        all_docs.extend(batch["documents"])
        all_metas.extend(batch["metadatas"])
        offset += batch_size

    tokenized = [_tokenize(d) for d in all_docs]
    bm25 = BM25Okapi(tokenized) if tokenized else None
    return {"bm25": bm25, "docs": all_docs, "metas": all_metas}


print("⚙️  BM25 keyword index তৈরি হচ্ছে (judgements + laws)...")
judgements_bm25 = _load_bm25_index(judgements_col)
laws_bm25 = _load_bm25_index(laws_col)
print("✅ BM25 index ready")


def _norm_cite(s) -> str:
    """Citation compare এর জন্য — lowercase, বাংলা অঙ্ক → ASCII, space/punctuation বাদ।
    'The Penal Code, 1860' আর 'The Penal Code 1860' যেন একই ধরা হয়।"""
    return "".join(_TOKEN_RE.findall(str(s or "").translate(BN_DIGITS).lower()))


# LLM যে Act নাম লেখে সেটা থেকে act_id — targeted lookup এর জন্য
ACT_TITLES = {m["act_id"]: m["title"] for m in laws_bm25["metas"]}
_ACT_ID_BY_TITLE = {_norm_cite(title): act_id for act_id, title in ACT_TITLES.items()}

# (act_id, section_no) → section — BM25 এর জন্য সব আইন memory তে আছেই; Chroma তে metadata-filter
# get প্রতিবার ~১ সেকেন্ড নিত, alias pin আর section lookup এ কয়েকবার লাগে
LAW_SECTIONS: dict[tuple, tuple] = {}
LAWS_BY_SECTION_NO: dict[str, list] = defaultdict(list)
for _doc, _meta in zip(laws_bm25["docs"], laws_bm25["metas"]):
    if _meta.get("section_no"):
        _key = (_meta["act_id"], _meta["section_no"])
        if _key not in LAW_SECTIONS:
            LAW_SECTIONS[_key] = (_doc, _meta)
            LAWS_BY_SECTION_NO[_meta["section_no"]].append(_key)


# ─── PYDANTIC MODELS ──────────────────────────────────────

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    question: str
    history: list[Message] = []
    filters: Optional[dict] = None

class SearchRequest(BaseModel):
    query: str
    n_results: int = 10
    search_type: str = "both"   # "cases" (বা পুরনো নাম "judgements") | "laws" | "both"
    filters: Optional[dict] = None

class PredictionRequest(BaseModel):
    situation: str
    case_type: Optional[str] = None

class DocRequest(BaseModel):
    doc_type: str
    details: str


# ─── FILTER BUILDER ────────────────────────────────────────

def build_chroma_filter(filters: dict | None) -> dict | None:
    if not filters:
        return None
    conditions = []
    if "year_from" in filters:
        conditions.append({"year": {"$gte": int(filters["year_from"])}})
    if "year_to" in filters:
        conditions.append({"year": {"$lte": int(filters["year_to"])}})
    if "case_type" in filters:
        conditions.append({"case_type": {"$eq": filters["case_type"]}})
    if "outcome" in filters:
        conditions.append({"outcome": {"$eq": filters["outcome"]}})
    if "division" in filters:
        conditions.append({"division": {"$eq": filters["division"]}})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _meta_matches(meta: dict, filters: dict | None) -> bool:
    """build_chroma_filter এর Python version — BM25 index Chroma এর where-filter মানে না,
    তাই এটা ছাড়া filter এর বাইরের case BM25 দিয়ে ঢুকে পড়ত।"""
    if not filters:
        return True
    year = meta.get("year") or 0
    if "year_from" in filters and year < int(filters["year_from"]):
        return False
    if "year_to" in filters and year > int(filters["year_to"]):
        return False
    for key in ("case_type", "outcome", "division"):
        if key in filters and meta.get(key) != filters[key]:
            return False
    return True


# ─── HYBRID RETRIEVAL: VECTOR + BM25 + RECIPROCAL RANK FUSION ─────

def _bm25_search(bm25_data: dict, query: str, n: int = 15, filters: dict | None = None) -> list[dict]:
    if not bm25_data["bm25"]:
        return []
    scores = bm25_data["bm25"].get_scores(_tokenize(query))
    results = []
    for i in sorted(range(len(scores)), key=lambda i: scores[i], reverse=True):
        if scores[i] <= 0 or len(results) >= n:
            break
        if _meta_matches(bm25_data["metas"][i], filters):
            results.append({"text": bm25_data["docs"][i], "meta": bm25_data["metas"][i]})
    return results


def _vector_query(collection, query: str, n: int, where: dict | None = None) -> list[dict]:
    kwargs = {"query_texts": [query], "n_results": n, "include": ["documents", "metadatas", "distances"]}
    if where:
        kwargs["where"] = where
    r = collection.query(**kwargs)
    return [
        {"text": d, "meta": m, "vector_sim": 1 - dist}
        for d, m, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0])
    ]


def _key_of(item: dict) -> str:
    """একই judgement এর সব chunk একই key — fusion এ judgement একটা unit।"""
    meta = item["meta"]
    return meta.get("filename") or f'{meta.get("title","")}-{meta.get("section","")}' or item["text"][:50]


def _chunk_key(item: dict) -> str:
    """Reranker cache এর জন্য chunk-নির্দিষ্ট key — একই judgement এর অন্য chunk আলাদা score পায়।"""
    meta = item["meta"]
    if meta.get("filename"):
        return f'{meta["filename"]}#{meta.get("chunk_index", 0)}'
    return _key_of(item)


def _rrf(ranked_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """যেকোনো সংখ্যক ranking (vector/BM25 × প্রতিটা query) মিলিয়ে fused score। exact score না,
    শুধু rank ব্যবহার করে — তাই vector distance আর BM25 score এর scale ভিন্ন হলেও কাজ করে।"""
    scores = defaultdict(float)
    items = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            kk = _key_of(item)
            scores[kk] += 1 / (k + rank + 1)
            # একই judgement এর একাধিক chunk একই key পায় — প্রথম (ভালো rank এর) chunk টা রাখি
            items.setdefault(kk, item)
    for kk, item in items.items():
        item["rrf_score"] = scores[kk]
    return sorted(items.values(), key=lambda x: x["rrf_score"], reverse=True)


def _precedent_weight(meta: dict) -> float:
    """Real lawyer এর মতো precedent hierarchy — Appellate Division HCD-কে bind করে, নতুন judgment
    সামান্য বেশি authoritative ধরা হয়। শুধু vector similarity দিয়ে না, এই weight দিয়েও rank করা হয়।"""
    division_w = 1.15 if meta.get("division") == "AD" else 1.0
    year = meta.get("year") or 2000
    recency_w = 1.0 + max(0, min(year, 2026) - 1950) / (2026 - 1950) * 0.2
    return division_w * recency_w


_DEAD_SECTION_RE = re.compile(r"^\[?\s*(?:omitted|repealed|বিলুপ্ত)", re.IGNORECASE)


def _is_dead_section(meta: dict) -> bool:
    """'[Omitted]' / '[Repealed]' section — Article 102 প্রশ্নে এগুলোই উপরে উঠে আসছিল।"""
    return bool(_DEAD_SECTION_RE.match(meta.get("section", "")))


def _laws_ranked(query: str, n: int) -> list[list[dict]]:
    lists = []
    try:
        lists.append(_vector_query(laws_col, query, n))
    except Exception as e:
        print(f"⚠️ Laws vector retrieval error: {e}")
    lists.append(_bm25_search(laws_bm25, query, n=n))
    return lists


def _cases_ranked(query: str, n: int, filters: dict | None) -> list[list[dict]]:
    lists = []
    try:
        lists.append(_vector_query(judgements_col, query, n, build_chroma_filter(filters)))
    except Exception as e:
        print(f"⚠️ Judgements vector retrieval error: {e}")
    lists.append(_bm25_search(judgements_bm25, query, n=n, filters=filters))
    return lists


def _fuse_cases(ranked_lists: list[list[dict]]) -> list[dict]:
    fused = _rrf(ranked_lists)
    for item in fused:
        item["score"] = item["rrf_score"] * _precedent_weight(item["meta"])
    return sorted(fused, key=lambda x: x["score"], reverse=True)


def hybrid_retrieve_judgements(query: str, n: int, filters: dict | None, n_final: int = N_CASES_FINAL) -> list[dict]:
    return _fuse_cases(_cases_ranked(query, n, filters))[:n_final]


def hybrid_retrieve_laws(query: str, n: int, n_final: int = N_LAWS_FINAL) -> list[dict]:
    fused = [l for l in _rrf(_laws_ranked(query, n)) if not _is_dead_section(l["meta"])]
    for item in fused:
        item["score"] = item["rrf_score"]
    return fused[:n_final]


def _targeted_law(act_id: int, section_no: str) -> dict | None:
    """নির্দিষ্ট Act + section সরাসরি metadata দিয়ে — fuzzy ranking এর উপর নির্ভর না করে।
    প্রতিবার নতুন dict, কারণ rerank_score/exact flag item এর উপর বসে।"""
    found = LAW_SECTIONS.get((act_id, section_no))
    return {"text": found[0], "meta": found[1]} if found else None


def resolve_section_request(request: dict, query: str) -> tuple[list[dict], list[str]]:
    """'Section N' প্রশ্নে কোন Act এর section দেখাব: প্রশ্নে Act এর নাম থাকলে সেটা; না থাকলে
    PRIORITY_ACTS (Penal Code, CrPC, CPC, Evidence, NI Act, Constitution) এর প্রথমটা যেটায় এই
    section আছে। ফেরত দেয় (দেখানোর section, একই নম্বর আছে এমন অন্য Act এর নাম)।"""
    section_no = request["section_no"]
    for act_id in request["act_ids"]:
        found = _targeted_law(act_id, section_no)
        if found:
            return [found], []

    matches = [m for m in (_targeted_law(*key) for key in LAWS_BY_SECTION_NO.get(section_no, []))
               if not _is_dead_section(m["meta"])]
    by_act = {m["meta"]["act_id"]: m for m in matches}
    order = [a for a in PRIORITY_ACTS if a in by_act]
    if order:
        chosen = by_act[order[0]]
        also_in = [ACT_TITLES[a] for a in order[1:]]
    elif matches:
        # কোনো সাধারণ Act এ নেই — প্রশ্নের সাথে সবচেয়ে কাছেরটা
        ranked = _vector_query(laws_col, query, 3, where={"section_no": {"$eq": section_no}})
        if not ranked:
            return [], []
        chosen = ranked[0]
        also_in = [r_["meta"]["title"] for r_ in ranked[1:]]
    else:
        return [], []
    others = len(matches) - 1 - len(also_in)
    if others > 0:
        also_in.append(f"আরও {others}টা আইন")
    return [chosen], also_in


# ─── RERANKER ─────────────────────────────────────────────

def _rerank_text(item: dict) -> str:
    return item["text"][:2000]


def rerank(query: str, items: list[dict], cache: dict) -> list[dict]:
    """Cross-encoder score বসায় (cache এ আগে থাকলে আবার না), threshold এর নিচেরগুলো বাদ।
    exact section lookup (user নিজে যে ধারা চেয়েছে) score ছাড়াই পাস।"""
    todo = [it for it in items if not it.get("exact") and _chunk_key(it) not in cache]
    if todo:
        scores = reranker.predict([(query, _rerank_text(it)) for it in todo], batch_size=8,
                                  show_progress_bar=False)
        for it, s in zip(todo, scores):
            cache[_chunk_key(it)] = float(s)
    for it in items:
        it["rerank_score"] = 1.0 if it.get("exact") else cache[_chunk_key(it)]
    passed = [it for it in items if it["rerank_score"] >= RERANK_THRESHOLD]
    return sorted(passed, key=lambda it: it["rerank_score"], reverse=True)


# ─── CONTRADICTION DETECTION ───────────────────────────────

PETITIONER_WON  = {"rule_absolute", "allowed"}
PETITIONER_LOST = {"rule_discharged", "dismissed"}


def detect_contradictions(judgements: list[dict]) -> list[dict]:
    """একই subject এ কিছু case এ petitioner জিতেছে আর কিছুতে হেরেছে কিনা দেখে —
    real lawyer conflicting precedent লুকায় না, explain করে।
    'unknown'/'disposed' conflict ধরা হয় না — outcome জানা না থাকা আর ভিন্ন রায় এক
    জিনিস না (~54% judgement এর outcome unknown)। 'general'/'other' subject ও বাদ।"""
    by_subject = defaultdict(list)
    for j in judgements:
        if j["meta"].get("outcome") not in PETITIONER_WON | PETITIONER_LOST:
            continue
        for subj in (j["meta"].get("subjects") or "").split(","):
            subj = subj.strip()
            if subj and subj not in ("general", "other"):
                by_subject[subj].append(j)

    conflicts = []
    for subj, cases in by_subject.items():
        outcomes = {c["meta"]["outcome"] for c in cases}
        if outcomes & PETITIONER_WON and outcomes & PETITIONER_LOST:
            conflicts.append({"subject": subj, "cases": cases, "outcomes": sorted(outcomes)})
    return conflicts


# ─── OUTCOME STATISTICS ────────────────────────────────────

OUTCOME_BN = {
    "rule_absolute":   "Rule absolute — আবেদনকারীর পক্ষে রায়",
    "rule_discharged": "Rule discharged — আবেদন খারিজ",
    "allowed":         "আপিল/আবেদন মঞ্জুর",
    "dismissed":       "আপিল/আবেদন খারিজ",
    "disposed":        "নিষ্পত্তি (disposed of)",
}


def compute_outcome_stats(judgements: list[dict]) -> dict:
    if not judgements:
        return {}
    outcomes = Counter(j["meta"].get("outcome", "unknown") for j in judgements)
    total = sum(outcomes.values())
    petitioner_won = sum(outcomes[o] for o in PETITIONER_WON)
    petitioner_lost = sum(outcomes[o] for o in PETITIONER_LOST)
    years = [j["meta"].get("year", 0) for j in judgements if j["meta"].get("year", 0) > 1950]
    subjects = []
    for j in judgements:
        subjects.extend((j["meta"].get("subjects") or "").split(","))
    common_subjects = [s for s, _ in Counter(subjects).most_common(3) if s.strip()]

    return {
        "total_similar_cases": total,
        "petitioner_won": petitioner_won,
        "petitioner_lost": petitioner_lost,
        "disposed_settled": outcomes.get("disposed", 0),
        "win_rate_pct": round(petitioner_won / total * 100) if total else 0,
        "year_range": f"{min(years)}–{max(years)}" if years else "N/A",
        "common_subjects": common_subjects,
    }


# ─── CONTEXT BUILDER ────────────────────────────────────────

def build_sources_block(retrieved: dict) -> str:
    lines = []
    if retrieved["laws"]:
        lines.append("## Laws\n")
        for law in retrieved["laws"]:
            meta = law["meta"]
            lines.append(
                f"### Act: {meta.get('title', '')}\n"
                f"Section: {section_label(meta)}\n"
                f"Year: {meta.get('year', '')} | Source: {meta.get('url', '')}\n"
                f"{law['text'][:MAX_CHUNK_LEN]}\n"
            )
    if retrieved["judgements"]:
        lines.append("\n## Judgements (Bangladesh Supreme Court)\n")
        for j in retrieved["judgements"]:
            meta = j["meta"]
            # 'Case:' লাইন আলাদা রাখা — LLM এটাই হুবহু case_no হিসেবে copy করবে, verifier মেলাবে
            lines.append(
                f"### Case: {meta.get('case_no', meta.get('filename', ''))}\n"
                f"Year: {meta.get('year', '?')} | Division: {meta.get('division', 'HCD')} | "
                f"Outcome: {meta.get('outcome', 'unknown')} | Subjects: {meta.get('subjects', '')}\n"
                f"{j['text'][:MAX_CHUNK_LEN]}\n"
            )
    return "\n".join(lines)


# ─── LLM CALLS: GEMINI (primary) + GROQ (fallback) ─────────

def _call_gemini(system_prompt: str, user_content: str, json_mode: bool, schema=None) -> str:
    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json" if json_mode else "text/plain",
        # schema দিলে Gemini constrained decoding করে — JSON mode একা থাকতে flash-lite মাঝে মাঝে
        # ভাঙা JSON দিত ('holding\":'), পুরো উত্তর short_answer এ raw text হয়ে যেত
        response_schema=schema,
        temperature=LLM_TEMPERATURE,
    )
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_content,
        config=config,
    )
    return response.text


def _call_groq(system_prompt: str, user_content: str, json_mode: bool) -> str:
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=LLM_TEMPERATURE,
        max_tokens=6000,
        **({"response_format": {"type": "json_object"}} if json_mode else {}),
    )
    return response.choices[0].message.content


def call_llm(system_prompt: str, user_content: str, json_mode: bool = True, schema=None) -> str:
    """Gemini আগে try করে, fail করলে বা configured না থাকলে Groq এ fallback করে।"""
    if gemini_client:
        try:
            return _call_gemini(system_prompt, user_content, json_mode, schema)
        except Exception as e:
            print(f"⚠️ Gemini error, Groq এ fallback করছি: {e}")
    if groq_client:
        return _call_groq(system_prompt, user_content, json_mode)
    raise RuntimeError("কোনো LLM configured না — GEMINI_API_KEY অথবা GROQ_API_KEY দাও .env এ")


def safe_parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"summary": text, "error": "JSON parse failed — raw response"}


# ─── RESPONSE SCHEMAS (Gemini constrained decoding) ─────────

class _FollowUp(BaseModel):
    question: str
    options: list[str]

class RouterOutput(BaseModel):
    rewritten_query: str
    sub_queries: list[str]
    intent: str
    follow_up_options: list[_FollowUp]

class _LawCitation(BaseModel):
    act: str
    section: str
    what_it_says: str
    how_it_applies: str

class _CaseCitation(BaseModel):
    case_no: str
    year: Optional[int]
    division: str
    holding: str

class _Procedure(BaseModel):
    steps: list[str]
    court: str
    deadline: str
    documents: list[str]
    cost: str
    penalties: str

class AnswerOutput(BaseModel):
    short_answer: str
    applicable_laws: list[_LawCitation]
    relevant_cases: list[_CaseCitation]
    procedure: _Procedure
    risks: list[str]
    when_to_see_lawyer: str
    follow_up_options: list[_FollowUp]


# ─── SYSTEM PROMPTS ─────────────────────────────────────────

QUERY_UNDERSTANDING_PROMPT = """তুমি Bangladesh legal research system এর query router। ইউজারের প্রশ্ন এবং conversation history দেখে ঠিক এই JSON দাও, আর কিছু না:

{
  "rewritten_query": "history অনুযায়ী pronoun/reference resolve করা standalone প্রশ্ন, user এর ভাষায়",
  "sub_queries": ["English legal search query", "বাংলা search query", "optional: অন্য দিক থেকে আরেকটা English query"],
  "intent": "law_lookup" | "case_search" | "prediction" | "procedure" | "general",
  "follow_up_options": [
    {"question": "মিসিং fact সম্পর্কে প্রশ্ন", "options": ["সংক্ষিপ্ত option ১", "option ২", "option ৩"]}
  ]
}

Rules:
- sub_queries: ২-৩টা। অন্তত একটা English, আইনি পরিভাষায় — নিশ্চিত হলে সম্ভাব্য Act আর section সহ
  (যেমন "dishonour of cheque Negotiable Instruments Act 1881 section 138 complaint procedure")।
  অন্তত একটা বাংলায়। Banglish প্রশ্ন হলে ঠিক বাংলা/ইংরেজিতে লেখো।
- intent:
  law_lookup  = user শুধু একটা নির্দিষ্ট আইন/ধারা কী বলে জানতে চায়, নিজের পরিস্থিতি না
  procedure   = কীভাবে মামলা/আবেদন/প্রক্রিয়া করতে হয়, কী পদক্ষেপ নিতে হবে
  prediction  = নিজের মামলার সম্ভাব্য ফলাফল
  case_search = রায়/নজির খুঁজছে
  general     = বাকি সব (অধিকার, প্রতিকার, সাধারণ আইনি প্রশ্ন)
- follow_up_options: prediction/procedure এ উত্তর আরও নির্দিষ্ট করতে যে তথ্য দরকার তার ১-৩টা প্রশ্ন,
  প্রতিটায় ২-৪টা ছোট mutually-exclusive option (পূর্ণ বাক্য না)। open-ended হলে options খালি রাখো।
  দরকার না হলে খালি array।
- JSON ছাড়া কিছু লিখবে না, কোনো preamble বা markdown না"""


SYSTEM_PROMPT = """তুমি একটা AI legal research assistant — Bangladesh এর আইন আর Supreme Court এর রায় থেকে উত্তর দাও। তুমি মানুষ বা আইনজীবী নও।

কঠোর নিয়ম:
1. নিজের অভিজ্ঞতা, কত বছরের practice, বার কাউন্সিল সদস্যপদ, বা নিজে মামলা পরিচালনার কথা কখনো লিখবে না
   ("আমার ৩০ বছরের অভিজ্ঞতায়", "আমি এমন মামলা পরিচালনা করেছি", "in my 20 years" — সব নিষিদ্ধ)।
2. শুধু <retrieved_sources> এর আইন, ধারা আর মামলা cite করবে — structured field এ এবং text এর ভেতরেও।
   act = "Act:" লাইনের পরের অংশ হুবহু, section = "Section:" লাইনের পরের অংশ হুবহু,
   case_no = "Case:" লাইনের পরের অংশ হুবহু ("Act:"/"Section:"/"Case:" শব্দটা নিজে লিখবে না)।
   Sources এ নেই এমন কোনো ধারা/অনুচ্ছেদ নম্বর বা মামলা লিখবে না, অনুমানও করবে না।
3. User শুধু <user_question> এ যা লিখেছে সেটাই বলেছে। Sources এর কোনো তথ্য (নাম, জেলা, ঘটনা, তারিখ)
   "আপনি বলেছেন" / "আপনার ক্ষেত্রে" বলে user এর উপর চাপাবে না।
4. Filler বাদ — ভূমিকা, সহানুভূতির বাক্য, নিজের পরিচয়, একই কথা দুইবার। সরাসরি তথ্য।
5. User যে ভাষায় লিখেছে সেই ভাষায় উত্তর (Banglish হলে বাংলায়)।
6. Sources এ কোনো তথ্য না থাকলে (যেমন court fee এর অঙ্ক) বানাবে না — লিখবে "sources এ উল্লেখ নেই"।

Output — শুধু এই JSON, এই ক্রমে (সব intent এ একই structure):
{
  "short_answer": "২-৪ বাক্যে সরাসরি উত্তর",
  "applicable_laws": [{"act": "...", "section": "...", "what_it_says": "১-২ বাক্য", "how_it_applies": "এই প্রশ্নে কেন প্রযোজ্য"}],
  "relevant_cases": [{"case_no": "...", "year": 2020, "division": "HCD", "holding": "আদালত কী সিদ্ধান্ত দিয়েছে — এক লাইনে"}],
  "procedure": {"steps": ["1. ...", "2. ..."], "court": "কোন আদালত/কর্তৃপক্ষ", "deadline": "সময়সীমা",
                "documents": ["..."], "cost": "খরচ/ফি", "penalties": "শাস্তি বা আইনি পরিণতি"},
  "risks": ["..."],
  "when_to_see_lawyer": "কখন ও কেন আইনজীবীর কাছে যাওয়া দরকার",
  "follow_up_options": [{"question": "...", "options": ["...", "..."]}]
}

Intent অনুযায়ী:
- law_lookup: ধারাটা কী বলে সেটাই মূল উত্তর; procedure/risks প্রাসঙ্গিক না হলে খালি রাখো ({} / [])।
  <section_lookup> থাকলে শুধু ওই Act এর জন্য উত্তর দাও; অন্য Act গুলোর নাম short_answer এর শেষে এক লাইনে, তার বেশি না।
- procedure: procedure.steps (ক্রমিক নম্বরসহ), court, deadline, documents, penalties — সবগুলো ভরাট বাধ্যতামূলক।
  উত্তর শুধু একটা প্রশ্ন দিয়ে শেষ করবে না।
- prediction: তথ্য কম হলেও আগে প্রযোজ্য আইনি কাঠামো, সম্ভাব্য পথ আর প্রতিটার শর্ত citation সহ দাও;
  তারপর follow_up_options এ মিসিং তথ্য জিজ্ঞাসা করো। জেতা/হারার শতাংশ বা নিশ্চয়তা দিবে না।
  শুধু প্রশ্ন ফেরত দেওয়া নিষেধ।
- law_lookup ছাড়া সব intent: relevant_cases এ sources থেকে সর্বোচ্চ ৩টা প্রাসঙ্গিক judgement।
  holding লিখবে শুধু case এর text আর Outcome থেকে।

JSON এর বাইরে একটা অক্ষরও লিখবে না।"""


# ─── LANGGRAPH STATE ──────────────────────────────────────

class GraphState(TypedDict, total=False):
    question: str
    history: list
    filters: Optional[dict]
    forced_intent: Optional[str]
    rewritten_query: str
    sub_queries: list
    intent: str
    follow_up_options: list
    alias_hits: list
    queries: list
    rerank_query: str
    section_exact: list
    section_also_in: list
    candidates: dict
    rerank_cache: dict
    retrieved: dict
    contradictions: list
    outcome_stats: dict
    max_rerank_score: float
    retrieval_sufficient: bool
    retrieval_retry: int
    structured_answer: dict
    citation_valid: bool
    citation_issues: dict
    answer_issues: list
    answer_retry: int
    unverified_citations: list
    sources: dict


# ─── LANGGRAPH NODES ───────────────────────────────────────

def _is_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.8


def _unique(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        key = it.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(it.strip())
    return out


def understanding_node(state: GraphState) -> dict:
    history_text = "\n".join(f"{m['role']}: {m['content']}" for m in state.get("history", [])[-MAX_HISTORY:])
    prompt = f"Conversation history:\n{history_text}\n\nবর্তমান প্রশ্ন: {state['question']}"
    try:
        parsed = safe_parse_json(call_llm(QUERY_UNDERSTANDING_PROMPT, prompt, json_mode=True, schema=RouterOutput))
    except Exception as e:
        print(f"⚠️ Query understanding LLM error, keyword fallback: {e}")
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    sub_queries = [q for q in (parsed.get("sub_queries") or []) if isinstance(q, str) and q.strip()][:3]
    return {
        "rewritten_query": parsed.get("rewritten_query") or state["question"],
        "sub_queries": sub_queries,
        "intent": resolve_intent(state["question"], parsed.get("intent"), state.get("forced_intent")),
        "follow_up_options": validate_follow_up_options(parsed.get("follow_up_options")),
        "retrieval_retry": 0,
        "answer_retry": 0,
        "rerank_cache": {},
    }


def query_expansion_node(state: GraphState) -> dict:
    """Alias dictionary + LLM sub-query মিলিয়ে retrieval query list, আর reranker এর query।
    Reranker বাংলা/Banglish প্রশ্ন আর ইংরেজি আইনের text মেলাতে পারে না (probe এ সঠিক NI Act
    s.138 পেয়েছিল 0.003), তাই rerank query = English sub-query + alias expansion।"""
    question = state["question"]
    rewritten = state.get("rewritten_query") or question
    hits = expand_query(f"{question}\n{rewritten}")
    sub_queries = state.get("sub_queries") or []

    english = next((q for q in sub_queries if _is_latin(q)), "")
    base = english or (rewritten if _is_latin(rewritten) else question)
    rerank_query = " ".join([base, *[h["expansion"] for h in hits]])

    alias_query = " ".join(h["expansion"] for h in hits)
    queries = _unique([rewritten, *sub_queries, alias_query])[:MAX_QUERIES]

    section_exact, also_in = [], []
    request = detect_section_request(question) or detect_section_request(rewritten)
    if request:
        section_exact, also_in = resolve_section_request(request, english or rewritten)
        for item in section_exact:
            item["exact"] = True

    return {"alias_hits": hits, "queries": queries, "rerank_query": rerank_query,
            "section_exact": section_exact, "section_also_in": also_in}


def retrieval_node(state: GraphState) -> dict:
    attempt = state.get("retrieval_retry", 0)
    intent = state["intent"]
    n = N_RETRIEVE if attempt == 0 else N_RETRIEVE_WIDE
    filters = state.get("filters") if attempt == 0 else None   # retry তে filter সরিয়ে broader search
    queries = state["queries"] if attempt == 0 else _unique([state["question"], *state["queries"]])

    law_lists, case_lists = [], []
    for q in queries:
        law_lists += _laws_ranked(q, n)
        if intent != "law_lookup":   # lookup ছাড়া সব intent এ judgements লাগবে
            case_lists += _cases_ranked(q, n, filters)

    laws = [l for l in _rrf(law_lists) if not _is_dead_section(l["meta"])]
    exact = state.get("section_exact") or []
    if exact and intent == "law_lookup":
        # Section lookup এ শুধু বেছে নেওয়া Act — অন্য Act এর একই নম্বরের section বাদ
        laws = [l for l in laws if l["meta"].get("act_id") == exact[0]["meta"].get("act_id")]

    pinned = []
    for hit in state.get("alias_hits") or []:
        for act_id, section_no in hit["sections"]:
            found = _targeted_law(act_id, section_no)
            if found:
                pinned.append(found)

    seen, law_candidates = set(), []
    for item in exact + pinned + laws[:RERANK_CANDIDATES]:
        if _key_of(item) not in seen:
            seen.add(_key_of(item))
            law_candidates.append(item)

    return {
        "candidates": {"laws": law_candidates, "judgements": _fuse_cases(case_lists)[:RERANK_CANDIDATES]},
        "retrieval_retry": attempt + 1,
    }


def rerank_node(state: GraphState) -> dict:
    cache = dict(state.get("rerank_cache") or {})
    candidates = state["candidates"]
    laws = rerank(state["rerank_query"], candidates["laws"], cache)[:N_LAWS_FINAL]
    cases = rerank(state["rerank_query"], candidates["judgements"], cache)[:N_CASES_FINAL]
    return {
        "retrieved": {"laws": laws, "judgements": cases},
        "rerank_cache": cache,
        "contradictions": detect_contradictions(cases),
        "outcome_stats": compute_outcome_stats(cases),
    }


def sufficiency_check_node(state: GraphState) -> dict:
    retrieved = state["retrieved"]
    scores = [it["rerank_score"] for it in retrieved["laws"] + retrieved["judgements"]]
    return {
        "max_rerank_score": max(scores, default=0.0),
        "retrieval_sufficient": bool(scores),
    }


def route_after_check(state: GraphState) -> str:
    if not state["retrieval_sufficient"] and state["retrieval_retry"] <= MAX_RETRIEVAL_RETRY:
        return "retrieval"
    return "generation"


def _insufficient_answer(state: GraphState) -> dict:
    """Reranker এ কিছুই পাস করেনি — LLM কে খালি context দিয়ে অনুমান করতে না দিয়ে সরাসরি বলা।"""
    options = state.get("follow_up_options") or [{
        "question": "প্রশ্নটা কোন আইনি বিষয়ে?",
        "options": ["জমি/সম্পত্তি", "পারিবারিক", "ফৌজদারি মামলা", "চাকরি/শ্রম"],
    }]
    return normalize_answer({
        "short_answer": ("এই প্রশ্নের জন্য database এ (Bangladesh এর আইন ও Supreme Court এর রায়) যথেষ্ট প্রাসঙ্গিক "
                         "উৎস পাওয়া যায়নি, তাই অনুমান করে উত্তর দেওয়া হচ্ছে না। কোন আইন, কী ঘটেছে, কোথায় — "
                         "এগুলো উল্লেখ করে আবার জিজ্ঞাসা করুন।"),
        "when_to_see_lawyer": "বিষয়টা জরুরি হলে বা সময়সীমা থাকলে সরাসরি একজন আইনজীবীর সাথে কথা বলুন।",
        "follow_up_options": options,
    })


def _generation_input(state: GraphState) -> str:
    notes = []
    exact = state.get("section_exact") or []
    if exact and state["intent"] == "law_lookup":
        notes.append(
            f"<section_lookup>user যে ধারা চেয়েছে: {exact[0]['meta']['title']} — {section_label(exact[0]['meta'])}। "
            f"একই নম্বরের section আরও আছে: {', '.join(state.get('section_also_in') or []) or 'নেই'}।</section_lookup>"
        )
    for c in state.get("contradictions") or []:
        case_list = ", ".join(j["meta"].get("case_no", j["meta"].get("filename", "?")) for j in c["cases"])
        notes.append(f"<conflicting_precedent>Subject {c['subject']}: ভিন্ন outcome ({', '.join(c['outcomes'])}) — "
                     f"{case_list}। risks এ উল্লেখ করো।</conflicting_precedent>")
    if state.get("follow_up_options") and state["intent"] in ("prediction", "procedure"):
        notes.append("<missing_facts>উত্তরের শেষে follow_up_options এ এগুলো জিজ্ঞাসা করা যায়: "
                     f"{json.dumps(state['follow_up_options'], ensure_ascii=False)}</missing_facts>")
    if state.get("answer_retry", 0) > 0:
        problems = list(state.get("answer_issues") or [])
        issues = state.get("citation_issues") or {}
        if any(issues.values()):
            problems.append("এই citation গুলো sources এ নেই — বাদ দাও বা sources এর citation ব্যবহার করো: "
                            + json.dumps(issues, ensure_ascii=False))
        notes.append("<previous_answer_problems>\n" + "\n".join(f"- {p}" for p in problems)
                     + "\n</previous_answer_problems>")

    rewritten = state.get("rewritten_query") or ""
    resolved = (f"\n<resolved_question>{rewritten}</resolved_question>"
                if rewritten and rewritten.strip() != state["question"].strip() else "")
    return (
        f"<intent>{state['intent']}</intent>\n"
        f"<user_question>\n{state['question']}\n</user_question>{resolved}\n"
        f"<retrieved_sources>\n{build_sources_block(state['retrieved'])}\n</retrieved_sources>\n"
        + "\n".join(notes)
    )


def generation_node(state: GraphState) -> dict:
    if not state.get("retrieval_sufficient"):
        return {"structured_answer": _insufficient_answer(state)}
    raw = call_llm(SYSTEM_PROMPT, _generation_input(state), json_mode=True, schema=AnswerOutput)
    return {"structured_answer": normalize_answer(safe_parse_json(raw))}


def _match_law(cited: dict, laws: list[dict]) -> dict | None:
    """Act হুবহু মিলতে হবে; section মিলবে number দিয়ে ('Section 302' ↔ section_no 302)
    অথবা heading দিয়ে ('… Punishment for murder')।"""
    act = _norm_cite(cited.get("act"))
    section = str(cited.get("section") or "").translate(BN_DIGITS).lower()
    num = re.search(r"\d+[a-z]?", section)
    section_norm = _norm_cite(section)
    for law in laws:
        meta = law["meta"]
        if not act or _norm_cite(meta.get("title")) != act:
            continue
        section_no = _norm_cite(meta.get("section_no"))
        heading = _norm_cite(meta.get("section"))
        if (num and section_no and num.group() == section_no) or (heading and heading in section_norm):
            return law
    return None


def _act_id_for(act_name: str) -> int | None:
    act_id = _ACT_ID_BY_TITLE.get(_norm_cite(act_name))
    if act_id is None:
        found = detect_acts(act_name or "")
        act_id = found[0] if found else None
    return act_id


def _targeted_lookup_for_citation(cited: dict, rerank_query: str, cache: dict) -> dict | None:
    """LLM এমন Act/section cite করেছে যা retrieve হয়নি (যেমন NI Act s.138) — DB তে আসলেই আছে
    কিনা খুঁজি, পেলে reranker এ পাস করলে তবেই রাখি।"""
    act_id = _act_id_for(cited.get("act", ""))
    num = re.search(r"\d+[A-Za-z]?", str(cited.get("section") or "").translate(BN_DIGITS))
    if act_id is None or not num:
        return None
    found = _targeted_law(act_id, num.group().upper() if num.group()[-1].isalpha() else num.group())
    if not found or _is_dead_section(found["meta"]):
        return None
    passed = rerank(rerank_query, [found], cache)
    return passed[0] if passed else None


def _case_matches(cited_case_no: str, judgements: list[dict]) -> dict | None:
    target = _norm_cite(cited_case_no)
    for j in judgements:
        if target and target == _norm_cite(j["meta"].get("case_no") or j["meta"].get("filename")):
            return j
    return None


def _case_ref_exists(num: str, year: str, judgements: list[dict]) -> bool:
    """Retrieved judgement এর case_no তে, অথবা তার text এ (যেমন যে নিম্ন আদালতের মামলা থেকে
    revision এসেছে) এই নম্বর আছে কিনা।"""
    for j in judgements:
        cn = _norm_cite(j["meta"].get("case_no") or j["meta"].get("filename"))
        if f"{num}of{year}" in cn or f"{num}of{year[-2:]}" in cn or (num in cn and year[-2:] in cn):
            return True
        if f"{num}of{year}" in _norm_cite(j["text"][:MAX_CHUNK_LEN]):
            return True
    return False


def verifier_node(state: GraphState) -> dict:
    answer = state["structured_answer"]
    retrieved = state["retrieved"]
    attempt = state.get("answer_retry", 0) + 1
    if not state.get("retrieval_sufficient"):
        # deterministic "পাওয়া যায়নি" উত্তর — কিছু cite করা হয়নি
        return {"citation_valid": True, "citation_issues": {}, "answer_issues": [], "answer_retry": attempt}

    cache = dict(state.get("rerank_cache") or {})
    laws = list(retrieved["laws"])
    verified_laws, bad_laws = [], []
    for cited in answer["applicable_laws"]:
        match = _match_law(cited, laws) or _targeted_lookup_for_citation(cited, state["rerank_query"], cache)
        if match is None:
            bad_laws.append(cited)
            continue
        if match not in laws:
            match["targeted"] = True
            laws.append(match)
        meta = match["meta"]
        verified_laws.append({**cited, "act": meta["title"], "section": section_label(meta), "url": meta.get("url", "")})

    verified_cases, bad_cases = [], []
    for cited in answer["relevant_cases"]:
        match = _case_matches(cited["case_no"], retrieved["judgements"])
        if match is None:
            bad_cases.append(cited)
            continue
        meta = match["meta"]
        verified_cases.append({**cited, "case_no": meta.get("case_no", meta.get("filename")),
                               "year": meta.get("year"), "division": meta.get("division", "HCD")})

    valid_numbers = {l["meta"].get("section_no") for l in laws if l["meta"].get("section_no")}
    for item in laws + retrieved["judgements"]:     # LLM যে text দেখেছে তার ভেতরের ধারা নম্বরও context
        valid_numbers |= section_numbers_in(item["text"][:MAX_CHUNK_LEN])
    bad_inline_sections = [{"inline_section": no, "field": path}
                           for path, no in inline_section_mentions(answer) if no not in valid_numbers]
    bad_inline_cases = [{"inline_case": f"{num} of {year}", "field": path}
                        for path, num, year in inline_case_refs(answer)
                        if not _case_ref_exists(num, year, retrieved["judgements"])]

    issues = []
    persona = find_persona_claims(answer)
    if persona:
        issues.append(f"ব্যক্তিগত অভিজ্ঞতার দাবি আছে — সম্পূর্ণ বাদ দাও: {persona}")
    if state["intent"] == "procedure" and procedure_gaps(answer):
        issues.append(f"procedure এর এই field গুলো খালি — ভরাট করো: {procedure_gaps(answer)}")
    if state["intent"] in ("procedure", "prediction") and is_only_question(answer):
        issues.append("উত্তরে শুধু প্রশ্ন — আগে আইনি কাঠামো, প্রযোজ্য আইন আর পথ দাও, তারপর প্রশ্ন")
    if state["intent"] != "law_lookup" and retrieved["judgements"] and not answer["relevant_cases"]:
        issues.append("sources এ প্রাসঙ্গিক judgement আছে কিন্তু relevant_cases খালি — সর্বোচ্চ ৩টা যোগ করো")

    citation_issues = {"bad_laws": bad_laws, "bad_cases": bad_cases,
                       "bad_inline_sections": bad_inline_sections, "bad_inline_cases": bad_inline_cases}
    return {
        "structured_answer": {**answer, "applicable_laws": verified_laws + bad_laws,
                              "relevant_cases": verified_cases + bad_cases},
        "retrieved": {**retrieved, "laws": laws},
        "rerank_cache": cache,
        "citation_valid": not any(citation_issues.values()),
        "citation_issues": citation_issues,
        "answer_issues": issues,
        "answer_retry": attempt,
    }


def route_after_verify(state: GraphState) -> str:
    needs_fix = not state["citation_valid"] or state.get("answer_issues")
    if needs_fix and state["answer_retry"] <= MAX_ANSWER_RETRY:
        problems = [*state.get("answer_issues", []),
                    *[f"{k}={len(v)}" for k, v in (state.get("citation_issues") or {}).items() if v]]
        print(f"   ↻ regenerate: {'; '.join(p[:120] for p in problems)}")
        return "generation"
    return "finalize"


def finalize_node(state: GraphState) -> dict:
    retrieved = state["retrieved"]
    answer = dict(state.get("structured_answer") or normalize_answer({}))
    issues = state.get("citation_issues") or {}

    # retry এর পরেও যে citation sources এ মেলেনি সেগুলো user এর কাছে যাওয়ার আগেই সরাই;
    # text এর ভেতরের unverified ধারা/মামলা নম্বর flag হয় (text কাটলে বাক্য ভেঙে যায়)
    bad_laws, bad_cases = issues.get("bad_laws", []), issues.get("bad_cases", [])
    answer["applicable_laws"] = [l for l in answer["applicable_laws"] if l not in bad_laws]
    answer["relevant_cases"] = [c for c in answer["relevant_cases"] if c not in bad_cases]
    unverified = (bad_laws + bad_cases + issues.get("bad_inline_sections", [])
                  + issues.get("bad_inline_cases", []))

    if find_persona_claims(answer):
        answer = strip_persona_sentences(answer)

    # law_lookup ছাড়া প্রতিটা উত্তরে সর্বোচ্চ ৩টা judgement — LLM না দিলে metadata থেকে
    if state["intent"] != "law_lookup" and state.get("retrieval_sufficient"):
        if not answer["relevant_cases"]:
            answer["relevant_cases"] = [
                {"case_no": j["meta"].get("case_no", j["meta"].get("filename")), "year": j["meta"].get("year"),
                 "division": j["meta"].get("division", "HCD"),
                 "holding": OUTCOME_BN.get(j["meta"].get("outcome"), "রায়ের ফলাফল metadata তে নেই")}
                for j in retrieved["judgements"]
            ]
        answer["relevant_cases"] = answer["relevant_cases"][:MAX_CASES_IN_ANSWER]

    sources = {
        "cases": [
            {
                "case_no": j["meta"].get("case_no", j["meta"].get("filename")),
                "year": j["meta"].get("year"),
                "type": j["meta"].get("case_type"),
                "outcome": j["meta"].get("outcome"),
                "division": j["meta"].get("division"),
                "subjects": j["meta"].get("subjects"),
                "rerank_score": round(j.get("rerank_score", 0), 4),
                "score": round(j.get("rerank_score", 0), 4),   # পুরনো client এর জন্য
            }
            for j in retrieved["judgements"][:MAX_CASES_IN_ANSWER]
        ],
        "laws": [
            {
                "title": l["meta"].get("title"),
                "section": section_label(l["meta"]),
                "url": l["meta"].get("url"),
                "year": l["meta"].get("year"),
                "rerank_score": round(l.get("rerank_score", 0), 4),
                "score": round(l.get("rerank_score", 0), 4),   # পুরনো client এর জন্য
                **({"targeted_lookup": True} if l.get("targeted") else {}),
            }
            for l in retrieved["laws"]
        ],
    }
    return {"sources": sources, "structured_answer": answer, "unverified_citations": unverified}


# ─── BUILD GRAPH ────────────────────────────────────────────

def _timed(name: str, fn):
    """প্রতিটা node কত সময় নিল server log এ — CPU তে reranker/LLM কোথায় সময় খাচ্ছে দেখার জন্য।"""
    def wrapper(state):
        started = time.perf_counter()
        out = fn(state)
        print(f"   ⏱ {name}: {time.perf_counter() - started:.1f}s")
        return out
    return wrapper


def build_graph():
    g = StateGraph(GraphState)
    for name, fn in [
        ("understanding", understanding_node), ("query_expansion", query_expansion_node),
        ("retrieval", retrieval_node), ("rerank", rerank_node), ("sufficiency_check", sufficiency_check_node),
        ("generation", generation_node), ("verifier", verifier_node), ("finalize", finalize_node),
    ]:
        g.add_node(name, _timed(name, fn))

    g.set_entry_point("understanding")
    g.add_edge("understanding", "query_expansion")
    g.add_edge("query_expansion", "retrieval")
    g.add_edge("retrieval", "rerank")
    g.add_edge("rerank", "sufficiency_check")
    g.add_conditional_edges("sufficiency_check", route_after_check, {"retrieval": "retrieval", "generation": "generation"})
    g.add_edge("generation", "verifier")
    g.add_conditional_edges("verifier", route_after_verify, {"generation": "generation", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()


legal_graph = build_graph()


def run_pipeline(question: str, history: list | None = None, filters: dict | None = None,
                 forced_intent: str | None = None) -> dict:
    init_state: GraphState = {"question": question, "history": history or [], "filters": filters,
                              "forced_intent": forced_intent}
    final_state = legal_graph.invoke(init_state)
    retrieved = final_state.get("retrieved") or {"laws": [], "judgements": []}
    answer = final_state.get("structured_answer") or normalize_answer({})
    return {
        # 'summary' = আগের schema র field নাম; পুরনো client ভাঙে না (SPEC: field বাদ দেওয়া যাবে না)
        "answer": {**answer, "summary": answer["short_answer"]},
        "sources": final_state.get("sources", {"cases": [], "laws": []}),
        "intent": final_state.get("intent"),
        "outcome_stats": final_state.get("outcome_stats", {}),
        "contradictions_detected": len(final_state.get("contradictions", [])),
        "citation_verified": bool(final_state.get("citation_valid")),
        "unverified_citations": final_state.get("unverified_citations", []),
        "retrieval_sufficient": bool(final_state.get("retrieval_sufficient")),
        "retrieval_attempts": final_state.get("retrieval_retry", 0),
        "answer_attempts": final_state.get("answer_retry", 0),
        "laws_retrieved": len(retrieved["laws"]),
        "cases_retrieved": len(retrieved["judgements"]),
    }


# ─── ENDPOINTS ────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "status": "Bangladesh Legal AI v3",
        "judgements": judgements_col.count(),
        "laws": laws_col.count(),
        "llm": {"primary": "gemini" if gemini_client else None, "fallback": "groq" if groq_client else None},
        "features": [
            "langgraph_pipeline",
            "legal_alias_query_expansion",
            "hybrid_retrieval_bm25_vector",
            "cross_encoder_rerank",
            "precedent_hierarchy_weighting",
            "sufficiency_check_retry_loop",
            "citation_verifier_retry_loop",
            "persona_claim_check",
            "contradiction_detection",
            "conversation_memory",
        ],
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question cannot be empty")
    try:
        return run_pipeline(question, [m.model_dump() for m in req.history], req.filters)
    except Exception as e:
        print(f"⚠️ /api/chat graph error: {e}")
        raise HTTPException(503, "LLM service temporarily unavailable — please try again in a moment")


@app.post("/api/predict")
def predict_outcome(req: PredictionRequest):
    situation = req.situation.strip()
    if not situation:
        raise HTTPException(400, "Situation cannot be empty")
    filters = {"case_type": req.case_type} if req.case_type else None
    try:
        result = run_pipeline(situation, [], filters, forced_intent="prediction")
    except Exception as e:
        print(f"⚠️ /api/predict graph error: {e}")
        raise HTTPException(503, "LLM service temporarily unavailable — please try again in a moment")
    return {
        **result,
        "prediction": result["answer"],
        "similar_cases_used": result["cases_retrieved"],
        "laws_referenced": result["laws_retrieved"],
    }


@app.post("/api/search")
def search(req: SearchRequest):
    search_type = {"judgements": "cases"}.get(req.search_type, req.search_type)
    if search_type not in ("cases", "laws", "both"):
        raise HTTPException(400, "search_type must be 'cases', 'laws' or 'both'")
    results = {"cases": [], "laws": []}

    if search_type in ("cases", "both"):
        for j in hybrid_retrieve_judgements(req.query, req.n_results, req.filters, n_final=req.n_results):
            results["cases"].append({
                "case_no": j["meta"].get("case_no", j["meta"].get("filename")),
                "year": j["meta"].get("year"),
                "type": j["meta"].get("case_type"),
                "outcome": j["meta"].get("outcome"),
                "division": j["meta"].get("division"),
                "subjects": j["meta"].get("subjects"),
                "judges": j["meta"].get("judges"),
                "preview": j["text"][:400],
                "score": round(j.get("score", 0), 4),
            })

    if search_type in ("laws", "both"):
        for l in hybrid_retrieve_laws(req.query, req.n_results, n_final=req.n_results):
            results["laws"].append({
                "title": l["meta"].get("title"),
                "section": section_label(l["meta"]),
                "url": l["meta"].get("url"),
                "year": l["meta"].get("year"),
                "type": l["meta"].get("act_type"),
                "preview": l["text"][:400],
                "score": round(l.get("score", 0), 4),
            })

    results["judgements"] = results["cases"]   # আগের response key — পুরনো client এর জন্য রাখা
    return results


@app.post("/api/generate-doc")
def generate_document(req: DocRequest):
    templates = {
        "nda": "Non-Disclosure Agreement (গোপনীয়তা চুক্তি)",
        "sale_deed": "Sale Deed (বিক্রয় দলিল)",
        "rent": "Rent Agreement (ভাড়া চুক্তি)",
        "complaint": "General Complaint (সাধারণ অভিযোগ)",
        "affidavit": "Affidavit (হলফনামা)",
        "writ": "Writ Petition (রিট পিটিশন)",
        "plaint": "Plaint / Civil Suit (আরজি)",
    }
    doc_name = templates.get(req.doc_type, req.doc_type)

    retrieved_laws = hybrid_retrieve_laws(f"{doc_name} Bangladesh law requirements", 8)
    law_context = "\n".join(
        f"\n{l['meta'].get('title')} — {section_label(l['meta'])}:\n{l['text'][:500]}\n"
        for l in retrieved_laws[:3]
    )

    prompt = f"""নিচের details দিয়ে একটা {doc_name} তৈরি করো।

Relevant legal requirements:
{law_context}

Details: {req.details}

Requirements:
- Bangladesh law অনুযায়ী correct format
- Key sections বাংলা এবং English দুটো ভাষায়
- Proper legal language use করো
- Signature/witness sections include করো
- উপরের requirements এ নেই এমন কোনো ধারা নম্বর বানাবে না"""

    try:
        document_text = call_llm("তুমি Bangladesh legal document drafting এর একটা AI assistant।", prompt, json_mode=False)
    except Exception as e:
        print(f"⚠️ /api/generate-doc LLM error: {e}")
        raise HTTPException(503, "LLM service temporarily unavailable — please try again in a moment")

    return {
        "document": document_text,
        "doc_type": doc_name,
        "laws_referenced": [
            {"title": l["meta"].get("title"), "section": section_label(l["meta"]), "url": l["meta"].get("url")}
            for l in retrieved_laws[:3]
        ],
    }


@app.get("/api/stats")
def get_stats():
    return {
        "judgements": {
            "total_chunks": judgements_col.count(),
            "estimated_cases": 8615,
            "coverage": "Bangladesh Supreme Court — High Court Division",
            "year_range": "1950–2025",
        },
        "laws": {
            "total_chunks": laws_col.count(),
            "total_acts": 1399,
            "source": "bdlaws.minlaw.gov.bd",
        },
        "features": {
            "langgraph_orchestration": True,
            "query_expansion": True,
            "hybrid_retrieval": True,
            "cross_encoder_rerank": True,
            "precedent_weighting": True,
            "contradiction_detection": True,
            "citation_verification": True,
        },
    }


@app.get("/api/filter-options")
def get_filter_options():
    return {
        "case_types": [
            "criminal_appeal", "criminal_revision", "criminal_misc", "death_reference", "jail_appeal",
            "writ_petition", "civil_revision", "civil_rule", "first_appeal", "first_misc_appeal",
            "company_matter", "admiralty_suit", "customs_vat_appeal", "contempt_petition", "other",
        ],
        "outcomes": ["rule_absolute", "rule_discharged", "allowed", "dismissed", "disposed", "unknown"],
        "divisions": ["HCD", "AD"],
        "year_range": {"min": 1950, "max": 2025},
    }
