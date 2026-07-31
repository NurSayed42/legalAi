"""
api_v3.py
=========
Bangladesh Legal AI — v3 (LangGraph multi-agent, Gemini + Groq)

v2 থেকে যা নতুন:
  ✅ LangGraph দিয়ে multi-node orchestration (single function-call এর বদলে)
  ✅ LLM               : Gemini (primary) + Groq (fallback)  — Claude/Anthropic সরানো হয়েছে
  ✅ Hybrid retrieval   : vector (ChromaDB) + BM25 keyword search, Reciprocal Rank Fusion দিয়ে মিলানো
  ✅ Precedent hierarchy: AD > HCD, নতুন case বেশি weight — শুধু vector similarity না
  ✅ Sufficiency check  : retrieval দুর্বল হলে agentic loop দিয়ে broader query দিয়ে আবার try
  ✅ Citation verifier  : LLM যে section/case cite করেছে সেটা সত্যিই retrieved context এ আছে কিনা check,
                          না থাকলে retry loop
  ✅ Contradiction detect: একই subject এ ভিন্ন outcome এর case থাকলে flag করে, prompt এ পাঠায়
  ✅ Clarification gate : prediction intent এ situation vague হলে সরাসরি opinion না দিয়ে প্রশ্ন করে
  ✅ Query understanding: keyword-based intent classifier এর বদলে LLM-based (multi-turn pronoun resolve সহ),
                          keyword classifier fallback হিসেবে রয়ে গেছে
"""

import os
import re
import sys
import json
from typing import Optional, TypedDict
from collections import Counter, defaultdict
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import json as _json


class UTF8JSONResponse(JSONResponse):
    """ensure_ascii=False — Bangla characters সঠিকভাবে render হবে।"""
    def render(self, content) -> bytes:
        return _json.dumps(
            content, ensure_ascii=False, allow_nan=False, indent=None
        ).encode("utf-8")


import chromadb
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi

from groq import Groq

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

from langgraph.graph import StateGraph, END

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
CHROMA_DB_PATH  = os.getenv("CHROMA_DB_PATH", "./legal_chroma_db")
EMBED_MODEL     = "paraphrase-multilingual-MiniLM-L12-v2"
GROQ_MODEL      = "llama-3.3-70b-versatile"
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

N_RETRIEVE      = 12   # প্রথম pass এ retrieval pool
N_RETRIEVE_WIDE = 25   # sufficiency check fail করলে broader retry
N_FINAL         = 5    # LLM কে পাঠানো final count
MAX_CHUNK_LEN   = 700
MAX_HISTORY     = 6
RRF_K           = 60   # reciprocal rank fusion constant
SUFFICIENCY_THRESHOLD = 0.01   # fused score এর নিচে হলে retrieval "দুর্বল" ধরা হবে
MAX_RETRIEVAL_RETRY   = 2
MAX_CITATION_RETRY    = 1
# ──────────────────────────────────────────────────────────

app = FastAPI(
    title="Bangladesh Legal AI v3",
    version="3.0.0",
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

# ─── SETUP: LLM CLIENTS ───────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

gemini_client = None
if GEMINI_AVAILABLE and GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

print(f"✅ Judgements: {judgements_col.count()} chunks")
print(f"✅ Laws: {laws_col.count()} chunks")
print(f"✅ Gemini: {'ready (' + GEMINI_MODEL + ')' if gemini_client else 'not configured — Groq fallback only'}")
print(f"✅ Groq: {'ready' if groq_client else 'not configured'}")


# ─── SETUP: BM25 (in-memory, built from Chroma contents at startup) ───

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


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
    search_type: str = "both"
    filters: Optional[dict] = None

class PredictionRequest(BaseModel):
    situation: str
    case_type: Optional[str] = None

class DocRequest(BaseModel):
    doc_type: str
    details: str


# ─── FALLBACK KEYWORD INTENT CLASSIFIER (LLM router fail করলে ব্যবহার হয়) ──

def classify_intent_keyword(question: str) -> str:
    q = question.lower()
    prediction_signals = [
        "case করলে", "মামলা করলে", "জিতবো", "হারবো", "outcome", "chance",
        "সম্ভাবনা", "কী হবে", "what will happen", "will i win", "predict",
    ]
    law_signals = ["ধারা", "section", "act", "আইন কি বলে", "law says", "penal code", "article", "অনুচ্ছেদ"]
    case_signals = ["case", "মামলা", "judgement", "verdict", "রায়", "court ruled", "precedent", "নজির"]

    if any(s in q for s in prediction_signals):
        return "prediction"
    elif any(s in q for s in law_signals):
        return "law_lookup"
    elif any(s in q for s in case_signals):
        return "case_search"
    return "general"


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


# ─── HYBRID RETRIEVAL: VECTOR + BM25 + RECIPROCAL RANK FUSION ─────

def _bm25_search(bm25_data: dict, query: str, n: int = 15) -> list[dict]:
    if not bm25_data["bm25"]:
        return []
    scores = bm25_data["bm25"].get_scores(_tokenize(query))
    top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
    return [
        {"text": bm25_data["docs"][i], "meta": bm25_data["metas"][i]}
        for i in top_idx if scores[i] > 0
    ]


def _key_of(item: dict) -> str:
    meta = item["meta"]
    return meta.get("filename") or f'{meta.get("title","")}-{meta.get("section","")}' or item["text"][:50]


def _reciprocal_rank_fusion(vector_results: list[dict], bm25_results: list[dict], k: int = RRF_K) -> list[dict]:
    """দুই retrieval method এর ranking মিলিয়ে একটা fused score দেয়। exact score না, শুধু rank ব্যবহার করে —
    তাই vector distance আর BM25 score এর scale ভিন্ন হলেও ঠিকমতো কাজ করে।"""
    scores = defaultdict(float)
    items = {}
    for rank, item in enumerate(vector_results):
        kk = _key_of(item)
        scores[kk] += 1 / (k + rank + 1)
        items[kk] = item
    for rank, item in enumerate(bm25_results):
        kk = _key_of(item)
        scores[kk] += 1 / (k + rank + 1)
        items.setdefault(kk, item)
    for kk, item in items.items():
        item["rrf_score"] = scores[kk]
    return list(items.values())


def _precedent_weight(meta: dict) -> float:
    """Real lawyer এর মতো precedent hierarchy — Appellate Division HCD-কে bind করে, নতুন judgment
    সামান্য বেশি authoritative ধরা হয়। শুধু vector similarity দিয়ে না, এই weight দিয়েও rank করা হয়।"""
    division_w = 1.15 if meta.get("division") == "AD" else 1.0
    year = meta.get("year") or 2000
    recency_w = 1.0 + max(0, min(year, 2026) - 1950) / (2026 - 1950) * 0.2
    return division_w * recency_w


def hybrid_retrieve_judgements(query: str, n: int, chroma_filter: dict | None) -> list[dict]:
    kwargs = {"query_texts": [query], "n_results": n}
    if chroma_filter:
        kwargs["where"] = chroma_filter
    try:
        r = judgements_col.query(**kwargs)
        vector_results = [{"text": d, "meta": m} for d, m in zip(r["documents"][0], r["metadatas"][0])]
    except Exception as e:
        print(f"⚠️ Judgements vector retrieval error: {e}")
        vector_results = []

    bm25_results = _bm25_search(judgements_bm25, query, n=n)
    fused = _reciprocal_rank_fusion(vector_results, bm25_results)
    for item in fused:
        item["score"] = item["rrf_score"] * _precedent_weight(item["meta"])
    return sorted(fused, key=lambda x: x["score"], reverse=True)[:N_FINAL]


SECTION_NUMBER_RE = re.compile(r"(?:section|ধারা|sec\.?)\s*(\d+[a-zA-Z]?)", re.IGNORECASE)


def _extract_section_number(query: str) -> str | None:
    m = SECTION_NUMBER_RE.search(query)
    return m.group(1) if m else None


def _exact_section_lookup(query: str, section_no: str, n: int = 5) -> list[dict]:
    """BM25/vector উভয়েই section number ঠিকমতো ধরতে পারে না (fuzzy ranking, exact
    ID match না) — তাই query তে explicit 'Section N' থাকলে section_no metadata দিয়ে
    সরাসরি filter করে exact match বের করি, fuzzy ranking এর উপর নির্ভর না করে।"""
    try:
        r = laws_col.query(
            query_texts=[query], n_results=n,
            where={"section_no": {"$eq": section_no}},
        )
        results = [{"text": d, "meta": m} for d, m in zip(r["documents"][0], r["metadatas"][0])]
    except Exception as e:
        print(f"⚠️ Exact section lookup error: {e}")
        return []
    for item in results:
        item["score"] = 1.0  # exact match — fused/BM25 score এর চেয়ে বেশি, সবার আগে থাকবে
    return results


def hybrid_retrieve_laws(query: str, n: int) -> list[dict]:
    exact = []
    section_no = _extract_section_number(query)
    if section_no:
        exact = _exact_section_lookup(query, section_no)

    try:
        r = laws_col.query(query_texts=[query], n_results=n)
        vector_results = [{"text": d, "meta": m} for d, m in zip(r["documents"][0], r["metadatas"][0])]
    except Exception as e:
        print(f"⚠️ Laws vector retrieval error: {e}")
        vector_results = []

    bm25_results = _bm25_search(laws_bm25, query, n=n)
    fused = _reciprocal_rank_fusion(vector_results, bm25_results)
    for item in fused:
        item["score"] = item["rrf_score"]

    seen = {_key_of(e) for e in exact}
    combined = exact + [f for f in fused if _key_of(f) not in seen]
    return sorted(combined, key=lambda x: x["score"], reverse=True)[:N_FINAL]


# ─── CONTRADICTION DETECTION ───────────────────────────────

def detect_contradictions(judgements: list[dict]) -> list[dict]:
    """একই subject নিয়ে retrieved cases গুলোর মধ্যে ভিন্ন outcome আছে কিনা দেখে —
    real lawyer conflicting precedent লুকায় না, explain করে।"""
    by_subject = defaultdict(list)
    for j in judgements:
        for subj in (j["meta"].get("subjects") or "").split(","):
            subj = subj.strip()
            if subj:
                by_subject[subj].append(j)

    conflicts = []
    for subj, cases in by_subject.items():
        outcomes = set(c["meta"].get("outcome", "unknown") for c in cases)
        if len(outcomes) > 1 and len(cases) >= 2:
            conflicts.append({"subject": subj, "cases": cases, "outcomes": list(outcomes)})
    return conflicts


# ─── OUTCOME STATISTICS ────────────────────────────────────

def compute_outcome_stats(judgements: list[dict]) -> dict:
    if not judgements:
        return {}
    outcomes = Counter(j["meta"].get("outcome", "unknown") for j in judgements)
    total = sum(outcomes.values())
    petitioner_won = outcomes.get("rule_absolute", 0) + outcomes.get("allowed", 0)
    petitioner_lost = outcomes.get("rule_discharged", 0) + outcomes.get("dismissed", 0)
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

def build_context(retrieved: dict, contradictions: list[dict]) -> str:
    lines = []
    if retrieved["judgements"]:
        lines.append("## Bangladesh Supreme Court Cases (precedent-weighted ranking)\n")
        seen = set()
        for j in retrieved["judgements"]:
            meta = j["meta"]
            fname = meta.get("filename", "")
            if fname in seen:
                continue
            seen.add(fname)
            case_no = meta.get("case_no", fname)
            year = meta.get("year", "?")
            division = meta.get("division", "HCD")
            outcome = meta.get("outcome", "unknown")
            subjects = meta.get("subjects", "")
            text_preview = j["text"][:MAX_CHUNK_LEN]
            lines.append(
                f"### Case: {case_no} ({year}) [{division}]\n"
                f"Outcome: {outcome} | Subjects: {subjects}\n{text_preview}\n"
            )

    if retrieved["laws"]:
        lines.append("\n## Applicable Bangladesh Laws\n")
        for law in retrieved["laws"]:
            meta = law["meta"]
            title = meta.get("title", "")
            section = meta.get("section", "")
            url = meta.get("url", "")
            year = meta.get("year", "")
            text_preview = law["text"][:MAX_CHUNK_LEN]
            lines.append(f"### {title} ({year}) — {section}\nSource: {url}\n{text_preview}\n")

    if contradictions:
        lines.append("\n## ⚠️ Conflicting precedent পাওয়া গেছে\n")
        for c in contradictions:
            case_list = ", ".join(
                j["meta"].get("case_no", j["meta"].get("filename", "?")) for j in c["cases"]
            )
            lines.append(
                f"Subject: {c['subject']} — ভিন্ন outcome আছে ({', '.join(c['outcomes'])}): {case_list}\n"
                f"এইটা honest_assessment এ address করো — কেন conflict, facts আলাদা নাকি নতুন case পুরনোটা overrule করেছে।\n"
            )

    return "\n".join(lines)


# ─── LLM CALLS: GEMINI (primary) + GROQ (fallback) ─────────

def _call_gemini(system_prompt: str, user_content: str, json_mode: bool) -> str:
    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json" if json_mode else "text/plain",
        temperature=0.1,
    )
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_content,
        config=config,
    )
    return response.text


def _call_groq(system_prompt: str, user_content: str) -> str:
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
        max_tokens=6000,
    )
    return response.choices[0].message.content


def call_llm(system_prompt: str, user_content: str, json_mode: bool = True) -> str:
    """Gemini আগে try করে, fail করলে বা configured না থাকলে Groq এ fallback করে।"""
    if gemini_client:
        try:
            return _call_gemini(system_prompt, user_content, json_mode)
        except Exception as e:
            print(f"⚠️ Gemini error, Groq এ fallback করছি: {e}")
    if groq_client:
        return _call_groq(system_prompt, user_content)
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


# ─── SYSTEM PROMPTS ─────────────────────────────────────────

QUERY_UNDERSTANDING_PROMPT = """তুমি Bangladesh legal AI system এর query router। ইউজারের প্রশ্ন এবং conversation history দেখে নিচের ঠিক এই JSON structure এ output দাও, আর কিছু না:

{
  "rewritten_query": "conversation history অনুযায়ী pronoun/reference resolve করা standalone query",
  "intent": "law_lookup" | "case_search" | "prediction" | "general",
  "needs_clarification": true/false,
  "follow_up_options": [
    {"question": "মিসিং fact সম্পর্কে প্রশ্ন", "options": ["সংক্ষিপ্ত option ১", "option ২", "option ৩"]}
  ]
}

Rules:
- intent="prediction" শুধু তখনই যখন ইউজার সরাসরি case করলে ফলাফল কী হবে জানতে চাইছে
- needs_clarification=true শুধু prediction intent এ, তাও শুধু তখন যখন situation এতটাই vague যে কোনো lawyer-ই opinion দিতে পারবে না
- সাধারণ law lookup বা case search এ needs_clarification সবসময় false, follow_up_options খালি array
- needs_clarification=true হলে follow_up_options এ ১-৩টা প্রশ্ন দাও, প্রতিটার সাথে ৩-৪টা সংক্ষিপ্ত
  mutually-exclusive option (একটা ছোট phrase, পূর্ণ বাক্য না) — user ক্লিক করে উত্তর দিতে পারবে
- options আকারে সাজানো সত্যিই অস্বাভাবিক হলে (genuinely open-ended fact দরকার) options খালি array
  রাখো, শুধু question দাও — তখন frontend open text input দেখাবে
- JSON ছাড়া কিছু লিখবে না, কোনো preamble বা markdown না"""


SYSTEM_PROMPT = """তুমি Barrister Rahim — Bangladesh Supreme Court এর ৩০ বছরের অভিজ্ঞ Senior Advocate।
HCD এবং Appellate Division দুটোতেই হাজার হাজার মামলা পরিচালনা করেছ।
Civil, Criminal, Family, Property, Constitutional — সব বিষয়ে গভীর জ্ঞান।

সবার আগে ঠিক করো প্রশ্নটা কোন ধরনের:

TYPE A — সরল তথ্য জিজ্ঞাসা: ইউজার শুধু একটা আইন/section/case কী বলে তা জানতে চাইছে
("Section 302 কী বলে", "এই আইনে কী আছে", "এই case এ কী হয়েছিল")। নিজের case নিয়ে
strategy বা prediction চাইছে না।

TYPE B — case strategy / outcome prediction: ইউজার নিজের specific facts দিয়ে "আমি
করলে কী হবে", "আমার case টা কেমন" ধরনের প্রশ্ন করছে, অথবা intent = prediction।

সন্দেহ হলে TYPE A ধরো — over-structuring এর চেয়ে simple থাকা ভালো।

── TYPE A হলে ──
সরাসরি কথোপকথনের ভঙ্গিতে ২-৪ প্যারাগ্রাফে উত্তর দাও, একজন senior lawyer সহকর্মীকে
যেভাবে সংক্ষেপে বুঝিয়ে দিত সেভাবে। প্রাসঙ্গিক section/case citation ভিতরেই মিশিয়ে
বলবে, আলাদা template ভরাট করবে না। শুধু নিচের field গুলো output করবে:
"summary", "applicable_laws", "relevant_cases", "follow_up_questions" বা
"follow_up_options" (শুধু genuinely কিছু জিজ্ঞাসা করার থাকলে), "disclaimer"।
irac_analysis, honest_assessment, strategy, outcome_prediction, action_checklist —
এই key গুলো JSON থেকে সম্পূর্ণ বাদ দিবে। "N/A" বা খালি বাক্য দিয়ে ভরবে না, key-ই
থাকবে না।

── TYPE B হলে ──
এই ক্রমে ভাবো (ভাবনার ক্রম, output structure না):
1. ISSUE SPOTTING — surface problem এর নিচে deeper legal issues খোঁজো। "জমি দখল"
   মানে শুধু property law না — limitation period, benami, criminal trespass,
   revenue record, adverse possession — সব check করো।
2. SPECIFIC LAW — general না, specific act + section number। ভুল: "property law
   অনুযায়ী" — সঠিক: "Specific Relief Act 1877, Section 8 অনুযায়ী"।
3. DOCUMENTS — real lawyer হিসেবে specific কাগজপত্রের list।
4. CONCRETE ACTION — abstract না, step-by-step numbered action plan।
5. HONEST RISK — false hope দেওয়া ethics এর বিরুদ্ধে, case এর weakness সৎভাবে বলো।
   Context এ conflicting precedent থাকলে honest_assessment এ address করো, লুকাবে না।
6. FOLLOW-UP — info incomplete হলে specific প্রশ্ন করো, সম্ভব হলে clickable
   options আকারে (নিচে follow_up_options দেখো)।

পূর্ণ schema ব্যবহার করবে (নিচে দেখো), কিন্তু:
- যে field এ সত্যিই বলার কিছু নেই সেটা JSON থেকে বাদ দিবে (key-ই থাকবে না) —
  "N/A", "প্রযোজ্য নয়", খালি বাক্য দিয়ে ভরবে না।
- একই কথা দুইবার বলবে না। irac_analysis.conclusion আর honest_assessment একই
  সিদ্ধান্ত repeat করবে না — conclusion এ কী হওয়া উচিত সেটা বলবে, honest_assessment
  এ কেন (strengths/weaknesses/critical factors) সেটা বলবে।

── সবসময় (TYPE A এবং B দুটোতেই) ──
- ভাষা: সরাসরি "আপনি/আপনার" — warm কিন্তু honest।
- কোনো emoji bullet বা emoji heading (▶, −, !, 📊, 🔍 ইত্যাদি) ব্যবহার করবে না।
  টেক্সটের মধ্যে দরকার হলে সাধারণ bold/heading style লিখবে, emoji দিয়ে না।
- CITATION RULE (কঠোরভাবে মানতে হবে): শুধুমাত্র নিচের context এ যে exact act name,
  section, case_no দেওয়া আছে সেগুলোই cite করবে। Context এ নেই এমন কোনো section
  number বা case বানিয়ে বলবে না — এটাই সবচেয়ে বড় ভুল।

FOLLOW_UP_OPTIONS ফরম্যাট: কোনো follow-up প্রশ্ন করার দরকার হলে সম্ভব হলে
clickable multiple-choice আকারে দাও:
  "follow_up_options": [
    {"question": "...", "options": ["সংক্ষিপ্ত option ১", "option ২", "option ৩"]}
  ]
প্রতিটা option ৩-৪টার বেশি না, প্রতিটা একটা সংক্ষিপ্ত phrase (পূর্ণ বাক্য না)।
যদি options আকারে সাজানো অস্বাভাবিক লাগে (সত্যিই open-ended প্রশ্ন), তখনই শুধু
"follow_up_questions": ["..."] ব্যবহার করবে।

TYPE A JSON schema — শুধু এই key গুলো:
{
  "summary": "২-৪ প্যারাগ্রাফের সরাসরি উত্তর, প্রাসঙ্গিক citation সহ",
  "applicable_laws": [
    {"act": "Full Act Name (context এ যেভাবে আছে ঠিক সেভাবে)", "section": "Section X (context থেকে exact)",
     "what_it_says": "সহজ বাংলায়", "how_it_applies": "কেন relevant", "url": "bdlaws URL"}
  ],
  "relevant_cases": [
    {"case_no": "context এ যেভাবে আছে ঠিক সেভাবে", "year": 2020, "court": "HCD বা AD",
     "outcome": "outcome", "why_relevant": "কেন relevant"}
  ],
  "follow_up_options": [{"question": "...", "options": ["...", "..."]}],
  "follow_up_questions": ["শুধু options অস্বাভাবিক হলে ব্যবহার করো"],
  "disclaimer": "এটা legal research assistance। চূড়ান্ত সিদ্ধান্তের জন্য qualified আইনজীবীর পরামর্শ নিন।"
}

TYPE B JSON schema — পূর্ণ, কিন্তু অপ্রাসঙ্গিক key বাদ দেওয়া যাবে:
{
  "summary": "আপনার situation সম্পর্কে সরাসরি ৩-৪ বাক্য",
  "irac_analysis": {
    "issue": "সব legal issues",
    "applicable_rule": "Specific act + section number সহ",
    "application": "আপনার exact facts এ আইনটা কীভাবে কাজ করে",
    "conclusion": "Honest opinion — কী করা উচিত"
  },
  "applicable_laws": [
    {"act": "Full Act Name (context এ যেভাবে আছে ঠিক সেভাবে)", "section": "Section X (context থেকে exact)",
     "what_it_says": "সহজ বাংলায়", "how_it_applies": "কেন relevant", "url": "bdlaws URL"}
  ],
  "relevant_cases": [
    {"case_no": "context এ যেভাবে আছে ঠিক সেভাবে", "year": 2020, "court": "HCD বা AD",
     "outcome": "outcome", "why_relevant": "কেন relevant"}
  ],
  "honest_assessment": {
    "strengths": ["..."], "weaknesses": ["..."], "critical_factors": ["..."],
    "win_rate_context": "..."
  },
  "strategy": {
    "recommended_action": "...", "forum": "...", "relief_to_seek": "...",
    "documents_needed": ["..."], "estimated_timeline": "...", "immediate_steps": "..."
  },
  "outcome_prediction": {
    "historical_win_rate": "...", "factors_in_your_favor": ["..."],
    "factors_against": ["..."], "estimated_timeline": "...", "recommendation": "..."
  },
  "action_checklist": ["১. ...", "২. ...", "৩. ..."],
  "follow_up_options": [{"question": "...", "options": ["...", "..."]}],
  "follow_up_questions": ["শুধু options অস্বাভাবিক হলে ব্যবহার করো"],
  "disclaimer": "এটা legal research assistance। চূড়ান্ত সিদ্ধান্তের জন্য qualified আইনজীবীর পরামর্শ নিন।"
}

JSON এর বাইরে একটা অক্ষরও লিখবে না।"""


# ─── LANGGRAPH STATE ──────────────────────────────────────

class GraphState(TypedDict, total=False):
    question: str
    history: list
    filters: Optional[dict]
    forced_intent: Optional[str]
    rewritten_query: str
    intent: str
    needs_clarification: bool
    follow_up_options: list
    retrieved: dict
    contradictions: list
    outcome_stats: dict
    structured_answer: dict
    citation_valid: bool
    citation_issues: dict
    max_retrieval_score: float
    retrieval_retry: int
    citation_retry: int
    sources: dict


# ─── LANGGRAPH NODES ───────────────────────────────────────

def _validate_follow_up_options(raw) -> list:
    """LLM output validate করে — malformed/missing হলে চুপচাপ বাদ দেয়, crash করে না।
    Caller (clarify_node) খালি list পেলে নিজে open-text fallback বসায়।"""
    if not isinstance(raw, list):
        return []
    valid = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        options = item.get("options") or []
        options = [o.strip() for o in options if isinstance(o, str) and o.strip()][:4]
        valid.append({"question": question.strip(), "options": options})
    return valid


def understanding_node(state: GraphState) -> dict:
    history_text = "\n".join(f"{m['role']}: {m['content']}" for m in state.get("history", [])[-MAX_HISTORY:])
    prompt = f"Conversation history:\n{history_text}\n\nবর্তমান প্রশ্ন: {state['question']}"
    try:
        raw = call_llm(QUERY_UNDERSTANDING_PROMPT, prompt, json_mode=True)
        parsed = safe_parse_json(raw)
    except Exception as e:
        print(f"⚠️ Query understanding LLM error, keyword fallback: {e}")
        parsed = {}

    intent = state.get("forced_intent") or parsed.get("intent") or classify_intent_keyword(state["question"])
    return {
        "rewritten_query": parsed.get("rewritten_query") or state["question"],
        "intent": intent,
        "needs_clarification": bool(parsed.get("needs_clarification")),
        "follow_up_options": _validate_follow_up_options(parsed.get("follow_up_options")),
        "retrieval_retry": 0,
        "citation_retry": 0,
    }


def route_after_understanding(state: GraphState) -> str:
    if state["intent"] == "prediction" and state.get("needs_clarification"):
        return "clarify"
    return "retrieval"


def clarify_node(state: GraphState) -> dict:
    follow_up_options = state.get("follow_up_options") or []
    if not follow_up_options:
        # LLM options দিতে না পারলে silently খালি হাতে ফেরত না দিয়ে open-text fallback বসাও
        follow_up_options = [{"question": "আপনার situation টা আরেকটু বিস্তারিত বলুন।", "options": []}]

    return {
        "structured_answer": {
            "summary": "আপনার situation সম্পর্কে আরেকটু নির্দিষ্ট তথ্য দরকার সঠিক legal opinion দেওয়ার জন্য।",
            "follow_up_options": follow_up_options,
            "disclaimer": "এটা legal research assistance। চূড়ান্ত সিদ্ধান্তের জন্য qualified আইনজীবীর পরামর্শ নিন।",
        },
        "sources": {"cases": [], "laws": []},
        "outcome_stats": {},
        "contradictions": [],
    }


def retrieval_node(state: GraphState) -> dict:
    query = state.get("rewritten_query") or state["question"]
    intent = state["intent"]
    chroma_filter = build_chroma_filter(state.get("filters"))
    n = N_RETRIEVE if state.get("retrieval_retry", 0) == 0 else N_RETRIEVE_WIDE

    # retry তে filter সরিয়ে দাও — broader search
    if state.get("retrieval_retry", 0) > 0:
        chroma_filter = None
        query = state["question"]  # rewritten narrow query বাদে original দিয়ে try করো

    retrieved = {"judgements": [], "laws": []}
    if intent in ("case_search", "prediction", "general"):
        retrieved["judgements"] = hybrid_retrieve_judgements(query, n, chroma_filter)
    if intent in ("law_lookup", "general", "prediction"):
        retrieved["laws"] = hybrid_retrieve_laws(query, n)

    contradictions = detect_contradictions(retrieved["judgements"])
    outcome_stats = compute_outcome_stats(retrieved["judgements"])

    return {"retrieved": retrieved, "contradictions": contradictions, "outcome_stats": outcome_stats}


def sufficiency_check_node(state: GraphState) -> dict:
    retrieved = state["retrieved"]
    scores = [j.get("score", 0) for j in retrieved["judgements"]] + [l.get("score", 0) for l in retrieved["laws"]]
    max_score = max(scores) if scores else 0.0
    return {
        "max_retrieval_score": max_score,
        "retrieval_retry": state.get("retrieval_retry", 0) + 1,
    }


def route_after_check(state: GraphState) -> str:
    insufficient = state["max_retrieval_score"] < SUFFICIENCY_THRESHOLD
    retries_left = state["retrieval_retry"] <= MAX_RETRIEVAL_RETRY
    if insufficient and retries_left:
        return "retrieval"
    return "generation"


def generation_node(state: GraphState) -> dict:
    context = build_context(state["retrieved"], state.get("contradictions", []))

    citation_warning = ""
    if state.get("citation_retry", 0) > 0 and state.get("citation_valid") is False:
        issues = state.get("citation_issues", {})
        citation_warning = (
            f"\n\n⚠️ পূর্ববর্তী উত্তরে এমন citation ছিল যেগুলো context এ নাই: "
            f"{json.dumps(issues, ensure_ascii=False)}\n"
            f"এইবার শুধুমাত্র উপরের context এ থাকা exact act/section/case_no ব্যবহার করো।"
        )

    user_content = f"""Context from Bangladesh legal database:

{context}
{citation_warning}
---
Historical outcome statistics:
{json.dumps(state.get('outcome_stats', {}), ensure_ascii=False)}
---
User question: {state['question']}

Intent detected: {state['intent']}"""

    raw = call_llm(SYSTEM_PROMPT, user_content, json_mode=True)
    structured = safe_parse_json(raw)
    return {"structured_answer": structured}


def citation_verifier_node(state: GraphState) -> dict:
    answer = state.get("structured_answer", {})
    retrieved = state["retrieved"]

    def norm(s):
        return (s or "").strip().lower()

    valid_sections = {(norm(l["meta"].get("title")), norm(l["meta"].get("section"))) for l in retrieved["laws"]}
    valid_cases = {norm(j["meta"].get("case_no") or j["meta"].get("filename")) for j in retrieved["judgements"]}

    cited_laws = answer.get("applicable_laws") or []
    cited_cases = answer.get("relevant_cases") or []

    bad_laws = [l for l in cited_laws if (norm(l.get("act")), norm(l.get("section"))) not in valid_sections]
    bad_cases = [c for c in cited_cases if norm(c.get("case_no")) not in valid_cases]

    is_valid = not bad_laws and not bad_cases
    return {
        "citation_valid": is_valid,
        "citation_issues": {"bad_laws": bad_laws, "bad_cases": bad_cases},
        "citation_retry": state.get("citation_retry", 0) + 1,
    }


def route_after_citation(state: GraphState) -> str:
    if not state["citation_valid"] and state["citation_retry"] <= MAX_CITATION_RETRY:
        return "generation"
    return "finalize"


def finalize_node(state: GraphState) -> dict:
    retrieved = state["retrieved"]
    sources = {
        "cases": [
            {
                "case_no": j["meta"].get("case_no", j["meta"].get("filename")),
                "year": j["meta"].get("year"),
                "type": j["meta"].get("case_type"),
                "outcome": j["meta"].get("outcome"),
                "division": j["meta"].get("division"),
                "subjects": j["meta"].get("subjects"),
                "score": round(j.get("score", 0), 4),
            }
            for j in retrieved["judgements"]
        ],
        "laws": [
            {
                "title": l["meta"].get("title"),
                "section": l["meta"].get("section"),
                "url": l["meta"].get("url"),
                "year": l["meta"].get("year"),
                "score": round(l.get("score", 0), 4),
            }
            for l in retrieved["laws"]
        ],
    }
    return {"sources": sources}


# ─── BUILD GRAPH ────────────────────────────────────────────

def build_graph():
    g = StateGraph(GraphState)
    g.add_node("understanding", understanding_node)
    g.add_node("clarify", clarify_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("sufficiency_check", sufficiency_check_node)
    g.add_node("generation", generation_node)
    g.add_node("citation_verifier", citation_verifier_node)
    g.add_node("finalize", finalize_node)

    g.set_entry_point("understanding")
    g.add_conditional_edges("understanding", route_after_understanding, {"clarify": "clarify", "retrieval": "retrieval"})
    g.add_edge("retrieval", "sufficiency_check")
    g.add_conditional_edges("sufficiency_check", route_after_check, {"retrieval": "retrieval", "generation": "generation"})
    g.add_edge("generation", "citation_verifier")
    g.add_conditional_edges("citation_verifier", route_after_citation, {"generation": "generation", "finalize": "finalize"})
    g.add_edge("clarify", END)
    g.add_edge("finalize", END)
    return g.compile()


legal_graph = build_graph()


# ─── ENDPOINTS ────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "status": "Bangladesh Legal AI v3",
        "judgements": judgements_col.count(),
        "laws": laws_col.count(),
        "llm": {"primary": "gemini" if gemini_client else None, "fallback": "groq" if groq_client else None},
        "features": [
            "langgraph_multi_agent",
            "hybrid_retrieval_bm25_vector",
            "precedent_hierarchy_weighting",
            "sufficiency_check_retry_loop",
            "citation_verifier_retry_loop",
            "contradiction_detection",
            "clarification_gate",
            "conversation_memory",
        ],
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question cannot be empty")

    init_state: GraphState = {
        "question": question,
        "history": [m.dict() for m in req.history],
        "filters": req.filters,
    }
    try:
        final_state = legal_graph.invoke(init_state)
    except Exception as e:
        print(f"⚠️ /api/chat graph error: {e}")
        raise HTTPException(503, "LLM service temporarily unavailable — please try again in a moment")

    return {
        "answer": final_state.get("structured_answer", {}),
        "sources": final_state.get("sources", {"cases": [], "laws": []}),
        "intent": final_state.get("intent"),
        "outcome_stats": final_state.get("outcome_stats", {}),
        "contradictions_detected": len(final_state.get("contradictions", [])),
        "citation_verified": final_state.get("citation_valid"),
        "retrieval_attempts": final_state.get("retrieval_retry", 1),
    }


@app.post("/api/predict")
def predict_outcome(req: PredictionRequest):
    situation = req.situation.strip()
    if not situation:
        raise HTTPException(400, "Situation cannot be empty")

    filters = {"case_type": req.case_type} if req.case_type else None
    init_state: GraphState = {
        "question": situation, "history": [], "filters": filters,
        "forced_intent": "prediction",
    }
    try:
        final_state = legal_graph.invoke(init_state)
    except Exception as e:
        print(f"⚠️ /api/predict graph error: {e}")
        raise HTTPException(503, "LLM service temporarily unavailable — please try again in a moment")

    return {
        "prediction": final_state.get("structured_answer", {}),
        "outcome_stats": final_state.get("outcome_stats", {}),
        "similar_cases_used": len(final_state.get("retrieved", {}).get("judgements", [])),
        "laws_referenced": len(final_state.get("retrieved", {}).get("laws", [])),
        "contradictions_detected": len(final_state.get("contradictions", [])),
        "citation_verified": final_state.get("citation_valid"),
    }


@app.post("/api/search")
def search(req: SearchRequest):
    chroma_filter = build_chroma_filter(req.filters)
    results = {"judgements": [], "laws": []}

    if req.search_type in ("judgements", "both"):
        raw = hybrid_retrieve_judgements(req.query, req.n_results, chroma_filter)
        seen = set()
        for j in raw:
            fname = j["meta"].get("filename")
            if fname in seen:
                continue
            seen.add(fname)
            results["judgements"].append({
                "case_no": j["meta"].get("case_no", fname),
                "year": j["meta"].get("year"),
                "type": j["meta"].get("case_type"),
                "outcome": j["meta"].get("outcome"),
                "division": j["meta"].get("division"),
                "subjects": j["meta"].get("subjects"),
                "judges": j["meta"].get("judges"),
                "preview": j["text"][:400],
                "score": round(j.get("score", 0), 4),
            })

    if req.search_type in ("laws", "both"):
        raw = hybrid_retrieve_laws(req.query, req.n_results)
        for l in raw:
            results["laws"].append({
                "title": l["meta"].get("title"),
                "section": l["meta"].get("section"),
                "url": l["meta"].get("url"),
                "year": l["meta"].get("year"),
                "type": l["meta"].get("act_type"),
                "preview": l["text"][:400],
                "score": round(l.get("score", 0), 4),
            })

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
        f"\n{l['meta'].get('title')} — {l['meta'].get('section')}:\n{l['text'][:500]}\n"
        for l in retrieved_laws[:3]
    )

    prompt = f"""তুমি একজন Bangladesh legal document expert।
নিচের details দিয়ে একটা {doc_name} তৈরি করো।

Relevant legal requirements:
{law_context}

Details: {req.details}

Requirements:
- Bangladesh law অনুযায়ী correct format
- Key sections বাংলা এবং English দুটো ভাষায়
- Proper legal language use করো
- Signature/witness sections include করো
- Document টা court-admissible হতে হবে"""

    try:
        document_text = call_llm("তুমি একজন অভিজ্ঞ Bangladesh legal document drafting expert।", prompt, json_mode=False)
    except Exception as e:
        print(f"⚠️ /api/generate-doc LLM error: {e}")
        raise HTTPException(503, "LLM service temporarily unavailable — please try again in a moment")

    return {
        "document": document_text,
        "doc_type": doc_name,
        "laws_referenced": [
            {"title": l["meta"].get("title"), "section": l["meta"].get("section"), "url": l["meta"].get("url")}
            for l in retrieved_laws[:3]
        ],
    }


@app.get("/api/stats")
def get_stats():
    return {
        "judgements": {
            "total_chunks": judgements_col.count(),
            "estimated_cases": 8615,
            "coverage": "Bangladesh Supreme Court (HCD + AD)",
            "year_range": "1950–2024",
        },
        "laws": {
            "total_chunks": laws_col.count(),
            "total_acts": 1399,
            "source": "bdlaws.minlaw.gov.bd",
        },
        "features": {
            "langgraph_orchestration": True,
            "hybrid_retrieval": True,
            "precedent_weighting": True,
            "contradiction_detection": True,
            "citation_verification": True,
            "clarification_gate": True,
        },
    }


@app.get("/api/filter-options")
def get_filter_options():
    return {
        "case_types": [
            "civil_revision", "writ_petition", "criminal_appeal",
            "criminal_revision", "first_appeal", "death_reference", "suo_moto",
        ],
        "outcomes": ["rule_absolute", "rule_discharged", "allowed", "dismissed", "disposed", "unknown"],
        "divisions": ["HCD", "AD"],
        "year_range": {"min": 1950, "max": 2024},
    }
