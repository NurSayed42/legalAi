"""
legal_rules.py
==============
api_v3.py এর deterministic নিয়মগুলো — model বা DB ছাড়া, যাতে tests/test_rules.py আলাদা করে
test করা যায়:

  - LEGAL_ALIASES      : বাংলা/Banglish/English আইনি শব্দ → নির্দিষ্ট Act + section (query expansion)
  - ACT_ALIASES        : প্রশ্নে Act এর নাম চেনা ('CrPC', 'দণ্ডবিধি') → act_id
  - section request    : 'Section 302', 'ধারা ৪২০', 'Article 102'
  - intent             : procedure / prediction keyword, law_lookup শুধু Act/ধারা নাম থাকলে
  - persona check      : মানুষের অভিজ্ঞতা দাবি ('আমার ৩০ বছরের অভিজ্ঞতায়', 'in my 20 years')
  - answer schema      : সব intent এ, UI বা সরাসরি call — একই structure
"""

import re

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# act_id = bdlaws এর act-details-{id}.html — laws collection এর metadata তে এই id ই থাকে
PENAL_CODE, CRPC, CPC, EVIDENCE_ACT, NI_ACT, CONSTITUTION = 11, 75, 86, 24, 46, 367
MFLO, SPECIFIC_RELIEF, LIMITATION_ACT, CONTRACT_ACT = 305, 36, 88, 26
FAMILY_COURTS, TRANSFER_OF_PROPERTY, REGISTRATION_ACT = 682, 48, 90

# "Section N" দিলে কিন্তু Act এর নাম না দিলে এই ক্রমে খোঁজা হয়
PRIORITY_ACTS = [PENAL_CODE, CRPC, CPC, EVIDENCE_ACT, NI_ACT, CONSTITUTION]

ACT_ALIASES = {
    # বাংলা প্রতিবর্ণীকরণও ('পেনাল কোড') — model মাঝে মাঝে Act এর নাম বাংলায় লেখে
    PENAL_CODE:           [r"penal\s*code", r"দণ্ডবিধি", r"দন্ডবিধি", r"পেনাল\s*কোড"],
    CRPC:                 [r"\bcr\.?\s*p\.?\s*c\b", r"code\s*of\s*criminal\s*procedure", r"ফৌজদারি?\s*কার্যবিধি"],
    CPC:                  [r"\bc\.?\s*p\.?\s*c\b", r"code\s*of\s*civil\s*procedure", r"দেওয়ানি?\s*কার্যবিধি"],
    EVIDENCE_ACT:         [r"evidence\s*act", r"সাক্ষ্য\s*আইন", r"এভিডেন্স\s*(?:অ্যা|এ)ক্ট"],
    NI_ACT:               [r"\bn\.?\s*i\.?\s*act\b", r"negotiable\s*instruments?", r"হস্তান্তরযোগ্য\s*দলিল",
                           r"নেগোশিয়েবল", r"এন\.?\s*আই\.?\s*(?:অ্যা|এ)ক্ট"],
    CONSTITUTION:         [r"constitution", r"সংবিধান"],
    MFLO:                 [r"\bmflo\b", r"muslim\s*family\s*laws?", r"মুসলিম\s*পারিবারিক\s*আইন"],
    SPECIFIC_RELIEF:      [r"specific\s*relief", r"সুনির্দিষ্ট\s*প্রতিকার"],
    LIMITATION_ACT:       [r"limitation\s*act", r"তামাদি\s*আইন"],
    CONTRACT_ACT:         [r"contract\s*act", r"চুক্তি\s*আইন"],
    FAMILY_COURTS:        [r"family\s*courts?\s*ordinance", r"পারিবারিক\s*আদালত\s*অধ্যাদেশ"],
    TRANSFER_OF_PROPERTY: [r"transfer\s*of\s*property", r"সম্পত্তি\s*হস্তান্তর\s*আইন"],
    REGISTRATION_ACT:     [r"registration\s*act", r"রেজিস্ট্রেশন\s*আইন", r"নিবন্ধন\s*আইন"],
}
_ACT_RES = {act_id: [re.compile(p, re.IGNORECASE) for p in pats] for act_id, pats in ACT_ALIASES.items()}


def detect_acts(text: str) -> list[int]:
    """প্রশ্নে যে Act গুলোর নাম আছে, প্রশ্নে যে ক্রমে এসেছে।"""
    found = []
    for act_id, regexes in _ACT_RES.items():
        positions = [m.start() for rx in regexes for m in [rx.search(text)] if m]
        if positions:
            found.append((min(positions), act_id))
    return [act_id for _, act_id in sorted(found)]


# ─── QUERY EXPANSION: আইনি alias ─────────────────────────────

LEGAL_ALIASES = [
    {
        "name": "cheque_dishonour",
        "patterns": [r"che(?:que|ck|k)\s*(?:dis)?hono", r"\bchek\b", r"\bcheque", r"চেক", r"ডিসঅনার", r"dishono"],
        "expansion": "dishonour of cheque Negotiable Instruments Act 1881 section 138 legal notice and complaint",
        "sections": [(NI_ACT, "138"), (NI_ACT, "138A"), (NI_ACT, "140"), (NI_ACT, "141")],
    },
    {
        "name": "bail",
        "patterns": [r"\bjamin\b", r"জামিন", r"\bbail"],
        "expansion": "bail in non-bailable offence Code of Criminal Procedure 1898 section 497 and 498",
        "sections": [(CRPC, "497"), (CRPC, "498")],
    },
    {
        "name": "talaq",
        "patterns": [r"\btala[qk]", r"তালাক", r"\bdivorce", r"ডিভোর্স", r"বিবাহ\s*বিচ্ছেদ"],
        "expansion": "talaq divorce notice to Chairman arbitration council Muslim Family Laws Ordinance 1961 section 7",
        "sections": [(MFLO, "7")],
    },
    {
        "name": "land_dispossession",
        "patterns": [r"jomi\s*dokhol", r"\bdokhol", r"জমি.{0,20}দখল", r"দখল.{0,20}জমি", r"বেদখল", r"dispossess"],
        "expansion": ("recovery of possession of immovable property suit by person dispossessed "
                      "Specific Relief Act 1877 section 8 and 9, land dispute breach of peace "
                      "Code of Criminal Procedure section 145"),
        "sections": [(SPECIFIC_RELIEF, "8"), (SPECIFIC_RELIEF, "9"), (CRPC, "145")],
    },
    {
        "name": "writ",
        "patterns": [r"\bwrit", r"রিট"],
        "expansion": "writ petition High Court Division Constitution of Bangladesh article 102",
        "sections": [(CONSTITUTION, "102")],
    },
    {
        "name": "murder",
        "patterns": [r"\bmurder", r"খুন", r"হত্যা", r"\bkhun\b"],
        "expansion": "murder punishable with death or imprisonment for life Penal Code 1860 section 302",
        "sections": [(PENAL_CODE, "302")],
    },
]
for _alias in LEGAL_ALIASES:
    _alias["_res"] = [re.compile(p, re.IGNORECASE) for p in _alias["patterns"]]


def expand_query(text: str) -> list[dict]:
    """প্রশ্নে কোন আইনি topic আছে — প্রতিটার expansion text আর pin করার section গুলো।"""
    return [
        {"name": a["name"], "expansion": a["expansion"], "sections": list(a["sections"])}
        for a in LEGAL_ALIASES if any(rx.search(text) for rx in a["_res"])
    ]


# ─── SECTION REQUEST ─────────────────────────────────────────

_SECTION_REQUEST_RE = re.compile(
    r"(?:\b(section|sec\.?|s\.|article|art\.?)\s*|(ধারা|অনুচ্ছেদ)\s*)([0-9০-৯]+[A-Za-z]?)"
    r"|([0-9০-৯]+[A-Za-z]?)\s*(?:নং|নম্বর)?\s*(ধারা|অনুচ্ছেদ)",
    re.IGNORECASE,
)
_ARTICLE_WORDS = ("article", "art", "art.", "অনুচ্ছেদ")


def _normalize_section_no(raw: str) -> str:
    no = raw.translate(BN_DIGITS)
    return no[:-1] + no[-1].upper() if no[-1].isalpha() else no


def detect_section_request(text: str) -> dict | None:
    """'Section 302' / 'ধারা ৪২০' / '৪২০ ধারা' / 'Article 102' → {section_no, act_ids, is_article}।
    Article হলে আর অন্য Act এর নাম না থাকলে সংবিধান ধরা হয়।"""
    m = _SECTION_REQUEST_RE.search(text)
    if not m:
        return None
    word = (m.group(1) or m.group(2) or m.group(5) or "").lower()
    number = m.group(3) or m.group(4)
    is_article = word in _ARTICLE_WORDS
    act_ids = detect_acts(text)
    if is_article and not act_ids:
        act_ids = [CONSTITUTION]
    return {"section_no": _normalize_section_no(number), "act_ids": act_ids, "is_article": is_article}


def section_label(meta: dict) -> str:
    """'Section 302 — Punishment for murder' (সংবিধানে 'Article 102 — …')। scraper এর section
    field এ শুধু heading থাকে, number থাকে section_no তে।"""
    heading = meta.get("section", "")
    section_no = meta.get("section_no", "")
    if not section_no:
        return heading
    word = "Article" if meta.get("act_id") == CONSTITUTION else "Section"
    return f"{word} {section_no} — {heading}"


# ─── INTENT ──────────────────────────────────────────────────

INTENTS = ("law_lookup", "case_search", "prediction", "procedure", "general")

PROCEDURE_RE = re.compile(
    r"(?<!criminal )(?<!civil )\bprocedure\b|\bprocess\b|\bsteps?\b|how (?:do|can|should) i\b|how to\b|"
    r"what (?:can|should) i do|প্রক্রিয়া|পদ্ধতি|কীভাবে|কিভাবে|পদক্ষেপ|মামলা করতে|মামলা করার|"
    r"কী করব|কি করব|মামলা করব|\bki korbo\b|\bmamla korbo\b|\bkivabe\b|\bkibhabe\b",
    re.IGNORECASE,
)
PREDICTION_RE = re.compile(
    r"জিতব|হারব|জেতার|সম্ভাবনা|\bchance|will i win|win the case|predict|\boutcome\b|\bjitbo\b|\bharbo\b",
    re.IGNORECASE,
)
CASE_SEARCH_RE = re.compile(r"\b(?:find|show|list)\b.{0,40}\b(?:cases?|judg(?:e)?ments?|precedents?)\b|নজির|রায় খুঁজ",
                            re.IGNORECASE)


def resolve_intent(question: str, llm_intent: str | None, forced: str | None = None) -> str:
    """Keyword নিয়মগুলো LLM router এর আগে — router প্রায় সব প্রশ্নকে law_lookup বলত, আর
    law_lookup এ judgements খোঁজা হয় না। তাই law_lookup শুধু তখন, যখন প্রশ্নে কোনো Act বা
    ধারার নাম আছে।"""
    if forced:
        return forced
    if PREDICTION_RE.search(question):
        return "prediction"
    if PROCEDURE_RE.search(question):
        return "procedure"
    if CASE_SEARCH_RE.search(question):
        return "case_search"
    if detect_section_request(question):
        return "law_lookup"
    if llm_intent == "law_lookup":
        return "law_lookup" if detect_acts(question) else "general"
    return llm_intent if llm_intent in INTENTS else "general"


# ─── PERSONA CLAIMS ──────────────────────────────────────────

PERSONA_CLAIM_RE = re.compile(
    "|".join([
        r"আমার\s*(?:দীর্ঘ\s*)?(?:[০-৯\d]+\s*)?বছরের\s*(?:অভিজ্ঞতা|প্র্যা?কটিস|প্র্যাক্টিস|আইন\s*পেশা|কর্মজীবন|ক্যারিয়ার)",
        r"(?:অভিজ্ঞতায়|প্র্যা?কটিসে|প্র্যাক্টিসে|কর্মজীবনে|ক্যারিয়ারে)\s*(?:আমি\s*)?দেখেছি",
        r"আমি[^।.!?\n]{0,60}(?:মামলা|কেস)[^।.!?\n]{0,40}(?:পরিচালনা|লড়েছি|লড়েছি|জিতেছি|পরিচালিত)",
        r"(?:আইনজীবী|অ্যাডভোকেট|এডভোকেট|ব্যারিস্টার)\s*হিসেবে\s*(?:আমি|আমার)",
        r"আমি[^।.!?\n]{0,40}বার\s*কাউন্সিল",
        r"\bin my (?:\d+|many|long|several|years of)\b[^.!?\n]{0,30}\b(?:years?|practice|career)\b",
        r"\bmy (?:own )?(?:legal )?(?:experience|practice|career) (?:as|at|in|of)\b",
        r"\bI (?:have|'ve) (?:personally )?(?:handled|argued|represented|litigated|practi[sc]ed|appeared|won)\b",
        r"\bI am an? (?:senior |experienced |practi[sc]ing )?(?:lawyer|advocate|barrister|attorney)\b",
        r"\bas an? (?:senior |experienced |practi[sc]ing )?(?:lawyer|advocate|barrister|attorney),? I\b",
        r"\bI am a member of the (?:Bar|Bar Council)\b",
    ]),
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[।.!?])\s+|\n+")


def _iter_strings(value, path=""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _iter_strings(v, f"{path}[{i}]")


def find_persona_claims(answer) -> list[str]:
    return [m.group(0) for _, text in _iter_strings(answer) for m in PERSONA_CLAIM_RE.finditer(text)]


def strip_persona_sentences(answer):
    """Regenerate এর পরেও থেকে গেলে শেষ উপায় — যে বাক্যে দাবিটা আছে সেটা বাদ।"""
    if isinstance(answer, str):
        if not PERSONA_CLAIM_RE.search(answer):
            return answer
        return " ".join(s for s in _SENTENCE_SPLIT_RE.split(answer) if s and not PERSONA_CLAIM_RE.search(s))
    if isinstance(answer, dict):
        return {k: strip_persona_sentences(v) for k, v in answer.items()}
    if isinstance(answer, list):
        return [strip_persona_sentences(v) for v in answer]
    return answer


# ─── INLINE CITATIONS (answer এর text এর ভেতরে) ──────────────

INLINE_SECTION_RE = re.compile(
    r"(?:\b(?:sections?|secs?\.|ss?\.|articles?|arts?\.)\s*|(?:ধারা|অনুচ্ছেদ)(?:র|য়)?\s*)([0-9০-৯]+[A-Za-z]?)"
    r"|([0-9০-৯]+[A-Za-z]?)\s*(?:নং|নম্বর)?\s*(?:ধারা|অনুচ্ছেদ)",
    re.IGNORECASE,
)
INLINE_CASE_RE = re.compile(
    r"(?:writ\s*petition|petition|appeal|revision|reference|rule|case|মামলা|রিট|আপিল|রিভিশন)\s*"
    r"(?:no\.?|নং)?\s*([0-9০-৯]+)\s*(?:of|/)\s*([0-9০-৯]{2,4})\b",
    re.IGNORECASE,
)
# structured citation গুলো আলাদা করে verify হয়; এখানে শুধু free text
_INLINE_SKIP = re.compile(r"^applicable_laws\[\d+\]\.(?:act|section|url)$|^relevant_cases\[\d+\]\.(?:case_no|division)$"
                          r"|^follow_up_options|^disclaimer$")


def section_numbers_in(text: str) -> set[str]:
    """Source text এর ভেতরে উল্লেখ থাকা ধারা নম্বর — judgement 'section 302' নিয়ে আলোচনা করলে
    উত্তরে সেটা বলা context-এর বাইরের citation না।"""
    return {_normalize_section_no(m.group(1) or m.group(2)) for m in INLINE_SECTION_RE.finditer(text)}


def inline_section_mentions(answer: dict) -> list[tuple[str, str]]:
    out = []
    for path, text in _iter_strings(answer):
        if _INLINE_SKIP.search(path):
            continue
        for m in INLINE_SECTION_RE.finditer(text):
            out.append((path, _normalize_section_no(m.group(1) or m.group(2))))
    return out


def inline_case_refs(answer: dict) -> list[tuple[str, str, str]]:
    out = []
    for path, text in _iter_strings(answer):
        if _INLINE_SKIP.search(path):
            continue
        for m in INLINE_CASE_RE.finditer(text):
            out.append((path, m.group(1).translate(BN_DIGITS), m.group(2).translate(BN_DIGITS)))
    return out


# ─── ANSWER SCHEMA ───────────────────────────────────────────

DISCLAIMER = "এটা legal research assistance, আইনি পরামর্শ নয়। সিদ্ধান্ত নেওয়ার আগে একজন আইনজীবীর পরামর্শ নিন।"
PROCEDURE_REQUIRED = ("steps", "court", "deadline", "documents", "penalties")
_NUMBERED_RE = re.compile(r"^\s*[0-9০-৯]+\s*[.)।]")


def _str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(_str(v) for v in value if v)
    return str(value).strip()


def _str_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [s for s in (_str(v) for v in value) if s]


def validate_follow_up_options(raw) -> list:
    """LLM output validate করে — malformed/missing হলে চুপচাপ বাদ দেয়, crash করে না।"""
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


_SOURCE_LABEL_RE = re.compile(r"^\s*(?:Act|Section|Case)\s*:\s*", re.IGNORECASE)


def _unlabel(value) -> str:
    """Model মাঝে মাঝে context এর label সহ copy করে ('Act: The Penal Code, 1860')।"""
    return _SOURCE_LABEL_RE.sub("", _str(value))


def normalize_answer(raw) -> dict:
    """যেকোনো LLM output → সবসময় একই key, একই type। UI থেকে আসুক বা সরাসরি API call,
    answer এর structure একই থাকে; missing field খালি string/list হয়, key বাদ পড়ে না।"""
    a = raw if isinstance(raw, dict) else {}
    proc = a.get("procedure") if isinstance(a.get("procedure"), dict) else {}
    steps = _str_list(proc.get("steps"))
    steps = [s if _NUMBERED_RE.match(s) else f"{i}. {s}" for i, s in enumerate(steps, 1)]
    laws = a.get("applicable_laws") if isinstance(a.get("applicable_laws"), list) else []
    cases = a.get("relevant_cases") if isinstance(a.get("relevant_cases"), list) else []
    return {
        "short_answer": _str(a.get("short_answer") or a.get("summary")),
        "applicable_laws": [
            {"act": _unlabel(l.get("act")), "section": _unlabel(l.get("section")),
             "what_it_says": _str(l.get("what_it_says")), "how_it_applies": _str(l.get("how_it_applies")),
             "url": _str(l.get("url"))}
            for l in laws if isinstance(l, dict)
        ],
        "relevant_cases": [
            {"case_no": _unlabel(c.get("case_no")), "year": c.get("year"), "division": _str(c.get("division")),
             "holding": _str(c.get("holding") or c.get("why_relevant"))}
            for c in cases if isinstance(c, dict)
        ],
        "procedure": {
            "steps": steps, "court": _str(proc.get("court")), "deadline": _str(proc.get("deadline")),
            "documents": _str_list(proc.get("documents")), "cost": _str(proc.get("cost")),
            "penalties": _str(proc.get("penalties")),
        },
        "risks": _str_list(a.get("risks")),
        "when_to_see_lawyer": _str(a.get("when_to_see_lawyer")),
        "follow_up_options": validate_follow_up_options(a.get("follow_up_options")),
        "disclaimer": DISCLAIMER,
    }


def procedure_gaps(answer: dict) -> list[str]:
    proc = answer.get("procedure") or {}
    return [k for k in PROCEDURE_REQUIRED if not proc.get(k)]


def is_only_question(answer: dict) -> bool:
    """উত্তর বলতে শুধু একটা প্রশ্ন — কোনো আইন, ধাপ বা ঝুঁকি নেই।"""
    short = answer.get("short_answer", "").strip()
    has_substance = answer.get("applicable_laws") or (answer.get("procedure") or {}).get("steps") or answer.get("risks")
    return not has_substance and (not short or short.endswith(("?", "？")))
