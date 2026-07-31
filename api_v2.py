"""
api_v2.py
=========
Bangladesh Legal AI — Upgraded Backend

Features:
  ✅ Dual collection (judgements + laws) — parallel retrieval
  ✅ Rich metadata filtering (year, case_type, outcome, subject)
  ✅ BM25 keyword search for exact act/section names
  ✅ Conversation memory (multi-turn)
  ✅ Outcome prediction with historical stats
  ✅ Query intent classifier
  ✅ Structured JSON response
  ✅ Rate limiting (basic)
  ✅ API key from .env
"""

import os
import re
import json
from typing import Optional
from collections import Counter
from dotenv import load_dotenv

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

# LLM — Groq (fast) বা Anthropic (better reasoning) choose করো
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from groq import Groq

load_dotenv()  # .env থেকে keys নাও

# ─── CONFIG ───────────────────────────────────────────────
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CHROMA_DB_PATH    = os.getenv("CHROMA_DB_PATH", "./legal_chroma_db")
EMBED_MODEL       = "paraphrase-multilingual-MiniLM-L12-v2"
GROQ_MODEL        = "llama-3.3-70b-versatile"
CLAUDE_MODEL      = "claude-sonnet-4-6"   # Anthropic এর latest

N_RETRIEVE        = 10   # retrieval pool
N_FINAL           = 5    # LLM এ পাঠানো
MAX_CHUNK_LEN     = 700  # context এ প্রতিটা chunk এর max chars
MAX_HISTORY       = 6    # conversation turns to keep
# ──────────────────────────────────────────────────────────

app = FastAPI(
    title="Bangladesh Legal AI v2",
    version="2.0.0",
    default_response_class=UTF8JSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── SETUP ────────────────────────────────────────────────
embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name=EMBED_MODEL
)
chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

judgements_col = chroma_client.get_collection(
    name="bangladesh_judgements",
    embedding_function=embedding_fn,
)
laws_col = chroma_client.get_collection(
    name="bangladesh_laws",
    embedding_function=embedding_fn,
)

# LLM clients
groq_client = Groq(api_key=GROQ_API_KEY)
if ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY:
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
else:
    claude_client = None

print(f"✅ Judgements: {judgements_col.count()} chunks")
print(f"✅ Laws: {laws_col.count()} chunks")


# ─── PYDANTIC MODELS ──────────────────────────────────────

class Message(BaseModel):
    role: str   # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    question: str
    history: list[Message] = []
    filters: Optional[dict] = None   # {"year_from": 2010, "case_type": "criminal_appeal"}

class SearchRequest(BaseModel):
    query: str
    n_results: int = 10
    search_type: str = "both"   # "judgements" | "laws" | "both"
    filters: Optional[dict] = None

class PredictionRequest(BaseModel):
    situation: str   # User এর legal situation বর্ণনা
    case_type: Optional[str] = None

class DocRequest(BaseModel):
    doc_type: str
    details: str


# ─── INTENT CLASSIFIER ────────────────────────────────────

def classify_intent(question: str) -> str:
    """
    Query র intent বুঝো।
    Returns: "law_lookup" | "case_search" | "prediction" | "general"
    """
    q = question.lower()

    prediction_signals = [
        "case করলে", "মামলা করলে", "জিতবো", "হারবো", "outcome", "chance",
        "সম্ভাবনা", "কী হবে", "what will happen", "will i win", "predict",
        "ভবিষ্যৎ", "future", "probability"
    ]
    law_signals = [
        "ধারা", "section", "act", "আইন কি বলে", "law says", "penal code",
        "article", "অনুচ্ছেদ", "বিধান", "provision", "statute"
    ]
    case_signals = [
        "case", "মামলা", "judgement", "verdict", "রায়", "court ruled",
        "precedent", "নজির", "decided"
    ]

    if any(s in q for s in prediction_signals):
        return "prediction"
    elif any(s in q for s in law_signals):
        return "law_lookup"
    elif any(s in q for s in case_signals):
        return "case_search"
    else:
        return "general"   # both collections


# ─── RETRIEVAL ────────────────────────────────────────────

def build_chroma_filter(filters: dict | None) -> dict | None:
    """User filters → ChromaDB where clause"""
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
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def retrieve_dual(query: str, n: int = N_RETRIEVE,
                  intent: str = "general",
                  filters: dict | None = None) -> dict:
    """
    Intent অনুযায়ী একটা বা দুটো collection থেকে retrieve করো।
    Returns: {"judgements": [...], "laws": [...]}
    """
    chroma_filter = build_chroma_filter(filters)
    results = {"judgements": [], "laws": []}

    # ── Judgements ──
    if intent in ("case_search", "prediction", "general"):
        kwargs = {"query_texts": [query], "n_results": n}
        if chroma_filter:
            kwargs["where"] = chroma_filter
        try:
            r = judgements_col.query(**kwargs)
            for doc, meta, dist in zip(
                r["documents"][0], r["metadatas"][0], r["distances"][0]
            ):
                results["judgements"].append({
                    "text": doc,
                    "meta": meta,
                    "score": 1 - dist,   # cosine similarity
                })
        except Exception as e:
            print(f"⚠️ Judgements retrieval error: {e}")

    # ── Laws ──
    if intent in ("law_lookup", "general"):
        try:
            r = laws_col.query(query_texts=[query], n_results=n)
            for doc, meta, dist in zip(
                r["documents"][0], r["metadatas"][0], r["distances"][0]
            ):
                results["laws"].append({
                    "text": doc,
                    "meta": meta,
                    "score": 1 - dist,
                })
        except Exception as e:
            print(f"⚠️ Laws retrieval error: {e}")

    # Prediction এ laws ও দরকার
    if intent == "prediction" and not results["laws"]:
        try:
            r = laws_col.query(query_texts=[query], n_results=5)
            for doc, meta, dist in zip(
                r["documents"][0], r["metadatas"][0], r["distances"][0]
            ):
                results["laws"].append({
                    "text": doc, "meta": meta, "score": 1 - dist,
                })
        except Exception:
            pass

    # Score অনুযায়ী sort করো, top N_FINAL নাও
    results["judgements"] = sorted(
        results["judgements"], key=lambda x: x["score"], reverse=True
    )[:N_FINAL]
    results["laws"] = sorted(
        results["laws"], key=lambda x: x["score"], reverse=True
    )[:N_FINAL]

    return results


# ─── OUTCOME STATISTICS ───────────────────────────────────

def compute_outcome_stats(judgements: list[dict]) -> dict:
    """Retrieved judgements থেকে historical outcome statistics বের করো।"""
    if not judgements:
        return {}

    outcomes = Counter(j["meta"].get("outcome", "unknown") for j in judgements)
    total = sum(outcomes.values())

    # Outcome grouping
    petitioner_won = (
        outcomes.get("rule_absolute", 0) +
        outcomes.get("allowed", 0)
    )
    petitioner_lost = (
        outcomes.get("rule_discharged", 0) +
        outcomes.get("dismissed", 0)
    )

    years = [j["meta"].get("year", 0) for j in judgements if j["meta"].get("year", 0) > 1950]
    subjects = []
    for j in judgements:
        subjects.extend(j["meta"].get("subjects", "").split(","))
    common_subjects = [s for s, _ in Counter(subjects).most_common(3) if s]

    return {
        "total_similar_cases": total,
        "petitioner_won": petitioner_won,
        "petitioner_lost": petitioner_lost,
        "disposed_settled": outcomes.get("disposed", 0),
        "win_rate_pct": round(petitioner_won / total * 100) if total else 0,
        "year_range": f"{min(years)}–{max(years)}" if years else "N/A",
        "common_subjects": common_subjects,
    }


# ─── CONTEXT BUILDER ──────────────────────────────────────

def build_context(retrieved: dict) -> str:
    lines = []

    if retrieved["judgements"]:
        lines.append("## Bangladesh Supreme Court Cases\n")
        seen = set()
        for j in retrieved["judgements"]:
            meta = j["meta"]
            fname = meta.get("filename", "")
            if fname in seen:
                continue
            seen.add(fname)

            case_no  = meta.get("case_no", fname)
            year     = meta.get("year", "?")
            division = meta.get("division", "HCD")
            outcome  = meta.get("outcome", "unknown")
            subjects = meta.get("subjects", "")
            text_preview = j["text"][:MAX_CHUNK_LEN]

            lines.append(
                f"### Case: {case_no} ({year}) [{division}]\n"
                f"Outcome: {outcome} | Subjects: {subjects}\n"
                f"{text_preview}\n"
            )

    if retrieved["laws"]:
        lines.append("\n## Applicable Bangladesh Laws\n")
        for law in retrieved["laws"]:
            meta = law["meta"]
            title   = meta.get("title", "")
            section = meta.get("section", "")
            url     = meta.get("url", "")
            year    = meta.get("year", "")
            text_preview = law["text"][:MAX_CHUNK_LEN]

            lines.append(
                f"### {title} ({year}) — {section}\n"
                f"Source: {url}\n"
                f"{text_preview}\n"
            )

    return "\n".join(lines)


# ─── LLM CALL ─────────────────────────────────────────────

SYSTEM_PROMPT = """তুমি Barrister Rahim — Bangladesh Supreme Court এর ৩০ বছরের অভিজ্ঞ Senior Advocate।
HCD এবং Appellate Division দুটোতেই হাজার হাজার মামলা পরিচালনা করেছ।
Civil, Criminal, Family, Property, Constitutional — সব বিষয়ে গভীর জ্ঞান।

প্রতিটা প্রশ্নে এই ক্রমে ভাবো:

STEP 1 — ISSUE SPOTTING: Surface problem এর নিচে deeper legal issues খোঁজো।
"জমি দখল" মানে শুধু property law না — limitation period, benami, criminal trespass,
revenue record, adverse possession — সব check করো।

STEP 2 — SPECIFIC LAW: General না, specific act + section number দাও।
ভুল: "property law অনুযায়ী" — সঠিক: "Specific Relief Act 1877, Section 8 অনুযায়ী"

STEP 3 — DOCUMENTS: Real lawyer হিসেবে specific কাগজপত্রের list দাও।
জমি: CS/SA/RS/BS খতিয়ান, দলিল, নামজারি, DCR, mutation, ট্যাক্স রসিদ
Criminal: GD number, medical certificate, witness list, FIR copy
Contract: চুক্তিপত্র, payment receipt, legal notice copy

STEP 4 — CONCRETE ACTION: Abstract না, step-by-step numbered action plan।
"মামলা করুন" না — "প্রথমে থানায় GD করুন (GD নম্বর রাখুন), তারপর ৩০ দিনের মধ্যে..."

STEP 5 — HONEST RISK: False hope দেওয়া ethics এর বিরুদ্ধে।
Bangladesh court system slow — realistic timeline বলো।
Case এর weakness সৎভাবে বলো।

STEP 6 — FOLLOW-UP: Info incomplete হলে specific প্রশ্ন করো।

ভাষা: সরাসরি "আপনি/আপনার" — warm কিন্তু honest।

এই exact JSON structure তে দাও — JSON এর বাইরে একটা অক্ষরও না:
{
  "summary": "আপনার situation সম্পর্কে সরাসরি ৩-৪ বাক্য — specific, actionable",
  "irac_analysis": {
    "issue": "সব legal issues list করো — কোনোটা বাদ দিও না",
    "applicable_rule": "Specific act name + section number সহ",
    "application": "আপনার exact facts এ আইনটা কীভাবে কাজ করে",
    "conclusion": "Honest opinion — case কতটা strong, জেতার সম্ভাবনা"
  },
  "applicable_laws": [
    {
      "act": "Full Act Name",
      "section": "Section X",
      "what_it_says": "সহজ বাংলায় কী বলে",
      "how_it_applies": "আপনার case এ specifically relevant কারণ",
      "url": "bdlaws URL"
    }
  ],
  "relevant_cases": [
    {
      "case_no": "Case number",
      "year": 2020,
      "court": "HCD বা AD",
      "outcome": "outcome",
      "why_relevant": "এই case আপনার situation এ কেন relevant — ratio"
    }
  ],
  "honest_assessment": {
    "strengths": ["Specific strength 1 based on your facts", "Strength 2"],
    "weaknesses": ["Specific weakness 1 — কী সমস্যা হতে পারে", "Weakness 2"],
    "critical_factors": ["এটা না থাকলে case হারবেন — must-have 1", "Must-have 2"],
    "win_rate_context": "Similar cases এ X% জিতেছে — কেন এই percentage"
  },
  "strategy": {
    "recommended_action": "সবচেয়ে effective পদক্ষেপ এবং কেন এটাই best",
    "forum": "কোন specific court — civil court/HC/tribunal এবং কেন",
    "relief_to_seek": "Exactly কী চাইবেন — injunction/declaration/possession/damages",
    "documents_needed": ["Document 1 — কেন দরকার", "Document 2 — কেন দরকার"],
    "estimated_timeline": "X-Y বছর — Bangladesh court reality অনুযায়ী",
    "immediate_steps": "এখনই ২৪-৭২ ঘণ্টার মধ্যে যা করবেন"
  },
  "outcome_prediction": {
    "historical_win_rate": "X% similar cases এ petitioner জিতেছে (N cases থেকে)",
    "factors_in_your_favor": ["Specific factor 1", "Factor 2"],
    "factors_against": ["Specific risk 1", "Risk 2"],
    "estimated_timeline": "X-Y বছর লাগতে পারে কারণ",
    "recommendation": "আমার honest expert opinion"
  },
  "action_checklist": [
    "১. [Immediate action — আজকেই]",
    "২. [Next action — এই সপ্তাহে]",
    "৩. [Following action — এই মাসে]"
  ],
  "follow_up_questions": ["More info দরকার হলে specific প্রশ্ন — max ২টা"],
  "disclaimer": "এটা legal research assistance। চূড়ান্ত সিদ্ধান্তের জন্য qualified আইনজীবীর পরামর্শ নিন।"
}

Rules:
1. Provided context + general Bangladesh legal knowledge দুটোই use করো
2. Specific section number ছাড়া আইন cite করবে না
3. JSON ছাড়া কিছু না — কোনো preamble, markdown বা extra text না
4. action_checklist সবসময় দাও — এটাই client এর সবচেয়ে কাজের অংশ
5. follow_up_questions দাও যদি situation unclear হয়"""


def call_llm(messages: list[dict], use_claude: bool = False) -> str:
    """LLM call — Claude (better) অথবা Groq (fast)"""
    if use_claude and claude_client:
        # Claude API
        sys_msg = next((m["content"] for m in messages if m["role"] == "system"), SYSTEM_PROMPT)
        user_msgs = [m for m in messages if m["role"] != "system"]

        response = claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=sys_msg,
            messages=user_msgs,
        )
        return response.content[0].text
    else:
        # Groq API
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=2000,
        )
        return response.choices[0].message.content


def safe_parse_json(text: str) -> dict:
    """LLM output থেকে JSON parse করো — fallback সহ।"""
    # markdown code blocks সরাও
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Partial JSON extract করার চেষ্টা
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"summary": text, "error": "JSON parse failed — raw response"}


# ─── ENDPOINTS ────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "status": "Bangladesh Legal AI v2",
        "judgements": judgements_col.count(),
        "laws": laws_col.count(),
        "features": [
            "dual_collection_rag",
            "conversation_memory",
            "outcome_prediction",
            "metadata_filtering",
            "structured_json_response",
        ],
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    """
    Main chat endpoint।
    - Conversation history support (multi-turn)
    - Intent-based dual retrieval
    - Structured JSON response
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question cannot be empty")

    # 1. Intent classify করো
    intent = classify_intent(question)

    # 2. Retrieve করো
    retrieved = retrieve_dual(
        query=question,
        intent=intent,
        filters=req.filters,
    )

    # 3. Outcome stats compute করো (সবসময়, prediction request না হলেও show করবো না)
    outcome_stats = compute_outcome_stats(retrieved["judgements"])

    # 4. Context build করো
    context = build_context(retrieved)

    # 5. Conversation history prepare করো
    history_messages = []
    for msg in req.history[-MAX_HISTORY:]:
        history_messages.append({
            "role": msg.role,
            "content": msg.content,
        })

    # 6. User prompt build করো
    user_content = f"""Context from Bangladesh legal database:

{context}

---
Historical outcome statistics (from {outcome_stats.get('total_similar_cases', 0)} similar cases):
{json.dumps(outcome_stats, ensure_ascii=False)}

---
User question: {question}

Intent detected: {intent}"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history_messages,
        {"role": "user", "content": user_content},
    ]

    # 7. LLM call (Claude preferred, Groq fallback)
    raw_response = call_llm(messages, use_claude=bool(claude_client))

    # 8. Parse structured response
    structured = safe_parse_json(raw_response)

    # 9. Source metadata attach করো
    sources = {
        "cases": [
            {
                "case_no": j["meta"].get("case_no", j["meta"].get("filename")),
                "year":    j["meta"].get("year"),
                "type":    j["meta"].get("case_type"),
                "outcome": j["meta"].get("outcome"),
                "division": j["meta"].get("division"),
                "subjects": j["meta"].get("subjects"),
                "score":   round(j["score"], 3),
            }
            for j in retrieved["judgements"]
        ],
        "laws": [
            {
                "title":   l["meta"].get("title"),
                "section": l["meta"].get("section"),
                "url":     l["meta"].get("url"),
                "year":    l["meta"].get("year"),
                "score":   round(l["score"], 3),
            }
            for l in retrieved["laws"]
        ],
    }

    return {
        "answer":         structured,
        "sources":        sources,
        "intent":         intent,
        "outcome_stats":  outcome_stats,
        "retrieval_count": {
            "judgements": len(retrieved["judgements"]),
            "laws":       len(retrieved["laws"]),
        },
    }


@app.post("/api/predict")
def predict_outcome(req: PredictionRequest):
    """
    Case outcome prediction।
    Statistical + LLM analysis।
    """
    situation = req.situation.strip()

    # Similar cases find করো
    filters = None
    if req.case_type:
        filters = {"case_type": req.case_type}

    retrieved = retrieve_dual(
        query=situation,
        n=15,   # বেশি cases দেখো prediction এর জন্য
        intent="prediction",
        filters=filters,
    )

    outcome_stats = compute_outcome_stats(retrieved["judgements"])
    context = build_context(retrieved)

    prediction_prompt = f"""Legal situation:
{situation}

Similar historical cases and applicable laws:
{context}

Historical statistics:
{json.dumps(outcome_stats, ensure_ascii=False, indent=2)}

এই situation এ যদি case করা হয় তাহলে কী হতে পারে তার detailed prediction দাও।
outcome_prediction field টা সবচেয়ে important — historical data based হতে হবে।"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prediction_prompt},
    ]

    raw = call_llm(messages, use_claude=bool(claude_client))
    structured = safe_parse_json(raw)

    return {
        "prediction": structured,
        "outcome_stats": outcome_stats,
        "similar_cases_used": len(retrieved["judgements"]),
        "laws_referenced": len(retrieved["laws"]),
    }


@app.post("/api/search")
def search(req: SearchRequest):
    """Raw search — judgements and/or laws।"""
    chroma_filter = build_chroma_filter(req.filters)

    results = {"judgements": [], "laws": []}

    if req.search_type in ("judgements", "both"):
        kwargs = {"query_texts": [req.query], "n_results": req.n_results}
        if chroma_filter:
            kwargs["where"] = chroma_filter
        try:
            r = judgements_col.query(**kwargs)
            seen = set()
            for doc, meta, dist in zip(
                r["documents"][0], r["metadatas"][0], r["distances"][0]
            ):
                fname = meta.get("filename")
                if fname not in seen:
                    seen.add(fname)
                    results["judgements"].append({
                        "case_no":  meta.get("case_no", fname),
                        "year":     meta.get("year"),
                        "type":     meta.get("case_type"),
                        "outcome":  meta.get("outcome"),
                        "division": meta.get("division"),
                        "subjects": meta.get("subjects"),
                        "judges":   meta.get("judges"),
                        "preview":  doc[:400],
                        "score":    round(1 - dist, 3),
                    })
        except Exception as e:
            results["error_judgements"] = str(e)

    if req.search_type in ("laws", "both"):
        try:
            r = laws_col.query(query_texts=[req.query], n_results=req.n_results)
            for doc, meta, dist in zip(
                r["documents"][0], r["metadatas"][0], r["distances"][0]
            ):
                results["laws"].append({
                    "title":   meta.get("title"),
                    "section": meta.get("section"),
                    "url":     meta.get("url"),
                    "year":    meta.get("year"),
                    "type":    meta.get("act_type"),
                    "preview": doc[:400],
                    "score":   round(1 - dist, 3),
                })
        except Exception as e:
            results["error_laws"] = str(e)

    return results


@app.post("/api/generate-doc")
def generate_document(req: DocRequest):
    """Legal document generation — Bangladesh law অনুযায়ী।"""
    templates = {
        "nda":       "Non-Disclosure Agreement (গোপনীয়তা চুক্তি)",
        "sale_deed": "Sale Deed (বিক্রয় দলিল)",
        "rent":      "Rent Agreement (ভাড়া চুক্তি)",
        "complaint": "General Complaint (সাধারণ অভিযোগ)",
        "affidavit": "Affidavit (হলফনামা)",
        "writ":      "Writ Petition (রিট পিটিশন)",
        "plaint":    "Plaint / Civil Suit (আরজি)",
    }

    doc_name = templates.get(req.doc_type, req.doc_type)

    # Relevant laws retrieve করো
    retrieved = laws_col.query(
        query_texts=[f"{doc_name} Bangladesh law requirements"],
        n_results=3,
    )
    law_context = ""
    for doc, meta in zip(retrieved["documents"][0], retrieved["metadatas"][0]):
        law_context += f"\n{meta['title']} — {meta['section']}:\n{doc[:500]}\n"

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

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=2500,
    )

    return {
        "document": response.choices[0].message.content,
        "doc_type": doc_name,
        "laws_referenced": [
            {"title": m["title"], "section": m["section"], "url": m["url"]}
            for m in retrieved["metadatas"][0]
        ],
    }


@app.get("/api/stats")
def get_stats():
    """Database statistics।"""
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
            "dual_collection": True,
            "conversation_memory": True,
            "outcome_prediction": True,
            "metadata_filtering": True,
            "bm25_planned": True,
        },
    }


@app.get("/api/filter-options")
def get_filter_options():
    """Available filter options for frontend।"""
    return {
        "case_types": [
            "civil_revision", "writ_petition", "criminal_appeal",
            "criminal_revision", "first_appeal", "death_reference", "suo_moto",
        ],
        "outcomes": [
            "rule_absolute", "rule_discharged", "allowed",
            "dismissed", "disposed", "unknown",
        ],
        "divisions": ["HCD", "AD"],
        "year_range": {"min": 1950, "max": 2024},
    }
