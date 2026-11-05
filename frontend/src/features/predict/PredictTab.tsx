import { Gavel, Lightbulb } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { ChipGroup } from "@/components/ui/Controls";
import { Card, EmptyState, ErrorState, Skeleton } from "@/components/ui/Display";
import { Textarea } from "@/components/ui/Field";
import { api, toApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { SMALL_SAMPLE_BELOW } from "@/lib/config";
import { bnDigits } from "@/lib/format";
import { useAbortable } from "@/lib/hooks";
import { PREDICT_CASE_TYPES } from "@/lib/labels";
import type { OutcomeStats, PredictResponse } from "@/lib/types";
import { AnswerCard } from "@/features/chat/AnswerCard";

/** "auto" is only the chip's own value. It's sent as null, the way the old UI sent "Auto detect". */
const AUTO = "auto";
const CASE_TYPE_OPTIONS = [{ value: AUTO, label: "স্বয়ংক্রিয়" }, ...PREDICT_CASE_TYPES];

const EXAMPLES = [
  "আমার জমির registered deed আছে, প্রতিপক্ষ জোর করে দখল নিয়েছে। ৩ জন সাক্ষী আছে এবং mutation আমার নামে।",
  "চেক ডিজঅনার হয়েছে ৫ লক্ষ টাকার। চেক দেওয়ার পর ব্যাংক account বন্ধ করে দিয়েছে অপরপক্ষ।",
  "সরকারি চাকরি থেকে বিনা নোটিশে বরখাস্ত করা হয়েছে। ২০ বছরের service record ভালো।",
];

function Stat({ value, label, tone }: { value: number; label: string; tone?: string }) {
  return (
    <div className="min-w-0 rounded-card border border-line-1 bg-raised p-3">
      <div className={cn("text-24 font-semibold leading-tight", tone ?? "text-fg-1")}>{bnDigits(value)}</div>
      <div className="mt-1 text-14 text-fg-2">{label}</div>
    </div>
  );
}

/**
 * Plain counts, on purpose (D-212). The API's win rate comes from at most four cases picked for similarity,
 * so it can't support a probability. A ring with a percentage would read as a forecast of the person's own case.
 */
function PastOutcomes({ stats, cases, laws }: { stats: OutcomeStats; cases: number; laws: number }) {
  const total = stats.total_similar_cases ?? 0;
  const won = stats.petitioner_won ?? 0;
  const lost = stats.petitioner_lost ?? 0;
  const other = Math.max(0, total - won - lost);
  const parts = [
    { key: "won", count: won, className: "bg-success", label: "আবেদনকারী জিতেছেন" },
    { key: "lost", count: lost, className: "bg-danger", label: "আবেদনকারী হেরেছেন" },
    { key: "other", count: other, className: "bg-fg-3", label: "অন্যান্য বা অজানা" },
  ];
  return (
    <Card className="p-4 md:p-5">
      <h2 className="text-18 text-fg-1">খুঁজে পাওয়া রায়গুলোর ফল</h2>
      {total > 0 ? (
        <>
          <p className="mt-2 text-16 text-fg-1">
            <strong className="font-semibold text-accent">{bnDigits(total)}টি</strong> রায়ের মধ্যে{" "}
            <strong className="font-semibold text-accent">{bnDigits(won)}টিতে</strong> আবেদনকারী জিতেছেন।
          </p>
          <div
            role="img"
            aria-label={parts.map((p) => `${p.label}: ${bnDigits(p.count)}`).join(", ")}
            className="mt-3 flex h-2 w-full gap-0.5 overflow-hidden rounded-full bg-field"
          >
            {parts.map(
              (p) =>
                p.count > 0 && (
                  <div
                    key={p.key}
                    className={cn("h-full transition-[width] duration-slow", p.className)}
                    style={{ width: `${(p.count / total) * 100}%` }}
                  />
                ),
            )}
          </div>
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-14 text-fg-2">
            {parts.map((p) => (
              <li key={p.key} className="flex items-center gap-1.5">
                <span aria-hidden="true" className={cn("h-2 w-2 rounded-full", p.className)} />
                {p.label}: {bnDigits(p.count)}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="mt-2 text-14 text-fg-2">খুঁজে পাওয়া রায়গুলোতে ফল জানা যায়নি।</p>
      )}
      <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat value={cases} label="মিলে যাওয়া রায়" />
        <Stat value={won} label="জিতেছেন" tone="text-success" />
        <Stat value={lost} label="হেরেছেন" tone="text-danger" />
        <Stat value={laws} label="প্রাসঙ্গিক আইন" />
      </div>
      <p className="mt-4 rounded border border-warning bg-warning-tint px-3 py-2 text-14 text-fg-1">
        {total < SMALL_SAMPLE_BELOW && "নমুনা খুব ছোট, তাই এ থেকে আপনার মামলার ফল অনুমান করা যায় না। "}
        <strong className="font-semibold">এটি আইনি পরামর্শ নয়।</strong>
      </p>
    </Card>
  );
}

function ResultSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-hidden="true">
      <Card className="flex flex-col gap-3 p-4 md:p-5">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-2 w-full rounded-full" />
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Skeleton className="h-20 rounded-card" />
          <Skeleton className="h-20 rounded-card" />
          <Skeleton className="h-20 rounded-card" />
          <Skeleton className="h-20 rounded-card" />
        </div>
      </Card>
      <Card className="flex flex-col gap-2 p-4 md:p-5">
        <Skeleton className="h-5 w-full" />
        <Skeleton className="h-5 w-11/12" />
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="mt-2 h-24 rounded-card" />
      </Card>
    </div>
  );
}

export default function PredictTab(_: { active: boolean }) {
  const [situation, setSituation] = useState("");
  const [caseType, setCaseType] = useState<string>(AUTO);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PredictResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { begin } = useAbortable();
  const field = useRef<HTMLTextAreaElement>(null);
  const resultTop = useRef<HTMLDivElement>(null);
  const busy = useRef(false);

  const predict = useCallback(
    async (override?: string) => {
      const sit = (override ?? situation).trim();
      if (!sit || busy.current) return;
      busy.current = true;
      const { signal } = begin();
      setLoading(true);
      setResult(null);
      setError(null);
      // On a phone the result is below the form, so bring it into view.
      requestAnimationFrame(() => {
        if (window.matchMedia("(max-width: 1023px)").matches)
          resultTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
      });
      try {
        setResult(await api.predict({ situation: sit, case_type: caseType === AUTO ? null : caseType }, signal));
      } catch (e) {
        const apiError = toApiError(e);
        if (apiError.kind !== "aborted") setError(apiError.message);
      } finally {
        busy.current = false;
        if (!signal.aborted) setLoading(false);
      }
    },
    [begin, caseType, situation],
  );

  const handlers = {
    onQuickReply: (text: string) => {
      const updated = situation.trim() ? `${situation}\n${text}` : text;
      setSituation(updated);
      void predict(updated);
    },
    onFill: (text: string) => {
      if (text) setSituation((s) => (s.trim() ? `${s}\n${text}` : text));
      field.current?.focus();
    },
  };

  return (
    <div className="flex flex-1 flex-col gap-6">
      <div>
        <h1 className="text-30 text-fg-1">সম্ভাব্য ফলাফল</h1>
        <p className="mt-1 max-w-prose text-16 text-fg-2">
          আপনার পরিস্থিতি লিখুন। মিলে যাওয়া রায় ও প্রাসঙ্গিক আইন দেখে একটি বিশ্লেষণ পাবেন।
        </p>
      </div>
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Card className="flex flex-col gap-4 p-4 md:p-5 lg:sticky lg:top-[calc(var(--header-h)+64px)]">
          <div className="min-w-0">
            <p id="case-type-label" className="mb-1 text-14 font-medium text-fg-2">
              মামলার ধরন (ঐচ্ছিক)
            </p>
            <ChipGroup label="মামলার ধরন" value={caseType} options={CASE_TYPE_OPTIONS} onChange={setCaseType} />
          </div>
          <div>
            <label htmlFor="situation" className="mb-1 block text-14 font-medium text-fg-2">
              আপনার পরিস্থিতি
            </label>
            <Textarea
              id="situation"
              ref={field}
              rows={5}
              maxLines={14}
              value={situation}
              onChange={(e) => setSituation(e.target.value)}
              placeholder="কী ঘটেছে, কী প্রমাণ আছে, কতদিন আগে…"
            />
          </div>
          <div className="min-w-0">
            <p className="mb-1 text-14 font-medium text-fg-2">উদাহরণ দিয়ে শুরু করুন</p>
            <div className="flex flex-col gap-2">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setSituation(example)}
                  className="press flex min-h-11 items-start gap-2 rounded border border-line-1 bg-raised p-2 text-left text-14 text-fg-2 hover:border-line-strong hover:text-fg-1"
                >
                  <Lightbulb size={16} strokeWidth={1.75} className="mt-1 text-fg-3" aria-hidden="true" />
                  <span className="min-w-0">{example}</span>
                </button>
              ))}
            </div>
          </div>
          <Button
            variant="primary"
            size="lg"
            loading={loading}
            disabled={!situation.trim()}
            className="w-full"
            icon={<Gavel size={18} strokeWidth={1.75} aria-hidden="true" />}
            onClick={() => void predict()}
          >
            বিশ্লেষণ করুন
          </Button>
        </Card>

        <div
          ref={resultTop}
          className="flex min-w-0 scroll-mt-[calc(var(--header-h)+16px)] flex-col gap-4"
          aria-live="polite"
        >
          {loading && (
            <>
              <p role="status" className="sr-only">
                বিশ্লেষণ তৈরি হচ্ছে, এক-দুই মিনিট লাগতে পারে।
              </p>
              <ResultSkeleton />
            </>
          )}
          {error && <ErrorState message={error} onRetry={() => void predict()} />}
          {!loading && !error && !result && (
            <Card className="hidden lg:block">
              <EmptyState icon={<Gavel size={24} strokeWidth={1.75} aria-hidden="true" />} title="ফলাফল এখানে দেখাবে">
                মিলে যাওয়া রায়গুলোর ফল, প্রাসঙ্গিক আইন এবং আপনার পরিস্থিতির বিশ্লেষণ।
              </EmptyState>
            </Card>
          )}
          {result && !loading && (
            <>
              {result.outcome_stats && Object.keys(result.outcome_stats).length > 0 && (
                <div className="rise-in">
                  <PastOutcomes
                    stats={result.outcome_stats}
                    cases={result.similar_cases_used ?? 0}
                    laws={result.laws_referenced ?? 0}
                  />
                </div>
              )}
              <Card className="rise-in p-4 md:p-5">
                <h2 className="mb-3 text-18 text-fg-1">বিশ্লেষণ</h2>
                <AnswerCard data={result.prediction} intent="prediction" checks={result} handlers={handlers} />
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
