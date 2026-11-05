/* What the user reads, mapped to the exact values the API expects. The values must not change. */

export const CASE_TYPES = [
  { value: "civil_revision", label: "সিভিল রিভিশন" },
  { value: "writ_petition", label: "রিট পিটিশন" },
  { value: "criminal_appeal", label: "ফৌজদারি আপিল" },
  { value: "criminal_revision", label: "ফৌজদারি রিভিশন" },
  { value: "first_appeal", label: "প্রথম আপিল" },
  { value: "death_reference", label: "ডেথ রেফারেন্স" },
] as const;

/** The prediction screen never offered death references. */
export const PREDICT_CASE_TYPES = CASE_TYPES.filter((t) => t.value !== "death_reference");

export const YEARS = [
  { value: "2020", label: "২০২০ থেকে" },
  { value: "2015", label: "২০১৫ থেকে" },
  { value: "2010", label: "২০১০ থেকে" },
  { value: "2000", label: "২০০০ থেকে" },
] as const;

/** All 8,615 judgements in the index are High Court Division, so Appellate Division could only return nothing (D-212). */
export const COURTS = [{ value: "HCD", label: "হাইকোর্ট বিভাগ" }] as const;

export const OUTCOMES = [
  { value: "rule_absolute", label: "রুল চূড়ান্ত" },
  { value: "rule_discharged", label: "রুল খারিজ" },
  { value: "allowed", label: "মঞ্জুর" },
  { value: "dismissed", label: "খারিজ" },
  { value: "disposed", label: "নিষ্পত্তি" },
] as const;

export type Tone = "success" | "danger" | "warning" | "neutral";

export function outcomeMeta(outcome: string | null | undefined): { label: string; tone: Tone } | null {
  switch (outcome) {
    case "rule_absolute":
      return { label: "রুল চূড়ান্ত", tone: "success" };
    case "allowed":
      return { label: "মঞ্জুর", tone: "success" };
    case "rule_discharged":
      return { label: "রুল খারিজ", tone: "danger" };
    case "dismissed":
      return { label: "খারিজ", tone: "danger" };
    case "disposed":
      return { label: "নিষ্পত্তি", tone: "warning" };
    default:
      return null;
  }
}

export function caseTypeLabel(value: string | null | undefined): string | null {
  return CASE_TYPES.find((t) => t.value === value)?.label ?? null;
}
