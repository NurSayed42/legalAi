import { FilePenLine, Gavel, MessageSquareText, Search, type LucideIcon } from "lucide-react";
import { lazy } from "react";
import type { TabId } from "@/lib/hooks";

const loaders = {
  qa: () => import("@/features/chat/ChatTab"),
  predict: () => import("@/features/predict/PredictTab"),
  search: () => import("@/features/search/SearchTab"),
  docs: () => import("@/features/docs/DocsTab"),
} satisfies Record<TabId, () => Promise<unknown>>;

export const TAB_COMPONENTS = {
  qa: lazy(loaders.qa),
  predict: lazy(loaders.predict),
  search: lazy(loaders.search),
  docs: lazy(loaders.docs),
};

export interface TabInfo {
  id: TabId;
  label: string;
  short: string;
  icon: LucideIcon;
}

export const TABS: readonly TabInfo[] = [
  { id: "qa", label: "আইনি প্রশ্নোত্তর", short: "প্রশ্ন", icon: MessageSquareText },
  { id: "predict", label: "সম্ভাব্য ফলাফল", short: "ফলাফল", icon: Gavel },
  { id: "search", label: "মামলা ও আইন", short: "খুঁজুন", icon: Search },
  { id: "docs", label: "দলিল তৈরি", short: "দলিল", icon: FilePenLine },
];

export function prefetchTab(id: TabId) {
  void loaders[id]().catch(() => {});
}

interface SlowConnection {
  saveData?: boolean;
  effectiveType?: string;
}

/** Fetches the other tabs when the browser is idle, unless the person is on a slow or metered connection. */
export function prefetchOthersWhenIdle(current: TabId) {
  const connection = (navigator as Navigator & { connection?: SlowConnection }).connection;
  if (connection?.saveData || connection?.effectiveType?.includes("2g")) return;
  const run = () => TABS.forEach((t) => t.id !== current && prefetchTab(t.id));
  if ("requestIdleCallback" in window) window.requestIdleCallback(run, { timeout: 4000 });
  else setTimeout(run, 2500);
}

/** Set by the Ctrl+K shortcut. The search tab reads it when it appears, however long its chunk took to load. */
export const searchFocus = {
  pending: false,
  listener: null as (() => void) | null,
  request() {
    this.pending = true;
    this.listener?.();
  },
};
