/* Pure helpers, with no browser or framework imports, so the unit tests can run on plain Node. */

const BN_DIGITS = "০১২৩৪৫৬৭৮৯";

/** Digits only, no grouping: for years and case numbers inside Bangla text. */
export function bnDigits(value: number | string): string {
  return String(value).replace(/\d/g, (d) => BN_DIGITS[Number(d)] ?? d);
}

/** Grouped, the way Bangla copy reads: 8615 becomes ৮,৬১৫. */
export function bnNumber(value: number): string {
  return new Intl.NumberFormat("bn-BD", { useGrouping: true }).format(value);
}

/** Retrieved and LLM text can carry any URL. Only web links may become clickable. */
export function safeUrl(url: unknown): string | null {
  if (typeof url !== "string") return null;
  try {
    const parsed = new URL(url.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Bangla-script characters in a piece of text, used to choose a lang attribute. */
export function looksEnglish(text: string): boolean {
  const latin = (text.match(/[A-Za-z]/g) ?? []).length;
  const bangla = (text.match(/[ঀ-৿]/g) ?? []).length;
  return latin > bangla;
}

export type SearchSegment = { text: string; hit: boolean };

const QUERY_STOPWORDS = new Set([
  "the",
  "and",
  "of",
  "in",
  "on",
  "for",
  "to",
  "a",
  "an",
  "or",
  "by",
  "at",
  "is",
  "ও",
  "এবং",
  "বা",
]);

/** Splits text around the query's terms, so a match can be marked without ever building HTML. */
export function highlightSegments(text: string, query: string): SearchSegment[] {
  const terms = [...new Set(query.toLowerCase().split(/[\s,;:!?।()"'“”]+/))]
    .filter((t) => t.length >= 2 && !QUERY_STOPWORDS.has(t))
    .sort((a, b) => b.length - a.length)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\/-]/g, "\\$&"));
  if (terms.length === 0 || !text) return text ? [{ text, hit: false }] : [];
  const pattern = new RegExp(`(${terms.join("|")})`, "giu");
  return text
    .split(pattern)
    .filter((part) => part !== "")
    .map((part, index) => ({ text: part, hit: index >= 0 && pattern.test(part) && resetLastIndex(pattern) }));
}

function resetLastIndex(pattern: RegExp): boolean {
  pattern.lastIndex = 0;
  return true;
}

/** Width for the relevance bar: the score only means something relative to the best result in the same list. */
export function relativeToTop(score: number | undefined, top: number): number {
  if (!score || top <= 0) return 0;
  return Math.max(0.08, Math.min(1, score / top));
}

export type Block =
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "rule" }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "paragraph"; lines: string[] };

/** Just enough Markdown for generated documents. Produces data, so the renderer never touches HTML. */
export function parseDocument(source: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ type: "paragraph", lines: paragraph });
    paragraph = [];
  };
  const flushList = () => {
    if (list) blocks.push({ type: "list", ...list });
    list = null;
  };
  for (const raw of source.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(heading[1]?.length ?? 1, 3) as 1 | 2 | 3;
      blocks.push({ type: "heading", level, text: (heading[2] ?? "").replace(/#+\s*$/, "").trim() });
      continue;
    }
    if (/^([-*_])\1{2,}$/.test(line)) {
      flushParagraph();
      flushList();
      blocks.push({ type: "rule" });
      continue;
    }
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const numbered = line.match(/^(?:\d+|[০-৯]+)[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const ordered = Boolean(numbered);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((bullet?.[1] ?? numbered?.[1] ?? "").trim());
      continue;
    }
    flushList();
    // A line that is entirely bold, on its own, is how the generator writes a title.
    const title = line.match(/^\*\*([^*]+)\*\*:?$/);
    if (title && paragraph.length === 0) {
      blocks.push({ type: "heading", level: blocks.length === 0 ? 1 : 3, text: (title[1] ?? "").trim() });
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  flushList();
  return blocks;
}

export type Inline = { text: string; strong: boolean };

export function parseInline(text: string): Inline[] {
  return text
    .split(/(\*\*[^*]+\*\*|__[^_]+__)/g)
    .filter((part) => part !== "")
    .map((part) => {
      const bold = part.match(/^(?:\*\*|__)(.+)(?:\*\*|__)$/);
      return bold ? { text: bold[1] ?? "", strong: true } : { text: part, strong: false };
    });
}

/** Plain text for copy and .txt download: no Markdown symbols a person would have to clean up. */
export function stripMarkdown(source: string): string {
  return source
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) =>
      line
        .replace(/^\s*#{1,6}\s+/, "")
        .replace(/^\s*([-*_])\1{2,}\s*$/, "")
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .replace(/__([^_]+)__/g, "$1"),
    )
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
