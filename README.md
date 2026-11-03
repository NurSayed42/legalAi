# Bangladesh Legal RAG — Legal Research Assistant

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
| [supremecourt.gov.bd](https://supremecourt.gov.bd) | Supreme Court judgement PDFs (High Court Division) | 8,615 judgements | `scraper.py` |
| [bdlaws.minlaw.gov.bd](http://bdlaws.minlaw.gov.bd) | Full text of Bangladesh Acts, section-level | 1,399 laws | `govt_scraper.py` |

**Scraping approach:** `requests` + `BeautifulSoup` to walk the judgement listing pages and collect PDF links (paginated, with retry + resume-from-existing-file support so re-runs don't re-download), then `PyPDF2` to extract raw text from each downloaded PDF (`extract_text.py`). Law pages are scraped directly as HTML (act-details pages), with footnote markers and superscripts stripped during cleaning.

**Known data gaps:**
- ~2,400 judgements (mostly s.498 bail and s.561A quashing orders) are Bangla typeset in a legacy Bijoy/SutonnyMJ font. `PyPDF2` extracts them as ASCII mojibake (`q¡C−L¡VÑ` = "হাইকোর্ট"). They are flagged `text_quality=legacy_bangla_font`, not converted; the reranker filters them out of answers.
- Not in the scraped laws corpus: NI Act s.138A, the Bangladesh Labour Act 2006, the Premises Rent Control Act 1991.

---

## 4. Preprocessing & Indexing

- **Metadata extraction** (`enrich_judgements.py`) — rule-based (regex): year, division (AD/HCD — every judgement in the current corpus is HCD), case type, outcome (rule absolute/discharged, dismissed, allowed, disposed — from the filename first, then the last 800 characters of the text), judges, subject keywords.
- **Metadata cleanup** (`clean_metadata.py`) — run before `enrich_judgements.py`, which picks up its output:
  - *Split words from PDF extraction* (`o f`, `appella nt`, `V E R S U S`) are re-joined using the corpus's own word counts — a pair is joined only when the joined word is far more frequent than the split pair and the two halves aren't both common words (so `a cross` stays).
  - *Case number and case type* come from the judgement header (`Criminal Appeal No. 6777 of 2023`) rather than the filename, which is often an internal code (`TN 72663 2023`). This cut `case_type=other` from 5,332 to 33 judgements.
  - *Subjects* are re-tagged by an LLM (Gemini, 20 judgements per call, cached and resumable) into a fixed 27-label taxonomy, replacing keyword matching that tagged any mention of "land" or "service".
  - *Bangla Acts* get their section numbers and years (`১৷` → `1`, `২০০০` → `2000`), which were all missing before; written to the laws collection in place.
  - A before/after report goes to `evals/reports/metadata_report.json`.
- **Chunking** — judgements are split 600 words per chunk with an 80-word overlap. Laws use section-level granularity — each Act section is its own document.
- **Embedding model** — `paraphrase-multilingual-MiniLM-L12-v2`, chosen because queries and sources mix Bangla and English. It truncates input at 128 tokens, so only the start of each judgement chunk is embedded; BM25 and the reranker see more of it.
- **Vector store** — ChromaDB (persistent, cosine), two collections: `bangladesh_judgements` and `bangladesh_laws`.

---

## 5. Architecture — LangGraph Pipeline

**8-node LangGraph state machine** with two retry loops:

```
User Query
    │
    ▼
understanding      (LLM router: intent, standalone rewrite, 2-3 English + Bangla
    │                sub-queries, missing-fact questions; keyword rules override
    │                the router for procedure / prediction / case search, and
    │                law_lookup is only kept when the question names an Act or section)
    ▼
query_expansion    (legal alias dictionary — চেক ডিসঅনার / chek dishonour → NI Act
    │                s.138/140/141, জামিন → CrPC s.497/498, তালাক → MFLO s.7,
    │                জমি দখল → SRA s.8/9 + CrPC s.145, writ → Constitution art.102;
    │                "Section N" without an Act → Penal Code, CrPC, CPC, Evidence Act,
    │                NI Act, Constitution, in that order)
    ▼
retrieval          (vector + BM25 for every query, all rankings merged with
    │                Reciprocal Rank Fusion; judgements also weighted by precedent
    │                (AD > HCD, recency); alias sections pinned by act + section;
    │                judgements are retrieved for every intent except law_lookup)
    ▼
rerank             (BAAI/bge-reranker-v2-m3 cross-encoder against the English
    │                sub-query + alias expansion; chunks below 0.05 are dropped)
    ▼
sufficiency_check  (nothing passed the reranker → one broader retry without
    │                filters; still nothing → retrieval_sufficient=false and a fixed
    │                "not enough sources found" answer, no LLM guess)
    ▼
generation         (Gemini with a response schema / Groq fallback, temperature 0.1;
    │                prompt wraps <user_question> and <retrieved_sources>)
    ▼
verifier           (every cited Act + section and case number must be in the sources;
    │                a cited section that wasn't retrieved gets a targeted act+section
    │                lookup and must pass the reranker; section/case numbers inside the
    │                text are checked too; also rejects personal-experience claims,
    │                incomplete procedure answers, and question-only answers →
    │                one regeneration with the problems listed)
    ▼
finalize           → fixed answer structure; citations still failing are removed
                     and listed in unverified_citations
```

**Node list (from the actual `StateGraph`):** `understanding → query_expansion → retrieval → rerank → sufficiency_check → generation → verifier → finalize`

**Answer structure** (identical for every intent and for UI or direct API calls): `short_answer` → `applicable_laws` (act, section, what it says, how it applies) → `relevant_cases` (case_no, year, division, one-line holding; up to 3) → `procedure` (numbered steps, court, deadline, documents, cost, penalties) → `risks` → `when_to_see_lawyer` → `follow_up_options` → `disclaimer`.

---

## 6. Why This Approach

| Design choice | Reasoning |
|---|---|
| RAG instead of fine-tuning | Case law and statutes change; RAG lets the knowledge base update independently of the model. |
| Hybrid retrieval (vector + BM25) via RRF | Vector search alone misses exact section numbers and case citations; BM25 alone misses conceptually similar cases phrased differently. RRF combines rankings without hand-tuned weights. |
| Legal alias dictionary + LLM sub-queries | Users write "chek dishonour hole ki korbo"; the statutes say "dishonour of cheque for insufficiency of funds". Without expansion, Bangla questions retrieved unrelated Bangla Acts (the Copyright Act for a cheque question). |
| Cross-encoder reranker | RRF is rank-only, so it can't say whether anything relevant was found. The reranker scores each chunk against the question; calibration showed relevant pairs at 0.29–0.74 and irrelevant ones ≤ 0.03. It reranks against the English sub-query because it scored the right NI Act section near zero against the raw Bangla/Banglish question. |
| Precedent hierarchy weighting | An Appellate Division ruling should outrank a High Court Division observation. (The corpus is HCD-only today, so only the recency bonus has an effect.) |
| Verifier node | A fabricated or misapplied citation is the biggest trust risk in legal AI. Every citation is checked against the retrieved text before the answer reaches the user. |
| No persona | The model is told it is an AI research assistant, and a regex check rejects claims like "আমার ৩০ বছরের অভিজ্ঞতায়" / "in my 20 years" and regenerates. |
| Framework first, then questions | Prediction questions with missing facts get the legal framework and options with citations first, then follow-up questions — never only a clarifying question. |
| Contradiction detection | A conflict is flagged only when, on the same subject, the petitioner won in some retrieved cases and lost in others — unknown or disposed outcomes don't count. |
| Gemini primary / Groq fallback | Gemini for answer quality; Groq (`openai/gpt-oss-120b`) if Gemini errors or is rate-limited. |

---

## 7. Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (`StateGraph`, conditional edges) |
| API | FastAPI (`api_v3.py`), rules in `legal_rules.py` |
| LLMs | Gemini (primary, schema-constrained JSON), Groq — `openai/gpt-oss-120b` (fallback) |
| Retrieval | ChromaDB (vector) + `rank-bm25` (keyword), merged via Reciprocal Rank Fusion |
| Reranking | `BAAI/bge-reranker-v2-m3` (sentence-transformers `CrossEncoder`; int8-quantized on CPU) |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers) |
| Scraping | `requests` + `BeautifulSoup4` (HTML/PDF discovery), `PyPDF2` (PDF text extraction) |
| Language | Python |

---

## 8. API Endpoints

- **`POST /api/chat`** — main Q&A endpoint. Accepts `question`, `history`, and optional `filters` (`year_from`, `year_to`, `case_type`, `outcome`, `division`; applied to both vector and BM25 results). Response: `answer` (structure above), `sources` (`laws` and up to 3 `cases`, with reranker scores), `intent` (`law_lookup` / `case_search` / `prediction` / `procedure` / `general`), `outcome_stats`, `contradictions_detected`, `citation_verified`, `unverified_citations`, `retrieval_sufficient`, `retrieval_attempts`, `answer_attempts`.
- **`POST /api/predict`** — the same pipeline with intent forced to `prediction`; same fields, plus `prediction` (= `answer`), `similar_cases_used`, `laws_referenced`.
- **`POST /api/search`** — hybrid search (vector + BM25) without answer generation. `search_type`: `cases` (or the old name `judgements`), `laws`, or `both`; returns up to `n_results` of each under `cases` and `laws`.
- **`POST /api/generate-doc`** — drafts a legal document (NDA, sale deed, rent agreement, affidavit, writ petition, plaint, etc.) grounded in retrieved law sections.
- **`GET /api/filter-options`**, **`GET /api/stats`** — metadata/browsing endpoints.

---

## 9. Evaluation & Tests

```bash
python -m pytest tests/test_rules.py -q          # deterministic rules, no server needed
uvicorn api_v3:app --port 8000                  # then, in another terminal:
python -m pytest tests/test_regression.py -v     # 4 live questions: cheque dishonour procedure,
                                                # murder bail, "Section 302", divorce procedure
python evals/run_eval.py --label after           # 20 questions → eval/results/after.json
python evals/run_eval.py --compare before after  # side-by-side summary
```

`run_eval.py` logs, per question: retrieved laws and cases, `applicable_laws` count, `citation_verified`, unverified citations, retrieval attempts, sufficiency, persona-claim detection and latency.

---

## 10. Known Limitations / Future Work

- **Latency on CPU** — the reranker costs ~0.4 s per (query, chunk) pair even int8-quantized, and Gemini's free tier often takes 10+ s per call; a typical answer takes about a minute on a 4-core CPU. A GPU or a hosted reranker would cut most of this.
- **Gemini free-tier rate limit** — 15 requests/minute per model. Each answer uses 2–3 calls; under load the Groq fallback takes over.
- **High Court Division only** — no Appellate Division judgements in the corpus yet, so the AD > HCD weight has nothing to act on.
- **Legacy-font judgements** — ~2,400 Bangla judgements are unreadable mojibake until converted from Bijoy encoding to Unicode.
- **Outcome coverage** — the rule-based extractor can't determine the outcome for ~54% of judgements (`unknown`), which limits `outcome_stats` and contradiction detection.
- **Reranker threshold** (`RERANK_THRESHOLD = 0.05`) was set from a small calibration probe, not tuned on real query logs.
- **Inline citations** are flagged, not removed — removing a section number from the middle of a sentence would garble it.
- **Statute currency** — repealed/amended sections aren't tracked yet.

---

## 11. Project Structure

```
scraper.py                # Supreme Court judgement PDF discovery + download
extract_text.py           # PDF → text extraction (PyPDF2)
govt_scraper.py           # Bangladesh Acts scraper (bdlaws, section-level)
build_laws_vectordb.py    # Laws collection (section-level documents)
clean_metadata.py         # OCR split-word repair, case type from header, LLM subject labels, Bangla section numbers
enrich_judgements.py      # Judgement metadata + judgements collection (uses clean_metadata.py output if present)
api_v3.py                 # FastAPI app + LangGraph pipeline (current)
legal_rules.py            # Alias dictionary, intent rules, persona check, answer schema
api_v2.py                 # Previous API version
legal_ui_v2.html          # Single-file web UI for the API
evals/                    # run_eval.py, reports/
tests/                    # test_rules.py (offline), test_regression.py (live server)
requirements.txt
.env.example
```

---

## 12. Getting Started

```bash
git clone https://github.com/NurSayed42/legalAi.git
cd legalAi
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # add your API keys
```

Build the data and vector store (one-time; downloads public court and legislation data):

```bash
python scraper.py                 # judgement PDFs
python extract_text.py            # PDF → text → judgements.json
python govt_scraper.py            # statutes → bangladesh_laws.json
python build_laws_vectordb.py     # laws collection
python clean_metadata.py --subjects --apply   # cleanup + LLM subject labels + Bangla section numbers
python enrich_judgements.py       # judgements collection (uses the cleanup output)
```

Run the API and open the UI (the first start downloads the ~2.3 GB reranker):

```bash
uvicorn api_v3:app --port 8000
# then open legal_ui_v2.html in a browser (it calls http://localhost:8000)
```

---

## 13. Environment Variables

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Primary LLM |
| `GROQ_API_KEY` | Fallback LLM (Groq) |
| `GEMINI_MODEL` | Optional Gemini model override (default `gemini-3.5-flash`) |
| `GROQ_MODEL` | Optional Groq model override (default `openai/gpt-oss-120b`) |
| `CHROMA_DB_PATH` | Path to the persistent ChromaDB directory (used by the API and both build scripts) |

---

## Disclaimer

This is a research tool, not legal advice. Answers should be checked against the cited judgements and statutes.

## Author

**Nur Sayed** — Lead Engineer at VecoSoft · [GitHub](https://github.com/NurSayed42)
