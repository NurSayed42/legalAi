import "@fontsource/fraunces/latin-400.css";
import "@fontsource/noto-serif-bengali/bengali-400.css";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ClipboardList,
  Download,
  FileSignature,
  FileText,
  Gavel,
  Handshake,
  House,
  KeyRound,
  Printer,
  RefreshCw,
  ScrollText,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { Fragment, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { CopyButton } from "@/components/ui/CopyButton";
import { Card, CitationPill, ErrorState, Skeleton } from "@/components/ui/Display";
import { Textarea } from "@/components/ui/Field";
import { api, toApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { bnDigits, parseDocument, parseInline, stripMarkdown, type Block } from "@/lib/format";
import { useAbortable } from "@/lib/hooks";
import type { DocResponse } from "@/lib/types";

interface DocType {
  id: string;
  icon: LucideIcon;
  en: string;
  bn: string;
  placeholder: string;
  facts: string[];
}

const TYPES: DocType[] = [
  {
    id: "nda",
    icon: Handshake,
    en: "NDA",
    bn: "গোপনীয়তা চুক্তি",
    placeholder: "যেমন: দুই প্রতিষ্ঠানের মধ্যে নতুন পণ্যের তথ্য গোপন রাখার চুক্তি…",
    facts: ["দুই পক্ষের নাম ও ঠিকানা", "কোন তথ্য গোপন থাকবে", "চুক্তির মেয়াদ", "ভঙ্গ করলে কী হবে"],
  },
  {
    id: "sale_deed",
    icon: House,
    en: "Sale Deed",
    bn: "বিক্রয় দলিল",
    placeholder: "যেমন: ঢাকার একটি জমি বিক্রির দলিল…",
    facts: [
      "বিক্রেতা ও ক্রেতার নাম ও ঠিকানা",
      "জমির দাগ, খতিয়ান ও পরিমাণ",
      "বিক্রয় মূল্য ও পরিশোধের ধরন",
      "সাক্ষীদের নাম",
    ],
  },
  {
    id: "rent",
    icon: KeyRound,
    en: "Rent Agreement",
    bn: "ভাড়া চুক্তি",
    placeholder: "যেমন: ঢাকার একটি ফ্ল্যাট এক বছরের জন্য ভাড়া…",
    facts: ["মালিক ও ভাড়াটিয়ার নাম ও ঠিকানা", "সম্পত্তির ঠিকানা", "মাসিক ভাড়া ও অগ্রিম", "চুক্তির মেয়াদ"],
  },
  {
    id: "affidavit",
    icon: ScrollText,
    en: "Affidavit",
    bn: "হলফনামা",
    placeholder: "যেমন: নামের ভুল সংশোধনের জন্য হলফনামা…",
    facts: [
      "হলফকারীর নাম, পিতা বা স্বামীর নাম, ঠিকানা",
      "কী বিষয়ে হলফ",
      "সংশ্লিষ্ট তথ্য ও তারিখ",
      "কোন কর্তৃপক্ষের জন্য",
    ],
  },
  {
    id: "complaint",
    icon: ClipboardList,
    en: "Complaint",
    bn: "অভিযোগ",
    placeholder: "যেমন: প্রতারণার বিষয়ে থানায় অভিযোগ…",
    facts: ["অভিযোগকারীর নাম ও ঠিকানা", "কার বিরুদ্ধে অভিযোগ", "ঘটনার তারিখ ও স্থান", "কী ঘটেছে ও কী প্রমাণ আছে"],
  },
  {
    id: "writ",
    icon: Gavel,
    en: "Writ Petition",
    bn: "রিট পিটিশন",
    placeholder: "যেমন: বিনা নোটিশে চাকরি থেকে বরখাস্তের বিরুদ্ধে রিট…",
    facts: [
      "আবেদনকারীর নাম ও ঠিকানা",
      "প্রতিপক্ষ কর্তৃপক্ষ",
      "কোন সিদ্ধান্ত বা আদেশের বিরুদ্ধে",
      "আপনি কী প্রতিকার চান",
    ],
  },
  {
    id: "plaint",
    icon: FileSignature,
    en: "Plaint",
    bn: "আরজি",
    placeholder: "যেমন: জমির দখল ফেরত পেতে দেওয়ানি মামলা…",
    facts: ["বাদী ও বিবাদীর নাম ও ঠিকানা", "মামলার কারণ ও তারিখ", "সম্পত্তি বা দাবির বিবরণ", "আপনি কী প্রতিকার চান"],
  },
];

const STEPS = ["ধরন বাছুন", "তথ্য দিন", "দেখে নিন"];

function Stepper({
  step,
  onGo,
  canGo,
}: {
  step: number;
  onGo: (step: number) => void;
  canGo: (step: number) => boolean;
}) {
  return (
    <ol className="flex items-center gap-2" aria-label="ধাপ">
      {STEPS.map((label, i) => {
        const done = i < step;
        const current = i === step;
        return (
          <li key={label} className="flex min-w-0 flex-1 items-center gap-2 last:flex-none">
            <button
              type="button"
              disabled={!canGo(i)}
              aria-current={current ? "step" : undefined}
              onClick={() => onGo(i)}
              className="press hit-target relative flex min-w-0 items-center gap-2 rounded text-14 disabled:cursor-default"
            >
              <span
                aria-hidden="true"
                className={cn(
                  "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-14 font-semibold",
                  current && "border-gold-500 bg-gold-500 text-on-gold",
                  done && "border-gold-500 bg-gold-tint text-accent",
                  !current && !done && "border-line-strong text-fg-3",
                )}
              >
                {done ? <Check size={16} strokeWidth={2} /> : bnDigits(i + 1)}
              </span>
              <span
                className={cn(
                  "truncate",
                  current ? "font-medium text-fg-1" : "sr-only text-fg-2 xs:not-sr-only",
                  !current && !done && "text-fg-3",
                )}
              >
                <span className="sr-only">ধাপ {bnDigits(i + 1)}: </span>
                {label}
              </span>
            </button>
            {i < STEPS.length - 1 && (
              <span aria-hidden="true" className={cn("h-px min-w-3 flex-1", done ? "bg-gold-500" : "bg-line-2")} />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Inline({ text }: { text: string }) {
  return (
    <>
      {parseInline(text).map((part, i) =>
        part.strong ? (
          <strong key={i} className="font-semibold">
            {part.text}
          </strong>
        ) : (
          <Fragment key={i}>{part.text}</Fragment>
        ),
      )}
    </>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case "heading":
      return block.level === 1 ? (
        <h2 className="mt-6 text-center text-24 first:mt-0">
          <Inline text={block.text} />
        </h2>
      ) : (
        <h3 className="mt-5 text-18 first:mt-0">
          <Inline text={block.text} />
        </h3>
      );
    case "rule":
      return <hr className="my-4 border-0 border-t border-paper-line" />;
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag className={cn("my-2 pl-6", block.ordered ? "list-decimal" : "list-disc")}>
          {block.items.map((item, i) => (
            <li key={i} className="my-1">
              <Inline text={item} />
            </li>
          ))}
        </Tag>
      );
    }
    default:
      return (
        <p className="my-2">
          {block.lines.map((line, i) => (
            <Fragment key={i}>
              {i > 0 && <br />}
              <Inline text={line} />
            </Fragment>
          ))}
        </p>
      );
  }
}

function PaperSkeleton() {
  return (
    <div
      className="mx-auto flex w-full max-w-paper flex-col gap-3 rounded-card border border-line-1 bg-surface p-6 md:p-10"
      aria-hidden="true"
    >
      <Skeleton className="mx-auto h-7 w-1/2" />
      <Skeleton className="mt-4 h-4 w-full" />
      <Skeleton className="h-4 w-11/12" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="mt-4 h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  );
}

export default function DocsTab(_: { active: boolean }) {
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState("");
  const [details, setDetails] = useState("");
  const [result, setResult] = useState<DocResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { begin } = useAbortable();
  const busy = useRef(false);
  const type = TYPES.find((t) => t.id === selected);
  const blocks = useMemo(() => (result ? parseDocument(result.document) : []), [result]);
  const plain = useMemo(() => (result ? stripMarkdown(result.document) : ""), [result]);

  const generate = async () => {
    if (!selected || !details.trim() || busy.current) return;
    busy.current = true;
    const { signal } = begin();
    setStep(2);
    setLoading(true);
    setError(null);
    setResult(null);
    window.scrollTo({ top: 0 });
    try {
      setResult(await api.generateDoc({ doc_type: selected, details }, signal));
    } catch (e) {
      const apiError = toApiError(e);
      if (apiError.kind !== "aborted") setError(apiError.message);
    } finally {
      busy.current = false;
      if (!signal.aborted) setLoading(false);
    }
  };

  const download = () => {
    const url = URL.createObjectURL(new Blob([plain], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${type?.en.toLowerCase().replace(/\s+/g, "-") ?? "document"}.txt`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const canGo = (target: number) => {
    if (loading) return false;
    if (target === 0) return true;
    if (target === 1) return Boolean(selected);
    return Boolean(result);
  };

  return (
    <div className="flex flex-1 flex-col gap-6">
      <div className="no-print">
        <h1 className="text-30 text-fg-1">দলিল তৈরি</h1>
        <p className="mt-1 max-w-prose text-16 text-fg-2">
          তথ্য দিন, একটি খসড়া পাবেন। ব্যবহারের আগে একজন আইনজীবীকে দিয়ে দেখিয়ে নিন।
        </p>
      </div>
      <div className="no-print mx-auto w-full max-w-paper">
        <Stepper step={step} onGo={setStep} canGo={canGo} />
      </div>

      {step === 0 && (
        <div className="rise-in">
          <h2 className="mb-3 text-18 text-fg-1">কোন ধরনের দলিল চান?</h2>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
            {TYPES.map((t) => {
              const Icon = t.icon;
              const isSelected = selected === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => {
                    setSelected(t.id);
                    setStep(1);
                  }}
                  className={cn(
                    "press relative flex min-h-[132px] min-w-0 flex-col items-center justify-center gap-2 rounded-card border bg-surface p-3 text-center shadow-1 last:col-span-2 hover:border-line-strong hover:bg-raised md:last:col-span-1",
                    isSelected ? "border-gold-500 shadow-glow" : "border-line-1",
                  )}
                >
                  {isSelected && (
                    <span
                      aria-hidden="true"
                      className="absolute right-2 top-2 flex h-5 w-5 items-center justify-center rounded-full bg-gold-500 text-on-gold"
                    >
                      <Check size={16} strokeWidth={2} />
                    </span>
                  )}
                  <Icon
                    size={24}
                    strokeWidth={1.75}
                    className={isSelected ? "text-accent" : "text-fg-2"}
                    aria-hidden="true"
                  />
                  <span className="text-16 font-medium leading-snug text-fg-1">{t.bn}</span>
                  <span lang="en" className="text-13 text-fg-3">
                    {t.en}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {step === 1 && type && (
        <Card className="rise-in mx-auto flex w-full max-w-paper flex-col gap-4 p-4 md:p-6">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded border border-gold-600 bg-gold-tint text-accent">
              <type.icon size={20} strokeWidth={1.75} aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <h2 className="text-18 text-fg-1">{type.bn}</h2>
              <p lang="en" className="text-13 text-fg-3">
                {type.en}
              </p>
            </div>
          </div>
          <div>
            <p className="mb-1 text-14 font-medium text-fg-2">যে তথ্যগুলো দিলে খসড়া ভালো হবে</p>
            <ul className="grid grid-cols-1 gap-x-4 gap-y-1 text-14 text-fg-2 md:grid-cols-2">
              {type.facts.map((fact) => (
                <li key={fact} className="flex items-start gap-2">
                  <Check size={16} strokeWidth={1.75} className="mt-1 text-gold-500" aria-hidden="true" />
                  <span className="min-w-0">{fact}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <label htmlFor="doc-details" className="mb-1 block text-14 font-medium text-fg-2">
              বিস্তারিত
            </label>
            <Textarea
              id="doc-details"
              rows={6}
              maxLines={16}
              value={details}
              onChange={(e) => setDetails(e.target.value)}
              placeholder={type.placeholder}
            />
          </div>
          <div className="flex flex-wrap justify-between gap-2">
            <Button icon={<ArrowLeft size={16} strokeWidth={1.75} aria-hidden="true" />} onClick={() => setStep(0)}>
              ধরন বদলান
            </Button>
            <Button
              variant="primary"
              disabled={!details.trim()}
              icon={<ArrowRight size={16} strokeWidth={1.75} aria-hidden="true" />}
              onClick={() => void generate()}
            >
              খসড়া তৈরি করুন
            </Button>
          </div>
        </Card>
      )}

      {step === 2 && (
        <div className="flex min-w-0 flex-col gap-4" aria-live="polite">
          {loading && (
            <>
              <p role="status" className="text-center text-14 text-fg-2">
                খসড়া তৈরি হচ্ছে। এক মিনিটের মতো লাগতে পারে।
              </p>
              <PaperSkeleton />
            </>
          )}
          {error && (
            <div className="mx-auto w-full max-w-paper">
              <ErrorState message={error} onRetry={() => void generate()} />
              <div className="mt-3">
                <Button icon={<ArrowLeft size={16} strokeWidth={1.75} aria-hidden="true" />} onClick={() => setStep(1)}>
                  তথ্য বদলান
                </Button>
              </div>
            </div>
          )}
          {result && !loading && (
            <>
              <div className="glass no-print sticky top-[calc(var(--header-h)+8px)] z-[5] mx-auto grid w-full max-w-paper grid-cols-4 gap-0.5 rounded-card border border-line-2 p-1 shadow-2 md:top-[calc(var(--header-h)+56px)] md:flex md:gap-1">
                <CopyButton text={plain} label="কপি" compact />
                <Button
                  size="sm"
                  variant="ghost"
                  className="!px-1 md:!px-3"
                  icon={<Download size={16} strokeWidth={1.75} aria-hidden="true" />}
                  onClick={download}
                >
                  <span lang="en">.txt</span>
                  <span className="hidden md:inline">নামান</span>
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="!px-1 md:!px-3"
                  icon={<Printer size={16} strokeWidth={1.75} aria-hidden="true" />}
                  onClick={() => window.print()}
                >
                  প্রিন্ট
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="!px-1 md:!px-3"
                  aria-label="আবার তৈরি করুন"
                  icon={<RefreshCw size={16} strokeWidth={1.75} aria-hidden="true" />}
                  onClick={() => void generate()}
                >
                  <span className="md:hidden">আবার</span>
                  <span className="hidden md:inline">আবার তৈরি করুন</span>
                </Button>
              </div>
              <article
                aria-label="তৈরি করা খসড়া"
                className="print-area rise-in mx-auto w-full max-w-paper rounded-card border border-paper-line bg-paper px-5 py-8 font-serif text-16 leading-relaxed text-paper-fg shadow-3 [overflow-wrap:anywhere] md:px-12 md:py-14"
              >
                {blocks.map((block, i) => (
                  <BlockView key={i} block={block} />
                ))}
              </article>
              {result.laws_referenced && result.laws_referenced.length > 0 && (
                <section className="no-print mx-auto w-full max-w-paper">
                  <h3 className="flex items-center gap-2 text-16 text-fg-1">
                    <TriangleAlert size={16} strokeWidth={1.75} className="text-warning" aria-hidden="true" />
                    খুঁজে পাওয়া আইন: যাচাই করুন
                  </h3>
                  <p className="mb-2 mt-1 text-14 text-fg-2">
                    এগুলো দলিলের ধরন দেখে স্বয়ংক্রিয়ভাবে খোঁজা হয়েছে। আপনার দলিলের জন্য প্রযোজ্য নাও হতে পারে।
                  </p>
                  <div className="flex flex-col gap-2">
                    {result.laws_referenced.map((law, i) => (
                      <CitationPill key={i} title={law.title ?? "—"} detail={law.section} url={law.url} />
                    ))}
                  </div>
                </section>
              )}
              <p className="no-print mx-auto flex w-full max-w-paper items-start gap-2 text-14 text-fg-2">
                <FileText size={16} strokeWidth={1.75} className="mt-1 text-fg-3" aria-hidden="true" />
                <span className="min-w-0">
                  এটি খসড়া, চূড়ান্ত দলিল নয়। স্বাক্ষর বা জমা দেওয়ার আগে একজন আইনজীবীকে দিয়ে যাচাই করে নিন।
                </span>
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
