import {
  ArrowDown,
  Banknote,
  FileQuestion,
  Gavel,
  HeartHandshake,
  House,
  RefreshCw,
  Scale,
  ScrollText,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  User,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useStats } from "@/app/StatsProvider";
import { BrandMark } from "@/components/layout/Header";
import { Button, IconButton } from "@/components/ui/Button";
import { CopyButton } from "@/components/ui/CopyButton";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { ErrorState, Skeleton } from "@/components/ui/Display";
import type { FilterValues } from "@/components/ui/FilterPanel";
import { answerToText } from "@/lib/answer";
import { api, toApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { SLOW_ANSWER_AFTER_MS } from "@/lib/config";
import { bnNumber, looksEnglish } from "@/lib/format";
import { buildHistory, filtersOrNull, type Turn } from "@/lib/history";
import { useAbortable, useMediaQuery } from "@/lib/hooks";
import type { ChatRequest, ChatResponse, Filters } from "@/lib/types";
import { AnswerCard } from "./AnswerCard";
import { Composer } from "./Composer";

const SUGGESTIONS: { text: string; icon: LucideIcon }[] = [
  { text: "জমি দখলের মামলায় কী করতে পারি?", icon: House },
  { text: "Murder case এ bail পাওয়ার সম্ভাবনা কী?", icon: Gavel },
  { text: "চেক ডিজঅনার মামলার সম্পূর্ণ procedure কী?", icon: Banknote },
  { text: "Divorce এর আইনি প্রক্রিয়া কী?", icon: HeartHandshake },
  { text: "Section 302 Penal Code কী বলে?", icon: ScrollText },
  { text: "Writ petition কখন করা যায়?", icon: FileQuestion },
];

type Item =
  | { id: number; role: "user"; text: string }
  | {
      id: number;
      role: "ai";
      status: "loading" | "done" | "failed" | "stopped";
      request: ChatRequest;
      response?: ChatResponse;
      error?: string;
      feedback?: "up" | "down";
    };

function toTurn(item: Item): Turn {
  if (item.role === "user") return { role: "user", text: item.text };
  if (item.status === "done" && item.response) return { role: "ai", status: "done", answer: item.response.answer };
  return { role: "ai", status: item.status === "done" ? "failed" : item.status };
}

/** Same filter payload as before: only the set fields, and the year as the string the select held. */
function toApiFilters(values: FilterValues): Filters {
  const out: Filters = {};
  for (const key of ["case_type", "division", "year_from"]) {
    const value = values[key];
    if (value) out[key] = value;
  }
  return out;
}

function nearBottom(): boolean {
  return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
}

function Empty({ onPick }: { onPick: (text: string) => void }) {
  const { stats, status } = useStats();
  const cases = stats?.judgements?.estimated_cases;
  const acts = stats?.laws?.total_acts;
  return (
    <div className="ambient -mx-4 flex flex-1 flex-col items-center justify-center gap-6 px-4 pb-6 pt-4 text-center md:mx-0 md:pt-8">
      <BrandMark size="lg" />
      <div className="flex w-full flex-col items-center gap-2">
        <h1 className="font-serif text-38 text-fg-1">বাংলাদেশ আইনি সহায়তা</h1>
        <div className="min-h-[54px] max-w-[36rem] text-16 text-fg-2 [text-wrap:balance]">
          {cases && acts ? (
            <p>
              {bnNumber(cases)}টি রায় এবং {bnNumber(acts)}টি আইন থেকে সূত্রসহ উত্তর। বাংলা বা ইংরেজি, যেকোনো ভাষায়
              জিজ্ঞেস করুন।
            </p>
          ) : status === "unreachable" ? (
            <p>সূত্রসহ আইনি তথ্য। বাংলা বা ইংরেজি, যেকোনো ভাষায় জিজ্ঞেস করুন।</p>
          ) : (
            <div className="flex flex-col items-center gap-2 pt-1" aria-hidden="true">
              <Skeleton className="h-4 w-[18rem] max-w-full" />
              <Skeleton className="h-4 w-[12rem] max-w-full" />
            </div>
          )}
        </div>
      </div>
      <ul
        aria-label="উদাহরণ প্রশ্ন"
        className="no-scrollbar -mx-4 flex w-[calc(100%+32px)] snap-x snap-mandatory scroll-px-4 gap-2 overflow-x-auto px-4 pb-1 md:mx-0 md:grid md:w-full md:grid-cols-2 md:overflow-visible md:px-0"
      >
        {SUGGESTIONS.map(({ text, icon: Icon }) => (
          <li key={text} className="w-[248px] shrink-0 snap-start md:w-auto">
            <button
              type="button"
              onClick={() => onPick(text)}
              className="press group flex h-full min-h-[72px] w-full items-center gap-3 rounded-card border border-line-1 bg-surface p-3 text-left text-14 text-fg-1 shadow-1 hover:border-line-strong hover:bg-raised"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-line-1 bg-raised text-fg-2 transition group-hover:text-accent">
                <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
              </span>
              <span className="min-w-0">{text}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Thinking() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), SLOW_ANSWER_AFTER_MS);
    return () => clearTimeout(timer);
  }, []);
  return (
    <div className="flex flex-col gap-3">
      <div role="status" className="flex items-center gap-2 text-14 text-fg-2">
        <span className="flex gap-1" aria-hidden="true">
          <span className="typing-dot h-1.5 w-1.5 rounded-full bg-gold-500" />
          <span className="typing-dot h-1.5 w-1.5 rounded-full bg-gold-500" />
          <span className="typing-dot h-1.5 w-1.5 rounded-full bg-gold-500" />
        </span>
        {slow ? "একটু সময় লাগছে। উত্তর আসতে এক-দুই মিনিট লাগতে পারে।" : "মামলা ও আইন দেখে উত্তর তৈরি হচ্ছে"}
      </div>
      <div className="flex flex-col gap-2" aria-hidden="true">
        <Skeleton className="h-5 w-full" />
        <Skeleton className="h-5 w-11/12" />
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="mt-3 h-24 w-full rounded-card" />
      </div>
    </div>
  );
}

function AiAvatar() {
  return (
    <span
      aria-hidden="true"
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-gold-600 bg-gold-tint text-accent"
    >
      <Scale size={16} strokeWidth={1.75} />
    </span>
  );
}

export default function ChatTab({ active }: { active: boolean }) {
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [filters, setFilters] = useState<FilterValues>({});
  const [confirmClear, setConfirmClear] = useState(false);
  const [newAnswerBelow, setNewAnswerBelow] = useState(false);
  const { begin, stop } = useAbortable();
  const composer = useRef<HTMLTextAreaElement>(null);
  const nextId = useRef(1);
  const busyRef = useRef(false);
  const stuckToBottom = useRef(true);
  const lastAnswer = useRef<HTMLElement | null>(null);
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const busy = items.some((i) => i.role === "ai" && i.status === "loading");

  useEffect(() => {
    if (!active) return;
    const onScroll = () => {
      stuckToBottom.current = nearBottom();
      if (stuckToBottom.current) setNewAnswerBelow(false);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [active]);

  const scrollToEnd = useCallback(() => {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: reducedMotion ? "auto" : "smooth" });
  }, [reducedMotion]);

  const showAnswer = useCallback(() => {
    lastAnswer.current?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    setNewAnswerBelow(false);
  }, [reducedMotion]);

  // New content: follow it, unless the person has scrolled up to read. Then offer a pill instead.
  const lastStatus = (() => {
    const last = items[items.length - 1];
    return last?.role === "ai" ? `${last.id}:${last.status}` : String(last?.id ?? 0);
  })();
  useLayoutEffect(() => {
    if (!active || items.length === 0) return;
    const last = items[items.length - 1];
    if (last?.role === "ai" && last.status !== "loading") {
      if (stuckToBottom.current) showAnswer();
      else setNewAnswerBelow(true);
    } else if (stuckToBottom.current) {
      scrollToEnd();
    }
    // Only when the newest item changes state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastStatus]);

  const run = useCallback(
    async (id: number, request: ChatRequest) => {
      const { signal } = begin();
      busyRef.current = true;
      try {
        const response = await api.chat(request, signal);
        setItems((all) =>
          all.map((i) => (i.id === id && i.role === "ai" ? { ...i, status: "done", response, error: undefined } : i)),
        );
      } catch (error) {
        const apiError = toApiError(error);
        setItems((all) =>
          all.map((i) =>
            i.id === id && i.role === "ai"
              ? { ...i, status: apiError.kind === "aborted" ? "stopped" : "failed", error: apiError.message }
              : i,
          ),
        );
      } finally {
        busyRef.current = false;
      }
    },
    [begin],
  );

  const send = useCallback(
    (text?: string) => {
      const question = (text ?? input).trim();
      if (!question || busyRef.current) return;
      busyRef.current = true;
      setInput("");
      stuckToBottom.current = true;
      const request: ChatRequest = {
        question,
        history: buildHistory(items.map(toTurn)),
        filters: filtersOrNull(toApiFilters(filters)),
      };
      const userId = nextId.current++;
      const aiId = nextId.current++;
      setItems((all) => [
        ...all,
        { id: userId, role: "user", text: question },
        { id: aiId, role: "ai", status: "loading", request },
      ]);
      void run(aiId, request);
    },
    [filters, input, items, run],
  );

  const regenerate = useCallback(
    (id: number, request: ChatRequest) => {
      if (busyRef.current) return;
      busyRef.current = true;
      stuckToBottom.current = true;
      setItems((all) =>
        all.map((i) =>
          i.id === id && i.role === "ai"
            ? { ...i, status: "loading", response: undefined, error: undefined, feedback: undefined }
            : i,
        ),
      );
      void run(id, request);
    },
    [run],
  );

  const fill = useCallback((text: string) => {
    if (text) setInput(text);
    composer.current?.focus();
  }, []);

  const setFeedback = (id: number, feedback: "up" | "down") =>
    setItems((all) =>
      all.map((i) =>
        i.id === id && i.role === "ai" ? { ...i, feedback: i.feedback === feedback ? undefined : feedback } : i,
      ),
    );

  const lastAnswerId = [...items].reverse().find((i) => i.role === "ai")?.id;

  return (
    <div className="mx-auto flex w-full max-w-chat flex-1 flex-col">
      {items.length === 0 ? (
        <Empty onPick={send} />
      ) : (
        <div className="flex flex-1 flex-col gap-6">
          <div className="flex items-center justify-between">
            <h1 className="sr-only">আইনি প্রশ্নোত্তর</h1>
            <span />
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              icon={<Trash2 size={16} strokeWidth={1.75} aria-hidden="true" />}
              onClick={() => setConfirmClear(true)}
            >
              আলাপ মুছুন
            </Button>
          </div>
          {/* The log announces finished answers. The status line inside Thinking announces waiting. */}
          <div role="log" aria-live="polite" aria-relevant="additions" className="flex flex-col gap-6">
            {items.map((item) =>
              item.role === "user" ? (
                <div key={item.id} className="rise-in flex justify-end gap-2">
                  <p
                    lang={looksEnglish(item.text) ? "en" : undefined}
                    className="min-w-0 max-w-[85%] whitespace-pre-wrap rounded-panel rounded-br-sm bg-field px-4 py-2 text-16 text-fg-1"
                  >
                    {item.text}
                  </p>
                  <span
                    aria-hidden="true"
                    className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line-2 text-fg-2 xs:flex"
                  >
                    <User size={16} strokeWidth={1.75} />
                  </span>
                </div>
              ) : (
                <article
                  key={item.id}
                  ref={item.id === lastAnswerId ? lastAnswer : undefined}
                  aria-label="উত্তর"
                  className="group rise-in flex min-w-0 scroll-mt-[calc(var(--header-h)+64px)] gap-3"
                >
                  <div className="hidden md:block">
                    <AiAvatar />
                  </div>
                  <div className="min-w-0 flex-1">
                    {item.status === "loading" && <Thinking />}
                    {item.status === "failed" && (
                      <ErrorState
                        message={item.error ?? "উত্তর আনা যায়নি।"}
                        onRetry={busy ? undefined : () => regenerate(item.id, item.request)}
                      />
                    )}
                    {item.status === "stopped" && (
                      <div className="flex flex-wrap items-center gap-2 rounded-card border border-line-2 p-3 text-14 text-fg-2">
                        <span className="min-w-0 flex-1">আপনি উত্তরটি থামিয়েছেন।</span>
                        <Button
                          size="sm"
                          disabled={busy}
                          icon={<RefreshCw size={16} strokeWidth={1.75} aria-hidden="true" />}
                          onClick={() => regenerate(item.id, item.request)}
                        >
                          আবার চেষ্টা করুন
                        </Button>
                      </div>
                    )}
                    {item.status === "done" && item.response && (
                      <>
                        <AnswerCard
                          data={item.response.answer}
                          intent={item.response.intent}
                          checks={item.response}
                          handlers={{ onQuickReply: send, onFill: fill }}
                        />
                        <div className="reveal-on-hover mt-3 flex flex-wrap items-center gap-1">
                          <CopyButton text={answerToText(item.response.answer)} />
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            icon={<RefreshCw size={16} strokeWidth={1.75} aria-hidden="true" />}
                            onClick={() => regenerate(item.id, item.request)}
                          >
                            আবার তৈরি করুন
                          </Button>
                          <IconButton
                            size="sm"
                            label="উত্তরটি ভালো লেগেছে"
                            aria-pressed={item.feedback === "up"}
                            className={cn(item.feedback === "up" && "text-accent")}
                            onClick={() => setFeedback(item.id, "up")}
                          >
                            <ThumbsUp size={16} strokeWidth={1.75} aria-hidden="true" />
                          </IconButton>
                          <IconButton
                            size="sm"
                            label="উত্তরটি ভালো লাগেনি"
                            aria-pressed={item.feedback === "down"}
                            className={cn(item.feedback === "down" && "text-accent")}
                            onClick={() => setFeedback(item.id, "down")}
                          >
                            <ThumbsDown size={16} strokeWidth={1.75} aria-hidden="true" />
                          </IconButton>
                        </div>
                      </>
                    )}
                  </div>
                </article>
              ),
            )}
          </div>
        </div>
      )}

      <div className="relative">
        {newAnswerBelow && (
          <div className="pointer-events-none sticky bottom-[calc(var(--bottom-inset)+84px)] z-[6] flex h-0 justify-center md:bottom-[92px]">
            <button
              type="button"
              onClick={showAnswer}
              className="pop-in press pointer-events-auto absolute bottom-2 inline-flex h-9 items-center gap-1 rounded-full border border-line-2 bg-raised px-3 text-14 text-fg-1 shadow-2 hover:bg-field"
            >
              <ArrowDown size={16} strokeWidth={1.75} aria-hidden="true" />
              নতুন উত্তর
            </button>
          </div>
        )}
      </div>
      <Composer
        ref={composer}
        value={input}
        onChange={setInput}
        onSend={() => send()}
        onStop={stop}
        busy={busy}
        filters={filters}
        onFilters={setFilters}
      />
      <ConfirmDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="আলাপটি মুছবেন?"
        body="এই আলাপের সব প্রশ্ন ও উত্তর মুছে যাবে। এগুলো কোথাও সংরক্ষিত নেই, তাই ফেরত আনা যাবে না।"
        confirmLabel="মুছে ফেলুন"
        onConfirm={() => {
          setItems([]);
          setNewAnswerBelow(false);
          composer.current?.focus();
        }}
      />
    </div>
  );
}
