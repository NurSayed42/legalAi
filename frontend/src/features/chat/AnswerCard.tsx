import {
  BookOpen,
  CalendarClock,
  CircleAlert,
  FileStack,
  Gavel,
  Info,
  Landmark,
  ListChecks,
  MessageCircleQuestion,
  ReceiptText,
  Scale,
  ScrollText,
  ShieldAlert,
  TriangleAlert,
  UserRoundCheck,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Controls";
import { Badge, Disclaimer, SectionHeading, SourceLink } from "@/components/ui/Display";
import { citationLabel, hasProcedure, INTENT_LABEL, normalizeAnswer } from "@/lib/answer";
import { cn } from "@/lib/cn";
import { bnDigits, looksEnglish } from "@/lib/format";
import type { Answer, ChatResponse, Intent, LawCitation, Procedure } from "@/lib/types";

const INTENT_ICON: Record<Intent, typeof Scale> = {
  law_lookup: ScrollText,
  case_search: Landmark,
  prediction: Gavel,
  procedure: ListChecks,
  general: MessageCircleQuestion,
};

/** Text that may be English gets the right language tag, so it's read and hyphenated correctly. */
function Text({ value, className }: { value: string; className?: string }) {
  return (
    <span lang={looksEnglish(value) ? "en" : undefined} className={className}>
      {value}
    </span>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="min-w-0">
      <SectionHeading>{title}</SectionHeading>
      {children}
    </section>
  );
}

function TrustNote({ tone, icon, children }: { tone: "warning" | "danger"; icon: ReactNode; children: ReactNode }) {
  return (
    <div
      className={cn(
        "flex gap-2 rounded-card border p-3 text-14 text-fg-1",
        tone === "warning" ? "border-warning bg-warning-tint" : "border-danger bg-danger-tint",
      )}
    >
      <span className={cn("mt-1", tone === "warning" ? "text-warning" : "text-danger")}>{icon}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function LawCard({ law }: { law: LawCitation }) {
  return (
    <article className="min-w-0 rounded-card border border-line-1 bg-raised p-3 md:p-4">
      {law.act && <Text value={law.act} className="block text-14 font-medium text-fg-1" />}
      {law.section && <Text value={law.section} className="mt-1 block font-mono text-13 text-accent" />}
      {law.what_it_says && <p className="mt-2 text-14 text-fg-2">{law.what_it_says}</p>}
      {law.how_it_applies && (
        <p className="mt-2 border-l-2 border-gold-500 pl-3 text-14 text-fg-1">
          <span className="sr-only">আপনার ক্ষেত্রে: </span>
          {law.how_it_applies}
        </p>
      )}
      {law.url && (
        <div className="mt-2">
          <SourceLink url={law.url} />
        </div>
      )}
    </article>
  );
}

function ProcedureBlock({ procedure }: { procedure: Procedure }) {
  const facts: { key: keyof Procedure; label: string; icon: typeof Scale; tone: string }[] = [
    { key: "court", label: "আদালত বা কর্তৃপক্ষ", icon: Landmark, tone: "text-info" },
    { key: "deadline", label: "সময়সীমা", icon: CalendarClock, tone: "text-warning" },
    { key: "cost", label: "খরচ", icon: ReceiptText, tone: "text-fg-2" },
    { key: "penalties", label: "শাস্তি বা পরিণতি", icon: ShieldAlert, tone: "text-danger" },
  ];
  return (
    <div className="flex flex-col gap-3 rounded-card border border-line-1 bg-raised p-3 md:p-4">
      {procedure.steps && procedure.steps.length > 0 && (
        <ul className="flex flex-col gap-2 text-14 text-fg-1">
          {procedure.steps.map((step, i) => (
            <li key={i}>
              <Text value={step} />
            </li>
          ))}
        </ul>
      )}
      <dl className="m-0 grid grid-cols-1 gap-3 md:grid-cols-2">
        {facts.map(({ key, label, icon: Icon, tone }) => {
          const value = procedure[key];
          return typeof value === "string" && value ? (
            <div key={key} className="min-w-0">
              <dt className={cn("flex items-center gap-1 text-14 font-medium", tone)}>
                <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
                {label}
              </dt>
              <dd className="m-0 mt-0.5 text-14 text-fg-2">
                <Text value={value} />
              </dd>
            </div>
          ) : null;
        })}
      </dl>
      {procedure.documents && procedure.documents.length > 0 && (
        <div>
          <p className="flex items-center gap-1 text-14 font-medium text-warning">
            <FileStack size={16} strokeWidth={1.75} aria-hidden="true" />
            প্রয়োজনীয় কাগজপত্র
          </p>
          <ul className="mt-1 list-disc pl-5 text-14 text-fg-2">
            {procedure.documents.map((doc, i) => (
              <li key={i}>
                <Text value={doc} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export interface FollowUpHandlers {
  /** Answers picked from options go back as a new question, in the same text format as before. */
  onQuickReply: (text: string) => void;
  /** Open-ended follow-ups don't send anything: they put the question in the box to edit. */
  onFill: (text: string) => void;
}

function FollowUps({ answer, handlers }: { answer: Answer; handlers: FollowUpHandlers }) {
  const [picked, setPicked] = useState<Record<number, string>>({});
  const options = answer.follow_up_options ?? [];
  const questions = answer.follow_up_questions ?? [];
  if (options.length === 0 && questions.length === 0) return null;
  const hasChoices = options.some((f) => f.options && f.options.length > 0);

  const submit = () => {
    const text = options
      .map((f, i) => (picked[i] ? `${f.question} ${picked[i]}` : null))
      .filter(Boolean)
      .join("\n");
    if (text) handlers.onQuickReply(text);
  };

  return (
    <Section title="আরও জানতে">
      <div className="flex flex-col gap-3">
        {options.map((f, i) => (
          <div key={i} className="min-w-0">
            <p className="mb-2 text-14 text-fg-1">{f.question}</p>
            {f.options && f.options.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {f.options.map((option) => (
                  <Chip
                    key={option}
                    selected={picked[i] === option}
                    onClick={() =>
                      // Tapping the chosen option again clears it.
                      setPicked((p) => ({ ...p, [i]: p[i] === option ? "" : option }))
                    }
                  >
                    {option}
                  </Chip>
                ))}
              </div>
            ) : (
              <Chip onClick={() => handlers.onFill("")}>টাইপ করে উত্তর দিন</Chip>
            )}
          </div>
        ))}
        {hasChoices && (
          <div>
            <Button variant="secondary" disabled={!Object.values(picked).some(Boolean)} onClick={submit}>
              উত্তর জমা দিন
            </Button>
          </div>
        )}
        {questions.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {questions.map((q) => (
              <Chip key={q} onClick={() => handlers.onFill(q)}>
                {q}
              </Chip>
            ))}
          </div>
        )}
      </div>
    </Section>
  );
}

type Checks = Pick<ChatResponse, "retrieval_sufficient" | "unverified_citations" | "contradictions_detected">;

function hasTrustIssue(checks?: Checks): boolean {
  return Boolean(
    checks &&
    (checks.retrieval_sufficient === false ||
      checks.unverified_citations?.length ||
      (checks.contradictions_detected ?? 0) > 0),
  );
}

export function AnswerCard({
  data,
  intent,
  checks,
  handlers,
}: {
  data: Answer | string | null | undefined;
  intent?: Intent | null;
  checks?: Checks;
  handlers: FollowUpHandlers;
}) {
  const { answer, raw } = normalizeAnswer(data);
  if (raw) {
    return (
      <p className="whitespace-pre-wrap text-16 text-fg-1">
        <Text value={raw} />
      </p>
    );
  }
  if (!answer) return null;

  const summary = answer.short_answer || answer.summary;
  const laws = answer.applicable_laws ?? [];
  const cases = answer.relevant_cases ?? [];
  const IntentIcon = intent ? INTENT_ICON[intent] : null;
  const issue = hasTrustIssue(checks);

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <div className="flex flex-col gap-3">
        {summary && (
          <p className="text-18 text-fg-1">
            <Text value={summary} />
          </p>
        )}
        {(intent || issue) && (
          <div className="flex flex-wrap items-center gap-2">
            {intent && IntentIcon && (
              <Badge icon={<IntentIcon size={16} strokeWidth={1.75} aria-hidden="true" />}>
                {INTENT_LABEL[intent]}
              </Badge>
            )}
            {/* The full note comes further down. This keeps the warning in view beside the answer it qualifies. */}
            {issue && (
              <Badge tone="warning" icon={<TriangleAlert size={16} strokeWidth={1.75} aria-hidden="true" />}>
                যাচাই করে নিন
              </Badge>
            )}
          </div>
        )}
      </div>

      {laws.length > 0 && (
        <Section title="প্রযোজ্য আইন">
          <div className="flex flex-col gap-2">
            {laws.map((law, i) => (
              <LawCard key={i} law={law} />
            ))}
          </div>
        </Section>
      )}

      {cases.length > 0 && (
        <Section title="সংশ্লিষ্ট রায়">
          <div className="flex flex-col gap-2">
            {cases.map((c, i) => (
              <article key={i} className="min-w-0 rounded-card border border-line-1 bg-raised p-3 md:p-4">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  {c.case_no && <Text value={c.case_no} className="min-w-0 font-mono text-13 text-accent" />}
                  {c.year ? <span className="text-14 text-fg-3">{bnDigits(c.year)}</span> : null}
                  {c.division && (
                    <span lang="en" className="font-mono text-13 text-fg-3">
                      {c.division}
                    </span>
                  )}
                </div>
                {c.holding && (
                  <p className="mt-2 text-14 text-fg-2">
                    <Text value={c.holding} />
                  </p>
                )}
              </article>
            ))}
          </div>
        </Section>
      )}

      {hasProcedure(answer) && answer.procedure && (
        <Section title="প্রক্রিয়া">
          <ProcedureBlock procedure={answer.procedure} />
        </Section>
      )}

      {answer.risks && answer.risks.length > 0 && (
        <Section title="ঝুঁকি">
          <ul className="flex flex-col gap-1 rounded-card border border-danger bg-danger-tint p-3 text-14 text-fg-1 md:p-4">
            {answer.risks.map((risk, i) => (
              <li key={i} className="flex gap-2">
                <CircleAlert size={16} strokeWidth={1.75} className="mt-1 text-danger" aria-hidden="true" />
                <span className="min-w-0">
                  <Text value={risk} />
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {answer.when_to_see_lawyer && (
        <Section title="কখন আইনজীবীর কাছে যাবেন">
          <p className="flex gap-2 text-14 text-fg-2">
            <UserRoundCheck size={18} strokeWidth={1.75} className="mt-0.5 text-info" aria-hidden="true" />
            <span className="min-w-0">
              <Text value={answer.when_to_see_lawyer} />
            </span>
          </p>
        </Section>
      )}

      <FollowUps answer={answer} handlers={handlers} />

      {issue && checks && (
        <div className="flex flex-col gap-2">
          {checks.retrieval_sufficient === false && (
            <TrustNote tone="warning" icon={<Info size={16} strokeWidth={1.75} aria-hidden="true" />}>
              এই প্রশ্নের সঙ্গে যথেষ্ট প্রাসঙ্গিক মামলা বা আইন পাওয়া যায়নি। উত্তরটি দুর্বল ভিত্তির ওপর, তাই নিজে যাচাই
              করে নিন।
            </TrustNote>
          )}
          {checks.unverified_citations && checks.unverified_citations.length > 0 && (
            <TrustNote tone="danger" icon={<CircleAlert size={16} strokeWidth={1.75} aria-hidden="true" />}>
              {bnDigits(checks.unverified_citations.length)}টি সূত্র খুঁজে পাওয়া নথিতে মেলেনি, তাই তালিকা থেকে বাদ
              দেওয়া হয়েছে:{" "}
              <Text value={checks.unverified_citations.map(citationLabel).join("; ")} className="font-mono text-13" />
            </TrustNote>
          )}
          {(checks.contradictions_detected ?? 0) > 0 && (
            <TrustNote tone="warning" icon={<BookOpen size={16} strokeWidth={1.75} aria-hidden="true" />}>
              {bnDigits(checks.contradictions_detected ?? 0)}টি বিষয়ে পরস্পরবিরোধী রায় পাওয়া গেছে। একই ধরনের মামলায়
              ভিন্ন সিদ্ধান্ত আছে।
            </TrustNote>
          )}
        </div>
      )}

      {answer.disclaimer && <Disclaimer>{answer.disclaimer}</Disclaimer>}
    </div>
  );
}
