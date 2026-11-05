import { ChevronDown, CircleAlert, ExternalLink, RefreshCw, TriangleAlert } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";
import { hostOf, looksEnglish, safeUrl } from "@/lib/format";
import { Button } from "./Button";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("min-w-0 rounded-card border border-line-1 bg-surface shadow-1", className)} {...rest} />;
}

const BADGE = {
  neutral: "border-line-2 text-fg-2",
  gold: "border-gold-500 bg-gold-tint text-accent",
  success: "border-transparent bg-success-tint text-success",
  danger: "border-transparent bg-danger-tint text-danger",
  warning: "border-transparent bg-warning-tint text-warning",
  info: "border-transparent bg-info-tint text-info",
} as const;

export function Badge({
  tone = "neutral",
  icon,
  className,
  children,
  lang,
}: {
  tone?: keyof typeof BADGE;
  icon?: ReactNode;
  className?: string;
  children: ReactNode;
  lang?: string;
}) {
  return (
    <span
      lang={lang}
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-14 leading-snug",
        BADGE[tone],
        className,
      )}
    >
      {icon}
      <span className="min-w-0 truncate">{children}</span>
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd
      lang="en"
      className="inline-flex h-6 items-center rounded-sm border border-line-2 bg-raised px-1.5 font-mono text-12 text-fg-2"
    >
      {children}
    </kbd>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("shimmer rounded", className)} />;
}

export function EmptyState({
  icon,
  title,
  children,
  className,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-3 px-4 py-10 text-center", className)}>
      <span className="flex h-12 w-12 items-center justify-center rounded-card border border-line-2 bg-raised text-fg-2">
        {icon}
      </span>
      <h2 className="text-18 text-fg-1">{title}</h2>
      {children && <div className="max-w-prose text-14 text-fg-2">{children}</div>}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
  retryLabel = "আবার চেষ্টা করুন",
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-card border border-danger bg-danger-tint p-4 xs:flex-row xs:items-center"
    >
      <CircleAlert size={20} strokeWidth={1.75} className="text-danger" aria-hidden="true" />
      <p className="min-w-0 flex-1 text-14 text-fg-1">{message}</p>
      {onRetry && (
        <Button size="sm" icon={<RefreshCw size={16} strokeWidth={1.75} aria-hidden="true" />} onClick={onRetry}>
          {retryLabel}
        </Button>
      )}
    </div>
  );
}

/** Collapsed by default so it doesn't crowd the answer. The fact that it's legal information, not advice, stays visible. */
export function Disclaimer({ children, summary = "এটি আইনি পরামর্শ নয়" }: { children: ReactNode; summary?: string }) {
  return (
    <details className="group/disclaimer rounded border border-line-1 text-14 text-fg-2">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 rounded px-3 hover:text-fg-1 [&::-webkit-details-marker]:hidden">
        <TriangleAlert size={16} strokeWidth={1.75} className="text-warning" aria-hidden="true" />
        <span className="flex-1">{summary}</span>
        <ChevronDown
          size={16}
          strokeWidth={1.75}
          className="transition group-open/disclaimer:rotate-180"
          aria-hidden="true"
        />
      </summary>
      <p className="px-3 pb-3">{children}</p>
    </details>
  );
}

/** A law or case reference. Becomes a link only if the URL is a web address. */
export function CitationPill({ title, detail, url }: { title: string; detail?: string | null; url?: string | null }) {
  const href = safeUrl(url);
  const body = (
    <>
      <span className="min-w-0">
        <span lang={looksEnglish(title) ? "en" : undefined} className="block text-14 text-fg-1">
          {title}
        </span>
        {detail && (
          <span lang={looksEnglish(detail) ? "en" : undefined} className="block font-mono text-13 text-fg-2">
            {detail}
          </span>
        )}
      </span>
      {href && <ExternalLink size={16} strokeWidth={1.75} className="text-fg-3" aria-hidden="true" />}
    </>
  );
  const shared =
    "flex min-w-0 max-w-full items-start justify-between gap-2 rounded-card border border-line-2 bg-raised px-3 py-2";
  return href ? (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(shared, "press min-h-11 no-underline hover:border-line-strong")}
    >
      {body}
    </a>
  ) : (
    <span className={shared}>{body}</span>
  );
}

export function SourceLink({ url }: { url?: string | null }) {
  const href = safeUrl(url);
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="hit-target relative inline-flex max-w-full items-center gap-1 rounded-sm font-mono text-13 text-fg-2 underline decoration-line-strong underline-offset-2 hover:text-fg-1"
      lang="en"
    >
      <span className="truncate">{hostOf(href)}</span>
      <ExternalLink size={16} strokeWidth={1.75} aria-hidden="true" />
      <span className="sr-only" lang="bn">
        (নতুন ট্যাবে খুলবে)
      </span>
    </a>
  );
}

/** A plain bar. Its label says what it measures, so the width is never read as a promise. */
export function Meter({ value, label, className }: { value: number; label: string; className?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div role="img" aria-label={label} className={cn("h-1 w-full overflow-hidden rounded-full bg-field", className)}>
      <div className="h-full rounded-full bg-gold-500 transition-[width] duration-slow" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function SectionHeading({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <h3 id={id} className="mb-2 text-16 text-fg-1">
      {children}
    </h3>
  );
}
