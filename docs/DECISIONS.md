# Decisions and assumptions

Newest first within each section. Each entry says what was decided, why, and what would change it.

## Spec vs. actual state (found at the start of the SPEC.md work, 2026-11-03)

**D-001 — The corpus is High Court Division only.** SPEC §1 says "AD + HCD". `extract_division()` finds "APPELLATE DIVISION" in the first 400 characters of 0 of 8,615 judgements, and only 27 mention it in the first 2,000 (HCD judgements citing AD). `scraper.py` fetches `div_id=2`. The AD > HCD weight stays in code but has nothing to act on until AD judgements are added.

**D-002 — The pipeline had already changed before SPEC.md.** SPEC §1 lists `understanding → clarify → … → citation_verifier → finalize` and Groq Llama 3.3 70B. On this branch the graph is `understanding → query_expansion → retrieval → rerank → sufficiency_check → generation → verifier → finalize` (commit 98c6eeb, from an earlier explicit request: alias expansion, bge-reranker-v2-m3, framework-first prediction instead of a clarify-only reply). Groq retired `llama-3.3-70b-versatile`; the fallback is `openai/gpt-oss-120b`. The "baseline" in Phase 2 will be measured on this pipeline; `evals/reports/before.json` records the original pipeline on the original index for reference.

**D-003 — Backward compatibility: old field names restored as aliases.** The earlier rounds renamed `answer.summary` → `short_answer`, `sources.*.score` → `rerank_score`, and `/api/search` `judgements` → `cases`. SPEC §1 says never remove response fields, so the old names are returned alongside the new ones. The old answer's conditional keys (`irac_analysis`, `honest_assessment`, `strategy`, `outcome_prediction`, `action_checklist`) were only present for some intents and are not restored.

**D-004 — The v1 judgements index was rebuilt, not preserved.** An interrupted rebuild (2026-10-27) had deleted `bangladesh_judgements` and re-added only 6,900 of ~45K chunks. It is rebuilt from the unchanged `judgements.json` with the original regex metadata (`enrich_judgements.py --text raw --labels regex`), which is deterministic, so v1 = the original index. `enrich_judgements.py` now builds into `<name>__building` and swaps only when complete, so this can't recur.
The laws collection is not exactly the original: on 2026-10-27 `clean_metadata.py --apply` filled empty `section_no` (3,982) and `year` (4,230) for Bangla Acts in place. Documents and embeddings are unchanged.

**D-005 — Raw PDFs are not on this machine.** `downloaded_pdfs/` does not exist; only `judgements.json` (PyPDF2 text) is present. Phase 1 re-extraction needs the PDFs re-downloaded with `scraper.py` (8,615+ files from supremecourt.gov.bd, several hours). Not done without the owner's go-ahead.

**D-006 — Test/eval layout.** The earlier `eval/` folder was split into `evals/` (runner, reports) and `tests/` (unit + live regression tests) to match SPEC §3.

## Phase 1 — data quality

**D-101 — Audit buckets and thresholds** (`ingestion/quality_audit.py`, `THRESHOLDS`). All are guesses tuned on the 30-file sample below:
- broken: < 50 words, legacy-font characters > 2% of letters, non-printable > 1% of characters, garbage score > 0.15, or > 30% duplicated 12-word shingles;
- suspect: garbage score > 0.05, 1–2 letter fragments > 6% of Latin tokens (split words), or average word length outside 3.5–9 for Latin text;
- good: everything else.

**D-102 — Findings from the 30-file manual sample** (seed 42; 10 broken, 9 suspect, 8 good, plus 3 targeted).
1. *Legacy fonts — two encodings.* Bangla text typeset in legacy fonts comes out of PyPDF2 as ASCII mojibake. Two encodings appear: `h¡wm¡−cn p¤fË£j ®L¡VÑ` (¡ ¢ − Ñ …) and SutonnyMJ/Bijoy `evsjv‡`k miKvi` (‡ † ¨ …), both meaning "বাংলাদেশ…". The second looks like Latin letters, so the first audit version missed it; SutonnyMJ signature characters were added (379 files carry them). Consequence: a single Bijoy→Unicode converter would not fix the corpus — OCR is the general fix.
2. *Mixed files.* Many "broken" files are English judgements with a Bangla header or quoted Bangla passage in a legacy font (e.g. `642436_WP12889of2012`, `704153_WP1481of2014`). Most of their text is fine. → Extraction must decide per page, not per file.
3. *Duplicated text layers.* Some legacy-font files repeat each line 2–4 times without spaces (`p¤fË£j ®L¡VÑh¡wm¡−cn p¤fË£j ®L¡VÑ…`, e.g. `1327177_CriminalAppealNo.674of2018`). The 12-word shingle check catches only 9 files because the repeats are glued together; these files are broken by the legacy-font rule anyway.
4. *Near-empty files are real.* All sampled files under 150 words were genuine short admission orders ("This appeal will be heard. Call for the records…"), clean text. Threshold lowered to 50 words. No file has < 10 words — because `extract_text.py` silently dropped PDFs with no text layer (`if text:`). Image-only scans are therefore missing from the corpus entirely; their number is unknown until the PDFs are re-fetched.
5. *Split words are everywhere.* Even "good" files contain PyPDF2 spacing errors: `BANGLADE SH`, `Constitutio n`, `20 20`, `appella nt`, `Section s 83A`. "Suspect" files have them at > 6% of tokens (up to 48%).
6. *Unicode Bangla exists.* Some files have real Unicode Bangla (e.g. `2358026_C.R.326of2023`, Bangla share 32%) and extract fine.

**D-103 — OCR trigger is "page fails the quality check", not only "page has no text".** SPEC Phase 1.4 says OCR only for pages with no extractable text. Legacy-font pages *do* have extractable text — it's mojibake — so that rule would leave all ~2,800 legacy-font files broken. The cascade OCRs a page when its text is empty **or** the page fails the same legacy-font/garbage checks as the audit.

**D-105 — Unreadable spans include fonts embedded without a character map.** Two sample files had Bangla in `Vrinda` (a Unicode font) embedded without ToUnicode, so the text layer returns glyph ids (`\x01\x02\x03`). A span counts as unreadable — and is OCR'd — if its font is a known legacy font, if > 30% of it is control codes, or if > 10% of its letters are legacy-font mojibake characters.

**D-106 — OCR settings.** 200 dpi, `ben+eng`, Tesseract `tessdata` (standard) models. 200 dpi was faster than 300 (3.7 s vs 6.3 s per page) and produced less junk on the probe page. Known issue: `ben+eng` inserts Latin junk into Bangla text; to be tested: `ben`-only OCR of the unreadable spans, and `tessdata_best`.

**D-104 — Tesseract.** Tesseract 5.5 is installed at `C:\Program Files\Tesseract-OCR` with `eng` only and is not on PATH. `ben` language data goes in a project-local `tessdata/` folder (gitignored) used through `TESSDATA_PREFIX`; the binary path is a config value.
