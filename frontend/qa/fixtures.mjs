// Made-up API responses for screenshots and payload capture. Nothing here comes from a real judgement:
// the parties are invented and the case numbers are placeholders, so this file is safe in a public repo.
// Statute names and section numbers are public law.

export const stats = {
  judgements: {
    total_chunks: 45571,
    estimated_cases: 8615,
    coverage: "Bangladesh Supreme Court — High Court Division",
    year_range: "1950–2025",
  },
  laws: { total_chunks: 22400, total_acts: 1399, source: "bdlaws.minlaw.gov.bd" },
  features: {},
};

const answer = {
  short_answer:
    "চেক ডিজঅনার হলে প্রথমে ৩০ দিনের মধ্যে লিখিত নোটিশ দিন। নোটিশ পাওয়ার ৩০ দিনের মধ্যে অর্থ পরিশোধ না হলে পরের এক মাসের মধ্যে আদালতে অভিযোগ করতে পারবেন।",
  summary:
    "চেক ডিজঅনার হলে প্রথমে ৩০ দিনের মধ্যে লিখিত নোটিশ দিন। নোটিশ পাওয়ার ৩০ দিনের মধ্যে অর্থ পরিশোধ না হলে পরের এক মাসের মধ্যে আদালতে অভিযোগ করতে পারবেন।",
  applicable_laws: [
    {
      act: "The Negotiable Instruments Act, 1881",
      section: "Section 138 — Dishonour of cheque for insufficiency, etc. of funds in the account",
      what_it_says: "পর্যাপ্ত অর্থ না থাকায় চেক ফেরত এলে তা অপরাধ হিসেবে গণ্য হতে পারে।",
      how_it_applies: "নমুনা: আপনার চেক ফেরত আসার তারিখ থেকে নোটিশের সময় গোনা শুরু হবে।",
      url: "http://bdlaws.minlaw.gov.bd/act-details-46.html",
    },
    {
      act: "The Negotiable Instruments Act, 1881",
      section: "Section 141 — Cognizance of offences",
      what_it_says: "লিখিত অভিযোগের ভিত্তিতে আদালত এই অপরাধ আমলে নেয়, এবং অভিযোগ করার সময়সীমা নির্দিষ্ট।",
      how_it_applies: "",
      url: "http://bdlaws.minlaw.gov.bd/act-details-46.html",
    },
  ],
  relevant_cases: [
    {
      case_no: "Criminal Revision No. 0000 of 2020",
      year: 2020,
      division: "HCD",
      holding: "নমুনা রায়: নোটিশ যথাসময়ে না দেওয়ায় অভিযোগটি গ্রহণযোগ্য হয়নি।",
    },
    {
      case_no: "Criminal Appeal No. 0000 of 2018",
      year: 2018,
      division: "HCD",
      holding: "নমুনা রায়: চেকের বৈধ পাওনা প্রমাণের দায় নিয়ে আলোচনা।",
    },
  ],
  procedure: {
    steps: [
      "১. ব্যাংকের ফেরতের স্লিপ সংগ্রহ করুন।",
      "২. ৩০ দিনের মধ্যে লিখিত নোটিশ পাঠান।",
      "৩. সময়মতো আদালতে অভিযোগ দায়ের করুন।",
    ],
    court: "যুগ্ম দায়রা জজ আদালত",
    deadline: "নোটিশের সময়সীমা শেষ হওয়ার পর এক মাস",
    cost: "আদালত ফি ও আইনজীবীর খরচ মামলাভেদে আলাদা",
    penalties: "এক বছর পর্যন্ত কারাদণ্ড বা চেকের তিনগুণ পর্যন্ত জরিমানা, অথবা উভয়",
    documents: ["মূল চেক", "ব্যাংকের ফেরতের স্লিপ", "নোটিশের কপি ও ডাকের রসিদ"],
  },
  risks: ["সময়সীমা পার হলে অভিযোগ গ্রহণযোগ্য নাও হতে পারে।", "নোটিশের প্রমাণ না থাকলে মামলা দুর্বল হয়ে পড়ে।"],
  when_to_see_lawyer: "নোটিশ পাঠানোর আগেই একজন আইনজীবীর সঙ্গে কথা বলুন, যাতে সময়সীমা ও ভাষা ঠিক থাকে।",
  follow_up_options: [
    { question: "চেকটি কবে ফেরত এসেছে?", options: ["৩০ দিনের মধ্যে", "৩০ দিনের বেশি আগে"] },
    { question: "আপনি কি নোটিশ পাঠিয়েছেন?", options: ["হ্যাঁ", "না"] },
  ],
  follow_up_questions: [],
  disclaimer: "এটি সাধারণ তথ্য, আইনি পরামর্শ নয়। আপনার বিষয়টি নিয়ে একজন আইনজীবীর সঙ্গে কথা বলুন।",
};

export const chat = {
  answer,
  sources: { cases: [], laws: [] },
  intent: "procedure",
  outcome_stats: {},
  contradictions_detected: 1,
  citation_verified: true,
  unverified_citations: [{ act: "The Sample Act, 1900", section: "Section 1" }],
  retrieval_sufficient: false,
  retrieval_attempts: 1,
  answer_attempts: 1,
  laws_retrieved: 5,
  cases_retrieved: 4,
};

export const predict = {
  ...chat,
  intent: "prediction",
  answer,
  prediction: answer,
  outcome_stats: {
    total_similar_cases: 4,
    petitioner_won: 1,
    petitioner_lost: 2,
    disposed_settled: 1,
    win_rate_pct: 25,
    year_range: "2018–2024",
    common_subjects: ["tenancy", "contract"],
  },
  similar_cases_used: 4,
  laws_referenced: 5,
};

const preview =
  "This is placeholder text standing in for a judgement preview. The petitioner relied on section 138 of the Negotiable Instruments Act, 1881 and argued that the notice was served in time. The respondent disputed the date of service and the learned Court below considered the evidence on record before passing the impugned order, which is now challenged in this revision.";

export const search = {
  cases: [
    {
      case_no: "Criminal Revision No. 0000 of 2023",
      year: 2023,
      type: "criminal_revision",
      outcome: "rule_absolute",
      division: "HCD",
      subjects: "contract,cheque_dishonour",
      judges: null,
      preview,
      score: 0.0392,
    },
    {
      case_no: "Criminal Misc. Case No. 0000 of 2016",
      year: 2016,
      type: "criminal_misc",
      outcome: "disposed",
      division: "HCD",
      subjects: "cheque_dishonour",
      judges: null,
      preview,
      score: 0.0312,
    },
    {
      case_no: "Criminal Revision No. 0001 of 2018",
      year: 2018,
      type: "criminal_revision",
      outcome: "rule_discharged",
      division: "HCD",
      subjects: "contract,tenancy",
      judges: null,
      preview,
      score: 0.0241,
    },
    {
      case_no:
        "AVeryLongFileNameWithoutAnySpacesToTestWrapping_Criminal_Appeal_No_0000_of_2015_Dismissed_dt_01_01_2020.pdf",
      year: 0,
      type: "criminal_appeal",
      outcome: "unknown",
      division: null,
      subjects: null,
      judges: null,
      preview,
      score: 0.0187,
    },
  ],
  laws: [
    {
      title: "The Negotiable Instruments Act, 1881",
      section: "Section 138 — Dishonour of cheque for insufficiency, etc. of funds in the account",
      url: "http://bdlaws.minlaw.gov.bd/act-details-46.html",
      year: 1881,
      type: "Act",
      preview:
        "Where any cheque drawn by a person on an account maintained by him is returned by the bank unpaid, placeholder text continues here to show how a law result looks.",
      score: 0.0325,
    },
    {
      title: "The Negotiable Instruments Act, 1881",
      section: "Section 141 — Cognizance of offences",
      url: "http://bdlaws.minlaw.gov.bd/act-details-46.html",
      year: 1881,
      type: "Act",
      preview:
        "No Court shall take cognizance of any offence punishable under section 138 except upon a complaint, placeholder text continues here.",
      score: 0.0288,
    },
  ],
};
search.judgements = search.cases;

export const doc = {
  document: `**ভাড়া চুক্তি (Rent Agreement)**

---

### ১. পক্ষগণ / Parties

**মালিক (Landlord):** নমুনা ব্যক্তি ক, ঢাকা
**ভাড়াটিয়া (Tenant):** নমুনা ব্যক্তি খ, ঢাকা

### ২. ভাড়া ও মেয়াদ / Rent and Term

- **মাসিক ভাড়া (Monthly Rent):** ১০,০০০ টাকা
- **মেয়াদ (Term):** ১২ মাস
- প্রতি মাসের ১০ তারিখের মধ্যে ভাড়া পরিশোধ করতে হবে।

### ৩. শর্তাবলী / Terms

1. ভাড়াটিয়া সম্পত্তিটি শুধু বসবাসের জন্য ব্যবহার করবেন।
2. মালিকের লিখিত অনুমতি ছাড়া সাবলেট করা যাবে না।

### স্বাক্ষর / Signatures

মালিক: ______________   ভাড়াটিয়া: ______________

সাক্ষী ১: ______________   সাক্ষী ২: ______________`,
  doc_type: "Rent Agreement (ভাড়া চুক্তি)",
  laws_referenced: [
    {
      title: "The Premises Rent Control Act, 1991",
      section: "Section 18 — Recovery of possession",
      url: "http://bdlaws.minlaw.gov.bd/act-details-1.html",
    },
    {
      title: "The Transfer of Property Act, 1882",
      section: "Section 105 — Lease defined",
      url: "http://bdlaws.minlaw.gov.bd/act-details-2.html",
    },
  ],
};

// Installs stub responses on a Playwright page. Each request body is recorded.
export async function stubApi(page, { record, delayMs = 0, fail = null } = {}) {
  const responses = {
    "/api/stats": stats,
    "/api/chat": chat,
    "/api/predict": predict,
    "/api/search": search,
    "/api/generate-doc": doc,
  };
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (req.method() === "POST" && record) record.push({ path, body: JSON.parse(req.postData() || "null") });
    if (path !== "/api/stats" && delayMs) await new Promise((r) => setTimeout(r, delayMs));
    if (fail && path !== "/api/stats") {
      await route.fulfill({
        status: fail,
        contentType: "application/json",
        body: JSON.stringify({ detail: "LLM service temporarily unavailable — please try again in a moment" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify(responses[path] ?? {}),
    });
  });
}
