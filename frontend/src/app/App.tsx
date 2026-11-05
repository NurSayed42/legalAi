import * as Tabs from "@radix-ui/react-tabs";
import { WifiOff } from "lucide-react";
import { Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ErrorBoundary } from "@/components/layout/ErrorBoundary";
import { Header } from "@/components/layout/Header";
import { BottomNav, TabBar } from "@/components/layout/Navigation";
import { Skeleton } from "@/components/ui/Display";
import { ToastProvider } from "@/components/ui/Toast";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { useHashTab, useIsDesktopNav, useKeyboardInset, useOnline, type TabId } from "@/lib/hooks";
import { StatsProvider } from "./StatsProvider";
import { prefetchOthersWhenIdle, searchFocus, TAB_COMPONENTS, TABS } from "./tabs";

function TabFallback() {
  return (
    <div className="mx-auto flex w-full max-w-chat flex-col gap-3 py-8" aria-hidden="true">
      <Skeleton className="h-8 w-1/2" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-32 w-full rounded-card" />
    </div>
  );
}

export function App() {
  const [tab, setTab] = useHashTab();
  const desktop = useIsDesktopNav();
  const online = useOnline();
  // A tab loads when first opened and then stays mounted, so its conversation or results survive switching away.
  const [visited, setVisited] = useState<Set<TabId>>(() => new Set([tab]));
  useKeyboardInset();
  if (!visited.has(tab)) setVisited(new Set(visited).add(tab));

  // All tabs share the page's scroll, so each one's position is put back when you return to it.
  const scrollByTab = useRef<Partial<Record<TabId, number>>>({});
  const previousTab = useRef(tab);
  useLayoutEffect(() => {
    if (previousTab.current === tab) return;
    previousTab.current = tab;
    window.scrollTo({ top: scrollByTab.current[tab] ?? 0 });
  }, [tab]);
  useEffect(() => {
    const save = () => {
      scrollByTab.current[previousTab.current] = window.scrollY;
    };
    window.addEventListener("scroll", save, { passive: true });
    return () => window.removeEventListener("scroll", save);
  }, []);

  useEffect(() => {
    prefetchOthersWhenIdle(tab);
    // Only once, on first load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setTab("search");
        searchFocus.request();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setTab]);

  return (
    <StatsProvider>
      <TooltipProvider delayDuration={300}>
        <ToastProvider>
          <div className="grain relative flex min-h-dvh flex-col">
            <a
              href="#main"
              onClick={(event) => {
                event.preventDefault();
                document.getElementById("main")?.focus();
              }}
              className="fixed left-4 top-2 z-skip -translate-y-20 rounded bg-gold-500 px-4 py-2 text-14 font-medium text-on-gold no-underline focus-visible:translate-y-0"
            >
              মূল অংশে যান
            </a>
            <Tabs.Root
              value={tab}
              onValueChange={(value) => setTab(value as TabId)}
              activationMode="manual"
              className="relative z-[1] flex min-h-dvh flex-col"
            >
              <Header />
              {desktop && <TabBar active={tab} />}
              {!online && (
                <div
                  role="status"
                  className="flex items-center justify-center gap-2 bg-warning-tint px-4 py-2 text-14 text-fg-1"
                >
                  <WifiOff size={16} strokeWidth={1.75} className="text-warning" aria-hidden="true" />
                  আপনি এখন অফলাইনে আছেন। প্রশ্ন বা অনুসন্ধান করতে ইন্টারনেট লাগবে।
                </div>
              )}
              <main
                id="main"
                tabIndex={-1}
                className="mx-auto flex w-full max-w-content flex-1 flex-col px-4 md:px-6 lg:px-8"
              >
                {TABS.map(({ id }) => {
                  const Panel = TAB_COMPONENTS[id];
                  return (
                    <Tabs.Content
                      key={id}
                      value={id}
                      forceMount
                      tabIndex={-1}
                      className="flex-1 flex-col pt-6 data-[state=active]:flex data-[state=inactive]:hidden data-[state=active]:tab-panel-in md:pt-8"
                    >
                      {visited.has(id) && (
                        <ErrorBoundary>
                          <Suspense fallback={<TabFallback />}>
                            <Panel active={tab === id} />
                          </Suspense>
                        </ErrorBoundary>
                      )}
                    </Tabs.Content>
                  );
                })}
              </main>
              <footer className="no-print mx-auto w-full max-w-content px-4 pb-[calc(var(--nav-h)+env(safe-area-inset-bottom,0px)+16px)] pt-6 text-center text-14 text-fg-3 md:px-6 md:pb-6 lg:px-8">
                তথ্যের জন্য, আইনি পরামর্শের বিকল্প নয়। সূত্র মিলিয়ে দেখুন।
              </footer>
              {!desktop && <BottomNav />}
            </Tabs.Root>
          </div>
        </ToastProvider>
      </TooltipProvider>
    </StatsProvider>
  );
}
