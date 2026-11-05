/* Mirrors api_v3.py. These shapes are frozen: the UI adapts to them, never the reverse. */

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}
export type Filters = Record<string, string | number>;

export interface ChatRequest {
  question: string;
  history: ChatMessage[];
  filters: Filters | null;
}
export interface PredictRequest {
  situation: string;
  case_type: string | null;
}
export type SearchType = "both" | "cases" | "laws";
export interface SearchRequest {
  query: string;
  search_type: SearchType;
  n_results: number;
  filters: Filters | null;
}
export interface DocRequest {
  doc_type: string;
  details: string;
}

export interface LawCitation {
  act?: string;
  section?: string;
  what_it_says?: string;
  how_it_applies?: string;
  url?: string;
}
export interface CaseCitation {
  case_no?: string;
  year?: number | null;
  division?: string;
  holding?: string;
}
export interface Procedure {
  steps?: string[];
  court?: string;
  deadline?: string;
  documents?: string[];
  cost?: string;
  penalties?: string;
}
export interface FollowUp {
  question: string;
  options?: string[];
}
export interface Answer {
  short_answer?: string;
  summary?: string;
  applicable_laws?: LawCitation[];
  relevant_cases?: CaseCitation[];
  procedure?: Procedure;
  risks?: string[];
  when_to_see_lawyer?: string;
  follow_up_options?: FollowUp[];
  follow_up_questions?: string[];
  disclaimer?: string;
}
export type UnverifiedCitation =
  string | { case_no?: string; inline_case?: string; inline_section?: string; act?: string; section?: string };

export interface OutcomeStats {
  total_similar_cases?: number;
  petitioner_won?: number;
  petitioner_lost?: number;
  disposed_settled?: number;
  win_rate_pct?: number;
  year_range?: string;
  common_subjects?: string[];
}
export type Intent = "law_lookup" | "case_search" | "prediction" | "procedure" | "general";

export interface ChatResponse {
  answer: Answer | string;
  sources?: { cases?: unknown[]; laws?: unknown[] };
  intent?: Intent | null;
  outcome_stats?: OutcomeStats;
  contradictions_detected?: number;
  citation_verified?: boolean;
  unverified_citations?: UnverifiedCitation[];
  retrieval_sufficient?: boolean;
}
export interface PredictResponse extends ChatResponse {
  prediction: Answer | string;
  similar_cases_used?: number;
  laws_referenced?: number;
}
export interface CaseResult {
  case_no?: string;
  year?: number | null;
  type?: string | null;
  outcome?: string | null;
  division?: string | null;
  subjects?: string | null;
  judges?: string | null;
  preview?: string;
  score?: number;
}
export interface LawResult {
  title?: string;
  section?: string;
  url?: string | null;
  year?: number | null;
  type?: string | null;
  preview?: string;
  score?: number;
}
export interface SearchResponse {
  cases?: CaseResult[];
  laws?: LawResult[];
}
export interface DocResponse {
  document: string;
  doc_type?: string;
  laws_referenced?: { title?: string | null; section?: string | null; url?: string | null }[];
}
export interface StatsResponse {
  judgements?: { estimated_cases?: number; coverage?: string };
  laws?: { total_acts?: number; source?: string };
}
