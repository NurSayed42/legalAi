import { BookOpen, FileText, Landmark, Search, SearchX, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { searchFocus } from "@/app/tabs";
import { Button, IconButton } from "@/components/ui/Button";
import { Chip, FilterChip, SegmentedControl } from "@/components/ui/Controls";
import { Badge, Card, EmptyState, ErrorState, Kbd, Meter, Skeleton, SourceLink } from "@/components/ui/Display";
import { FilterPanel, type FilterField, type FilterValues } from "@/components/ui/FilterPanel";
import { Select } from "@/components/ui/Select";
import { api, toApiError } from "@/lib/api";
import { SEARCH_PAGE_SIZE } from "@/lib/config";
import { bnDigits, highlightSegments, looksEnglish, relativeToTop } from "@/lib/format";
import { filtersOrNull } from "@/lib/history";
import { useAbortable, useCoarsePointer, useIsDesktopNav } from "@/lib/hooks";
import { caseTypeLabel, CASE_TYPES, OUTCOMES, outcomeMeta, YEARS } from "@/lib/labels";
import type { CaseResult, Filters, LawResult, SearchResponse, SearchType } from "@/lib/types";

const TYPE_OPTIONS = [
  { value: "both", label: "সব" },
  { value: "cases", label: "মামলা" },
  { value: "laws", label: "আইন" },
] as const;

const FIELDS: readonly FilterField[] = [
  { key: "case_type", label: "মামলার ধরন", noneLabel: "সব ধরনের মামলা", options: CASE_TYPES },
  { key: "year_from", label: "বছর", noneLabel: "সব বছর", options: YEARS },
  { key: "outcome", label: "ফল", noneLabel: "সব ফল", options: OUTCOMES },
];

const SUGGESTED = ["চেক ডিজঅনার", "section 138", "জমি দখল", "writ petition", "bail"];

/** Same filter payload as before: only the set fields, with the year as a number. */
function toApiFilters(values: FilterValues): Filters {
  const out: Filters = {};
  if (values.case_type) out.case_type = values.case_type;
  if (values.year_from) out.year_from = parseInt(values.year_from, 10);
  if (values.outcome) out.outcome = values.outcome;
  return out;
}

function filterLabel(key: string, value: string): string {
  const field = FIELDS.find((f) => f.key === key);
  return field?.options.find((o) => o.value === value)?.label ?? value;
}

function Highlighted({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlightSegments(text, query).map((part, i) =>
        part.hit ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
      )}
    </>
  );
}

function CaseCard({ item, query, top }: { item: CaseResult; query: string; top: number }) {
  const outcome = outcomeMeta(item.outcome);
  const type = caseTypeLabel(item.type);
  const subjects =
    item.subjects
      ?.split(",")
      .map((s) => s.trim())
      .filter(Boolean) ?? [];
  const barLabel = `এই তালিকার সেরা মিলের তুলনায় ${bnDigits(Math.round(relativeToTop(item.score, top) * 100))} শতাংশ`;
  return (
    <Card className="rise-in flex flex-col gap-2 p-4 hover:border-line-2">
      <div className="flex min-w-0 items-start gap-2">
        <FileText size={18} strokeWidth={1.75} className="mt-1 text-fg-3" aria-hidden="true" />
        <h3
          lang={looksEnglish(item.case_no ?? "") ? "en" : undefined}
          className="min-w-0 flex-1 break-words font-mono text-14 font-normal text-fg-1 [overflow-wrap:anywhere]"
        >
          {item.case_no}
        </h3>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {item.year ? <Badge>{bnDigits(item.year)}</Badge> : null}
        <Badge>হাইকোর্ট বিভাগ</Badge>
        {type && <Badge>{type}</Badge>}
        {outcome && <Badge tone={outcome.tone === "neutral" ? "neutral" : outcome.tone}>{outcome.label}</Badge>}
      </div>
      {item.preview && (
        <p
          lang={looksEnglish(item.preview) ? "en" : undefined}
          className="clamp-3 text-14 text-fg-2 [overflow-wrap:anywhere]"
        >
          <Highlighted text={item.preview} query={query} />
        </p>
      )}
      <div className="mt-1 flex items-center gap-3">
        <span className="shrink-0 text-14 text-fg-3">মিল</span>
        <Meter value={relativeToTop(item.score, top)} label={barLabel} />
      </div>
      {subjects.length > 0 && (
        <p className="sr-only">
          বিষয়: <span lang="en">{subjects.join(", ")}</span>
        </p>
      )}
    </Card>
  );
}

function LawCard({ item, query }: { item: LawResult; query: string }) {
  return (
    <Card className="rise-in flex flex-col gap-2 border-l-2 border-l-info p-4 hover:border-line-2 hover:border-l-info">
      <div className="flex min-w-0 items-start gap-2">
        <BookOpen size={18} strokeWidth={1.75} className="mt-1 text-info" aria-hidden="true" />
        <h3
          lang={looksEnglish(item.title ?? "") ? "en" : undefined}
          className="min-w-0 flex-1 text-14 font-medium text-fg-1 [overflow-wrap:anywhere]"
        >
          {item.title}
        </h3>
        {item.year ? <span className="shrink-0 text-14 text-fg-3">{bnDigits(item.year)}</span> : null}
      </div>
      {item.section && (
        <p
          lang={looksEnglish(item.section) ? "en" : undefined}
          className="font-mono text-13 text-info [overflow-wrap:anywhere]"
        >
          {item.section}
        </p>
      )}
      {item.preview && (
        <p
          lang={looksEnglish(item.preview) ? "en" : undefined}
          className="clamp-3 text-14 text-fg-2 [overflow-wrap:anywhere]"
        >
          <Highlighted text={item.preview} query={query} />
        </p>
      )}
      {item.url && <SourceLink url={item.url} />}
    </Card>
  );
}

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <Card key={i} className="flex flex-col gap-2 p-4">
          <Skeleton className="h-5 w-2/3" />
          <div className="flex gap-2">
            <Skeleton className="h-6 w-16 rounded-full" />
            <Skeleton className="h-6 w-24 rounded-full" />
          </div>
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-3/4" />
        </Card>
      ))}
    </div>
  );
}

export default function SearchTab({ active }: { active: boolean }) {
  const [query, setQuery] = useState("");
  const [searchedQuery, setSearchedQuery] = useState("");
  const [searchType, setSearchType] = useState<SearchType>("both");
  const [filters, setFilters] = useState<FilterValues>({});
  const [size, setSize] = useState(SEARCH_PAGE_SIZE);
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { begin } = useAbortable();
  const input = useRef<HTMLInputElement>(null);
  const desktop = useIsDesktopNav();
  const coarse = useCoarsePointer();

  useEffect(() => {
    const focus = () => {
      input.current?.focus();
      input.current?.select();
      searchFocus.pending = false;
    };
    searchFocus.listener = focus;
    if (searchFocus.pending) focus();
    return () => {
      searchFocus.listener = null;
    };
  }, []);
  useEffect(() => {
    if (active && searchFocus.pending) {
      input.current?.focus();
      searchFocus.pending = false;
    }
  }, [active]);

  const run = useCallback(
    async (next: { q?: string; type?: SearchType; values?: FilterValues; n?: number } = {}) => {
      const q = next.q ?? query;
      if (!q.trim()) return;
      const n = next.n ?? SEARCH_PAGE_SIZE;
      const more = n > SEARCH_PAGE_SIZE && results !== null;
      const { signal } = begin();
      setError(null);
      if (more) setLoadingMore(true);
      else {
        setLoading(true);
        setResults(null);
      }
      try {
        const data = await api.search(
          {
            query: q,
            search_type: next.type ?? searchType,
            n_results: n,
            filters: filtersOrNull(toApiFilters(next.values ?? filters)),
          },
          signal,
        );
        setResults(data);
        setSize(n);
        setSearchedQuery(q);
      } catch (e) {
        const apiError = toApiError(e);
        if (apiError.kind !== "aborted") setError(apiError.message);
      } finally {
        if (!signal.aborted) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [begin, filters, query, results, searchType],
  );

  const hasSearched = results !== null;
  const changeType = (type: SearchType) => {
    setSearchType(type);
    if (hasSearched) void run({ type });
  };
  const applyFilters = (values: FilterValues) => {
    setFilters(values);
    if (hasSearched) void run({ values });
  };
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run();
  };
  const pick = (text: string) => {
    setQuery(text);
    void run({ q: text });
  };

  const cases = results?.cases ?? [];
  const laws = results?.laws ?? [];
  const topCase = cases[0]?.score ?? 0;
  const empty = hasSearched && cases.length === 0 && laws.length === 0;
  // The API returns at most n_results of each kind, so a full list means there may be more.
  const mayHaveMore =
    (searchType !== "laws" && cases.length >= size) || (searchType !== "cases" && laws.length >= size);
  const activeFilters = Object.entries(filters).filter(([, value]) => value);

  return (
    <div className="mx-auto flex w-full max-w-[880px] flex-1 flex-col gap-4">
      <div>
        <h1 className="text-30 text-fg-1">মামলা ও আইন অনুসন্ধান</h1>
        <p className="mt-1 text-16 text-fg-2">রায় ও আইনের ধারা খুঁজুন, বাংলা বা ইংরেজিতে।</p>
      </div>
      <form role="search" onSubmit={onSubmit} className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <Search
            size={20}
            strokeWidth={1.75}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-3"
            aria-hidden="true"
          />
          <label htmlFor="search-input" className="sr-only">
            মামলা বা আইন খুঁজুন
          </label>
          <input
            id="search-input"
            ref={input}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="যেমন চেক ডিজঅনার, section 302…"
            enterKeyHint="search"
            autoComplete="off"
            className="h-12 w-full min-w-0 rounded-card border border-line-strong bg-field pl-10 pr-20 text-16 text-fg-1 transition hover:border-fg-3 focus-visible:border-transparent [&::-webkit-search-cancel-button]:hidden"
          />
          <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-1">
            {query ? (
              <IconButton
                size="sm"
                label="মুছুন"
                showTooltip={false}
                onClick={() => {
                  setQuery("");
                  input.current?.focus();
                }}
              >
                <X size={16} strokeWidth={1.75} aria-hidden="true" />
              </IconButton>
            ) : (
              !coarse && (
                <span className="hidden gap-1 pr-2 lg:flex" aria-hidden="true">
                  <Kbd>Ctrl</Kbd>
                  <Kbd>K</Kbd>
                </span>
              )
            )}
          </div>
        </div>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          loading={loading}
          disabled={!query.trim()}
          aria-label="খুঁজুন"
          className="px-4 md:px-6"
          icon={<Search size={18} strokeWidth={1.75} aria-hidden="true" className="xs:hidden" />}
        >
          <span className="hidden xs:inline">খুঁজুন</span>
        </Button>
      </form>

      <div className="flex flex-wrap items-center gap-2">
        <SegmentedControl
          label="কোথায় খুঁজবেন"
          value={searchType}
          options={TYPE_OPTIONS}
          onChange={changeType}
          className="w-full xs:w-auto"
        />
        {desktop ? (
          <div className="flex min-w-0 flex-1 gap-2">
            {FIELDS.map((field) => (
              <Select
                key={field.key}
                hideLabel
                label={field.label}
                noneLabel={field.noneLabel}
                options={field.options}
                value={filters[field.key] ?? ""}
                onChange={(value) => applyFilters({ ...filters, [field.key]: value })}
                className="flex-1"
              />
            ))}
          </div>
        ) : (
          <FilterPanel title="ছাঁকনি" fields={FIELDS} values={filters} onApply={applyFilters} />
        )}
      </div>

      {activeFilters.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" aria-label="চালু ছাঁকনি">
          {activeFilters.map(([key, value]) => (
            <FilterChip
              key={key}
              removeLabel={`${filterLabel(key, value)} সরান`}
              onRemove={() => applyFilters({ ...filters, [key]: "" })}
            >
              {filterLabel(key, value)}
            </FilterChip>
          ))}
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col gap-4" aria-live="polite">
        {loading && (
          <>
            <p role="status" className="sr-only">
              খোঁজা হচ্ছে
            </p>
            <ListSkeleton />
          </>
        )}
        {error && <ErrorState message={error} onRetry={() => void run()} />}

        {!loading && !error && !hasSearched && (
          <EmptyState icon={<Search size={24} strokeWidth={1.75} aria-hidden="true" />} title="কী খুঁজছেন?">
            <p>মামলার নম্বর, আইনের ধারা বা বিষয় লিখুন। যেমন:</p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {SUGGESTED.map((text) => (
                <Chip key={text} onClick={() => pick(text)}>
                  <span lang={looksEnglish(text) ? "en" : undefined}>{text}</span>
                </Chip>
              ))}
            </div>
          </EmptyState>
        )}

        {!loading && !error && empty && (
          <EmptyState icon={<SearchX size={24} strokeWidth={1.75} aria-hidden="true" />} title="কিছু পাওয়া যায়নি">
            <p>
              অন্য শব্দে খুঁজে দেখুন, বা ছাঁকনি সরিয়ে দিন। বাংলায় না পেলে ইংরেজিতে খুঁজুন, কারণ বেশিরভাগ রায়
              ইংরেজিতে।
            </p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {SUGGESTED.map((text) => (
                <Chip key={text} onClick={() => pick(text)}>
                  <span lang={looksEnglish(text) ? "en" : undefined}>{text}</span>
                </Chip>
              ))}
            </div>
          </EmptyState>
        )}

        {!loading && cases.length > 0 && (
          <section className="flex flex-col gap-3" aria-label="মামলা">
            <h2 className="flex items-center gap-2 text-16 text-fg-1">
              <Landmark size={18} strokeWidth={1.75} className="text-fg-3" aria-hidden="true" />
              মামলা <span className="font-normal text-fg-3">{bnDigits(cases.length)}টি</span>
            </h2>
            {cases.map((item, i) => (
              <CaseCard key={`${item.case_no}-${i}`} item={item} query={searchedQuery} top={topCase} />
            ))}
          </section>
        )}
        {!loading && laws.length > 0 && (
          <section className="flex flex-col gap-3" aria-label="আইন">
            <h2 className="flex items-center gap-2 text-16 text-fg-1">
              <BookOpen size={18} strokeWidth={1.75} className="text-info" aria-hidden="true" />
              আইন <span className="font-normal text-fg-3">{bnDigits(laws.length)}টি</span>
            </h2>
            {laws.map((item, i) => (
              <LawCard key={`${item.title}-${item.section}-${i}`} item={item} query={searchedQuery} />
            ))}
          </section>
        )}
        {!loading && mayHaveMore && (
          <div className="flex justify-center pb-2">
            <Button loading={loadingMore} onClick={() => void run({ q: searchedQuery, n: size + SEARCH_PAGE_SIZE })}>
              আরও দেখুন
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
