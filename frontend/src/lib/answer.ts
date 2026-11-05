import type { Answer, Intent, UnverifiedCitation } from "./types.ts";

/** The API can return a raw JSON string if the model's output wasn't parsed. Same handling as the old UI. */
export function normalizeAnswer(data: Answer | string | null | undefined): {
  answer: Answer | null;
  raw: string | null;
} {
  if (!data) return { answer: null, raw: null };
  if (typeof data !== "string") return { answer: data, raw: null };
  try {
    const parsed: unknown = JSON.parse(data.replace(/```json|```/g, "").trim());
    if (parsed && typeof parsed === "object") return { answer: parsed as Answer, raw: null };
  } catch {
    /* falls through: show it as plain text */
  }
  return { answer: null, raw: data };
}

export const INTENT_LABEL: Record<Intent, string> = {
  law_lookup: "আইনের ধারা",
  case_search: "মামলা অনুসন্ধান",
  prediction: "সম্ভাব্য ফলাফল",
  procedure: "প্রক্রিয়া",
  general: "সাধারণ প্রশ্ন",
};

export function citationLabel(c: UnverifiedCitation): string {
  if (c && typeof c === "object") {
    return (
      c.case_no ||
      c.inline_case ||
      (c.inline_section ? `ধারা ${c.inline_section}` : "") ||
      [c.act, c.section].filter(Boolean).join(" — ")
    );
  }
  return String(c);
}

export function hasProcedure(answer: Answer): boolean {
  const p = answer.procedure;
  return Boolean(p && (p.steps?.length || p.court || p.deadline || p.documents?.length || p.cost || p.penalties));
}

/** Plain text of a whole answer, for the copy button. */
export function answerToText(data: Answer | string | null | undefined): string {
  const { answer, raw } = normalizeAnswer(data);
  if (raw) return raw;
  if (!answer) return "";
  const out: string[] = [];
  const section = (title: string, lines: (string | undefined)[]) => {
    const kept = lines.filter((l): l is string => Boolean(l));
    if (kept.length) out.push(`${title}\n${kept.join("\n")}`);
  };
  const summary = answer.short_answer || answer.summary;
  if (summary) out.push(summary);
  section(
    "প্রযোজ্য আইন",
    (answer.applicable_laws ?? []).map((l) =>
      [[l.act, l.section].filter(Boolean).join(" — "), l.what_it_says, l.how_it_applies, l.url]
        .filter(Boolean)
        .join("\n"),
    ),
  );
  section(
    "সংশ্লিষ্ট রায়",
    (answer.relevant_cases ?? []).map((c) =>
      [`${c.case_no ?? ""}${c.year ? ` (${c.year})` : ""}`, c.holding].filter(Boolean).join("\n"),
    ),
  );
  const p = answer.procedure;
  if (p && hasProcedure(answer)) {
    section("প্রক্রিয়া", [
      ...(p.steps ?? []),
      p.court && `আদালত বা কর্তৃপক্ষ: ${p.court}`,
      p.deadline && `সময়সীমা: ${p.deadline}`,
      p.cost && `খরচ: ${p.cost}`,
      p.penalties && `শাস্তি বা পরিণতি: ${p.penalties}`,
      ...(p.documents?.length ? [`প্রয়োজনীয় কাগজপত্র: ${p.documents.join(", ")}`] : []),
    ]);
  }
  section("ঝুঁকি", answer.risks ?? []);
  section("কখন আইনজীবীর কাছে যাবেন", [answer.when_to_see_lawyer]);
  if (answer.disclaimer) out.push(answer.disclaimer);
  return out.join("\n\n");
}
