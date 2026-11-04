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
6. *Unicode Bangla exists.* Some files have real Unicode Bangla (e.g. `2358026_C.R.326of2023`, Bangla share 32%) and extract fine. **Corrected by D-113:** this file's text layer is in fact corrupted. It looked fine because the letters are in the Bangla block.

**D-103 — OCR trigger is "page fails the quality check", not only "page has no text".** SPEC Phase 1.4 says OCR only for pages with no extractable text. Legacy-font pages *do* have extractable text — it's mojibake — so that rule would leave all ~2,800 legacy-font files broken. The cascade OCRs a page when its text is empty **or** the page fails the same legacy-font/garbage checks as the audit.

**D-116 — What v2 is, and what's left open (2026-11-04).** `bangladesh_judgements_v2` = `text_v2/` + labels from `clean_metadata.py`. Those labels were derived from the v1 text; they weren't regenerated. One document (`2395499_706of2023`, 2 chunks) is wrongly tagged AD because two-column text put "Appellate Division" in the first 400 characters. Neither is corrected here. v2 was built before D-114 was found, so its OCR'd documents carry the line loss. The API still uses v1.

**D-115 — Death Ref 58 now swamps Bangla vector search (update to D-110).** In v1 this case's Bangla was mojibake, so Bangla queries rarely matched it. Now it's readable, and it's still 21% of chunks. On 10 test queries it went from 5 to 35 of the 100 top-10 results, and it filled all 10 slots for unrelated Bangla questions such as cheque dishonour. English queries were unaffected. Because fusion collapses chunks by file, a vector list full of one case also reduces candidates to one document. A per-document cap is needed before the API moves to v2; to be measured in Phase 3.

**D-114 — Tesseract silently drops Bangla lines; two flags fix it.** A page read by hand next to its output had lost 6 of 18 lines with no error, and every automated check had passed. Layout mode, DPI, language, script model and preprocessing made no difference. `-c textord_noise_rejrows=0 -c textord_noise_rejwords=0` did: 154 → 179 of 179 lines on 8 pages at essentially the same speed, word recall 64.9% → 95.4% on the hand-transcribed page, and no added junk on a 40-page sample. `tessdata_best` also keeps the lines but runs 2.4× slower.
Loss is concentrated in Death Ref 58 (part 2 keeps 83.0% of lines, part 3 85.0%, part 1 98.4%; about 54,000 of 317,000 lines missing in part 2). Other full-page OCR keeps 95.0% by the line-count metric, which moved only to 95.1% with the fix on a sample, so most of that shortfall is probably metric noise. Block OCR was unaffected. The flags are in `pdf_extract.py` from now on. **Existing outputs and v2 weren't regenerated**: that re-run is the owner's decision.
Lesson: mojibake and completeness are different properties. `ingestion/text_checks.py lines` now compares output to the PDF's line structure.

**D-113 — A third kind of broken Bangla.** Some text layers look like Unicode but hold visual-order vowels and Latin/IPA/Cyrillic letters in place of Bangla characters (`ǯদওয়ানী আদালেত ɛিতকার`). The legacy-character check can't see them. Found because "ground truth" from such files gave an impossible 27% OCR word recall. `text_checks.py bangla` flags a file when over 5% of its Bangla tokens contain foreign-script letters; the split is clean (3,058 files under 1%, 50 above 20%): **62 files, 0.43% of Bangla tokens**, median 9% Bangla, so mostly English judgements with Bangla quotations. Not re-extracted: `_is_legacy_span` doesn't yet treat these as unreadable. They rate "good" in the audit (61 of 62), and the audit buckets weren't changed, so v1/v2 stay comparable.

**D-112 — The v1 integrity check raised a false alarm; it now compares exact vectors.** After the v2 build, `verify-chroma` failed on `neighbours` for v1 and the pipeline stopped. Investigation: index files byte-identical to the pre-build backup; the same probe on the same unchanged DB gave 2 mismatches in about 10 runs; 0 of 45,571 raw float32 vectors and 0 documents differed from a copy of the backup (laws: 0 of 22,400). Approximate search isn't reliably repeatable, so it can't prove anything. Why it flips isn't established; replaying 572 WAL rows into the in-memory index on open is the likely reason. The check now hashes every vector by id, against a baseline taken from a copy of the backup (`chroma_pre_v2_exact.json`), so opening the backup itself was never needed. Tests show one changed vector, one changed document or one deleted id fails it. The pipeline stopping was the right behaviour; the check was wrong.

**D-107 — The `BrokenProcessPool` of 2026-11-04 04:39:37: what the evidence shows.** The full run stopped with 5,466 of 5,724 files done in its last pass. Evidence, from the Windows event logs and `phase1_run.log`:
- no Application Error, WER report or crash dump for `python.exe`/`tesseract.exe` in that window, so no native crash;
- no Resource-Exhaustion-Detector event in 30 days, and a worker peaks at ~106 MB, so memory exhaustion is unlikely (not strictly excluded: the pagefile's peak counter resets on reboot);
- `run_phase1.py` should have logged a line right after the failed pass and didn't, so it died at the same moment as the workers: a whole-tree termination, not one worker crashing;
- keyboard activity at 04:37:35, power source changes at 04:37:29 and 04:38:25, a user-mode `SetSuspendState` call at 04:39:40, three seconds after the traceback, then hibernation until 10:31.

Most likely the process tree was killed when the session closed, just before the laptop was put to sleep. What exactly closed it can't be proven from these logs. It doesn't change the fix: whatever the trigger, the code should have survived it.

**D-108 — Recovery design.** Defects found in the extraction code, and their fixes:
- one dead worker ended the whole pass → workers run in short-lived batches; unfinished units are retried alone; a unit that kills its worker twice alone goes to `failures.jsonl` and the run continues;
- no checkpoint inside a file → PDFs over 200 pages save every 50 pages to `text_v2_parts/`, atomically;
- non-atomic `.txt` writes and existence-only resume → temp + fsync + rename; an output counts only with a manifest row and a matching character count; anything else is moved to `data_quality/quarantine/`, never deleted;
- buffered manifest → each row is flushed and fsynced;
- no OCR timeout, PDFs never closed → 300 s per OCR call, 1 h stall detection, `doc.close()`;
- **`run_phase1.py` ignored return codes, so a failed extraction would have gone straight on to the audit and a v2 build from incomplete text** → a gate requires a validated output for all 8,615 PDFs. It reads the disk, not a progress counter.

`--force` was removed from `pdf_extract.py` (it overwrote outputs), and `enrich_judgements.py` now refuses to replace an existing collection without `--allow-replace`; its default target is v1. `ingestion/integrity.py` records SHA-256 of outputs and a logical fingerprint of the v1/laws collections (ids, metadata, documents, exact vectors; see D-112). HNSW file hashes are deliberately not compared, because Chroma can flush its WAL into them on open without any content change.

**D-109 — 2 workers.** RAM wasn't the cause of the crash, but only ~1 GB is typically available on this 7.7 GB laptop, and a worker plus its Tesseract process needs a few hundred MB. Below 1 GB available a batch runs with 1 worker; below 400 MB it waits first (`low_ram_mb`, `critical_ram_mb`). Measured on an idle machine: full-page OCR 1.33 s/page, against 3.78 s/page under the contention of the first run.

**D-110 — Death Reference 58/2013 dominates the corpus.** This judgement is split across PDFs of 11,407, 16,560 and 1,100 pages. Parts 1 and 2 alone were 9,996 of 45,571 v1 chunks (22%) and 24% of all words. They're extracted and indexed like everything else here, because dropping or capping them is a retrieval decision that needs numbers. To be measured in Phase 3: without a per-case cap, one case can fill the top-k.

**D-111 — The 8,357 existing outputs were kept as they are.** Two known small inconsistencies, not worth overwriting verified outputs for: 9 files have extra `\f` characters from the PDF text layer, so splitting them on `\f` gives more parts than pages (manifest page counts match the PDFs for all 8,357; new extractions strip these codes); and 7 of the original test sample predate the D-105 control-code rule (their audit shows no control-code problem).

**D-105 — Unreadable spans include fonts embedded without a character map.** Two sample files had Bangla in `Vrinda` (a Unicode font) embedded without ToUnicode, so the text layer returns glyph ids (`\x01\x02\x03`). A span counts as unreadable — and is OCR'd — if its font is a known legacy font, if > 30% of it is control codes, or if > 10% of its letters are legacy-font mojibake characters.

**D-106 — OCR settings.** 200 dpi, `ben+eng`, Tesseract `tessdata` (standard) models. 200 dpi was faster than 300 (3.7 s vs 6.3 s per page) and produced less junk on the probe page. Known issue: `ben+eng` inserts Latin junk into Bangla text; to be tested: `ben`-only OCR of the unreadable spans. See D-114 for the line-loss flags.

**D-104 — Tesseract.** Tesseract 5.5 is installed at `C:\Program Files\Tesseract-OCR` with `eng` only and is not on PATH. `ben` language data goes in a project-local `tessdata/` folder (gitignored) used through `TESSDATA_PREFIX`; the binary path is a config value.
