# Bangladesh Legal AI — RAG-based Legal Research Assistant

A retrieval-augmented generation (RAG) system that answers legal questions grounded in Bangladesh Supreme Court judgements and the full body of Bangladesh statutes, with explicit safeguards against hallucinated citations, weak retrieval, and overconfident predictions.

---

## 1. Problem Statement

In Bangladesh, real people's cases — a family fighting over inherited land, a worker unfairly dismissed, someone wrongly accused — often hinge on whether their lawyer can find the *right* precedent in time. That precedent is buried across tens of thousands of judgements and hundreds of statutes, with no searchable, unified way to check it. Junior lawyers and law students spend days on research that a senior lawyer's intuition would shortcut in minutes — and even then, a missed or misapplied precedent can mean a client loses a case they should have won.

Handing this job to a generic AI chatbot makes the problem worse, not better — because a fluent, confident answer is more dangerous than no answer at all when:
- It **invents a citation** that doesn't exist, or misquotes a real one — and a lawyer repeats it in a filing without realizing.
- It **answers confidently on thin evidence**, when the honest answer is "not enough precedent was found."
- It **treats a lower court's passing remark as equal to a binding Supreme Court ruling**, misleading someone about how strong their legal ground actually is.
- It **hides the fact that precedent is split** on an issue, when knowing that split is often the most important thing a researcher needs to know.
- It **guesses at how a case will turn out** from a vague description instead of asking what's actually missing — turning a research tool into a source of false confidence.

The real cost of these failure modes isn't a bad demo — it's a wrong legal opinion someone acts on.

## 2. The Solution

This project builds a legal research assistant that treats *trustworthiness* as the primary design constraint, not an afterthought bolted on at the end. Every answer is traceable to a real judgement or statute section; every citation the model generates is checked against the actual retrieved text before it reaches the user; the system explicitly measures whether it found enough to answer at all, and says so when it hasn't; and when precedent genuinely conflicts, that conflict is surfaced instead of papered over. In short: it's built to know the difference between "I found a clear answer" and "I'm guessing" — and to never present the second as the first.

---

## 3. Data

| Source | What | Count | Scraper |
|---|---|---|---|
| [supremecourt.gov.bd](https://supremecourt.gov.bd) | Supreme Court judgement PDFs (Appellate Division + High Court Division) | 8,615 judgements | `scraper.py` |
| [bdlaws.minlaw.gov.bd](http://bdlaws.minlaw.gov.bd) | Full text of Bangladesh Acts, section-level | 1,399 laws | `govt_scraper.py` / `laws_scraper_fixed.py` |

**Scraping approach:** `requests` + `BeautifulSoup` to walk the judgement listing pages and collect PDF links (paginated, with retry + resume-from-existing-file support so re-runs don't re-download), then `PyPDF2` to extract raw text from each downloaded PDF (`extract_text.py`). Law pages are scraped directly as HTML (act-details pages), with footnote markers and superscripts stripped during cleaning.

---

## 4. Preprocessing & Indexing

- **Metadata extraction** (`enrich_judgements.py`) — fully rule-based (regex), no LLM calls needed, so it's fast and free to re-run: extracts year, division (AD/HCD), case type (writ petition, criminal appeal, civil revision, etc.), outcome (rule absolute/discharged, dismissed, allowed, disposed — read from filename first, then the last 800 characters of the judgement text), judges, and subject-matter keywords (land dispute, criminal, contract, family, writ, service, tax, tenancy, company, cheque dishonour).
- **Chunking** — judgements are split 600 words per chunk with an 80-word overlap (tuned down from an earlier, larger chunk size for better retrieval precision). Laws use section-level granularity instead — each Act section is stored as its own document, since sections are already a natural, citation-sized unit and don't need further splitting.
- **Embedding model** — `paraphrase-multilingual-MiniLM-L12-v2` (via `sentence-transformers`, called through ChromaDB's `SentenceTransformerEmbeddingFunction`). Chosen specifically because queries and judgement text mix Bangla and English, and this model handles both without needing separate pipelines.
- **Vector store** — ChromaDB (persistent, cosine similarity), two collections: `bangladesh_judgements` and `bangladesh_laws`.

---

## 5. Architecture — LangGraph Pipeline

**7-node LangGraph state machine** with conditional routing:

```
User Query
    │
    ▼
understanding      (Gemini/Groq — intent classification, query rewrite,
    │                clarification check; keyword match is the fallback)
    │
    ├── vague situation + prediction intent ──► clarify
    │                                            (returns follow-up questions
    │                                             instead of guessing)
    ▼
retrieval           (vector search + BM25, merged via
    │                Reciprocal Rank Fusion, then re-scored by
    │                precedent weight: AD > HCD, + recency bonus)
    ▼
sufficiency_check   (fused retrieval score < threshold?
    │                if yes → back to retrieval with a broader query,
    │                max 2 retry attempts)
    ▼
generation          (Gemini primary / Groq fallback,
    │                structured JSON output)
    ▼
citation_verifier   (every cited act/section/case number checked
    │                against retrieved context; invalid citation →
    │                back to generation with a warning appended)
    ▼
finalize            → structured JSON response
```

**Node list (from the actual `StateGraph`):** `understanding → clarify → retrieval → sufficiency_check → generation → citation_verifier → finalize`
---

## 6. Why This Approach

| Design choice | Reasoning |
|---|---|
| RAG instead of fine-tuning | Case law and statutes change; RAG lets the knowledge base update independently of the model. |
| Hybrid retrieval (vector + BM25) via RRF | Vector search alone misses exact section numbers and case citations; BM25 alone misses conceptually similar cases phrased differently. RRF combines both rankings without needing to hand-tune a weighting between them. |
| Precedent hierarchy weighting | Not all judgements carry equal legal weight — an Appellate Division ruling should outrank a High Court Division observation on the same point. |
| Sufficiency check with retry | Rather than letting the model answer confidently on thin context, the pipeline explicitly measures whether retrieval actually found enough, and re-queries before giving up or answering. |
| Citation verifier node | The single biggest trust risk in legal AI is a fabricated or misapplied citation — every citation is checked against real retrieved text before the answer reaches the user. |
| Contradiction detection | Legal questions often have genuinely conflicting precedent; surfacing that instead of hiding it behind one confident answer is more honest and more useful to a real researcher. |
| Clarification gate on prediction queries | Predicting a legal outcome from an underspecified situation is exactly where hallucination-by-confident-guessing is most dangerous — better to ask than to guess. |
| Gemini primary / Groq fallback | Gemini for answer quality; Groq (Llama 3.3 70B) as a fast, free-tier fallback if Gemini errors, times out, or is rate-limited — keeps the system available without paid infrastructure. |
| Rule-based (non-LLM) metadata extraction | Metadata (year, outcome, case type, etc.) is extracted with regex, not an LLM call — deterministic, free, and fast enough to re-run over 8,600+ documents. |

---

## 7. Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (`StateGraph`, conditional edges) |
| API | FastAPI (`api_v3.py`) |
| LLMs | Gemini (primary), Groq — Llama 3.3 70B (fallback) |
| Retrieval | ChromaDB (vector) + `rank-bm25` (keyword), merged via Reciprocal Rank Fusion |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers) |
| Scraping | `requests` + `BeautifulSoup4` (HTML/PDF discovery), `PyPDF2` (PDF text extraction) |
| Language | Python |

---

## 8. API Endpoints

- **`POST /api/chat`** — main Q&A endpoint. Accepts `question`, `history`, and optional `filters` (e.g. `year_from`, `case_type`). Response includes `intent`, `outcome_stats`, `contradictions_detected`, `citation_verified`, and `retrieval_attempts` alongside the answer and sources.
- **`POST /api/predict`** — outcome prediction, gated by the clarification step; returns `follow_up_options` (clickable multiple-choice questions, or open-ended `follow_up_questions` as a fallback) instead of a guess when the situation is underspecified.
- **`POST /api/search`** — hybrid search (vector + BM25) without full answer generation.
- **`POST /api/generate-doc`** — drafts a legal document (NDA, sale deed, rent agreement, affidavit, writ petition, plaint, etc.) grounded in retrieved relevant law sections.
- **`GET /api/filter-options`**, **`GET /api/stats`** — metadata/browsing endpoints.

---

## 9. Known Limitations / Future Work

- **Statute currency** — repealed/amended sections aren't tracked yet; amendment metadata would need to be added from bdlaws during the laws-indexing step.
- **Sufficiency threshold** (`SUFFICIENCY_THRESHOLD = 0.01`) is a starting value, not yet empirically tuned against real query logs.
- **Dual-model self-consistency** — running both Gemini and Groq on the same query and flagging disagreement is designed but not yet implemented.
- **Few-shot exemplars** — the generation prompt doesn't yet include verified real-case examples, which would likely improve grounding further.

---
