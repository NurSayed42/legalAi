import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { BLUR_SETTLE_MS, HEADER_HIDE_AFTER_PX, KEYBOARD_THRESHOLD_PX } from "./config";

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (notify: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => list.removeEventListener("change", notify);
    },
    [query],
  );
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches);
}

export const useIsDesktopNav = () => useMediaQuery("(min-width: 768px)");
export const useCoarsePointer = () => useMediaQuery("(pointer: coarse)");

export type TabId = "qa" | "predict" | "search" | "docs";
const TAB_IDS: TabId[] = ["qa", "predict", "search", "docs"];

function tabFromHash(): TabId {
  const id = window.location.hash.replace(/^#/, "") as TabId;
  return TAB_IDS.includes(id) ? id : "qa";
}

/** The active tab lives in the URL hash, so back, refresh and deep links all work. */
export function useHashTab(): [TabId, (tab: TabId) => void] {
  const subscribe = useCallback((notify: () => void) => {
    window.addEventListener("hashchange", notify);
    return () => window.removeEventListener("hashchange", notify);
  }, []);
  const tab = useSyncExternalStore(subscribe, tabFromHash);
  const setTab = useCallback((next: TabId) => {
    if (next !== tabFromHash()) window.location.hash = next;
  }, []);
  return [tab, setTab];
}

export type Theme = "dark" | "light";
const THEME_KEY = "legalai.theme";

function readSaved(): Theme | null {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    return saved === "dark" || saved === "light" ? saved : null;
  } catch {
    return null;
  }
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const color = theme === "dark" ? "#0A0B0D" : "#FAF8F3";
  document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.setAttribute("content", color));
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(
    () => (document.documentElement.getAttribute("data-theme") as Theme) ?? "dark",
  );
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  useEffect(() => {
    const list = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = (event: MediaQueryListEvent) => {
      if (!readSaved()) setTheme(event.matches ? "dark" : "light");
    };
    list.addEventListener("change", follow);
    return () => list.removeEventListener("change", follow);
  }, []);
  const toggle = useCallback(() => {
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        /* a private window can refuse storage: the choice just lasts for this visit */
      }
      return next;
    });
  }, []);
  return { theme, toggle };
}

export function useOnline(): boolean {
  const subscribe = useCallback((notify: () => void) => {
    window.addEventListener("online", notify);
    window.addEventListener("offline", notify);
    return () => {
      window.removeEventListener("online", notify);
      window.removeEventListener("offline", notify);
    };
  }, []);
  return useSyncExternalStore(subscribe, () => navigator.onLine);
}

/** Mobile header: hides while scrolling down, returns on scroll up. Transform only, so nothing shifts. */
export function useHideOnScrollDown(enabled: boolean): boolean {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let last = window.scrollY;
    let frame = 0;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const y = window.scrollY;
        if (y < HEADER_HIDE_AFTER_PX) setHidden(false);
        else if (y > last + 6) setHidden(true);
        else if (y < last - 6) setHidden(false);
        last = y;
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
    };
  }, [enabled]);
  return enabled && hidden;
}

/**
 * Keeps the composer above the phone keyboard. Android resizes the page itself (the viewport meta asks it to),
 * and iOS doesn't, so there the gap is measured from the visual viewport and published as --kb.
 */
export function useKeyboardInset(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    const root = document.documentElement;
    // Leaving a field brings the bottom nav back, which moves the composer. The delay lets the tap that
    // caused the blur land first, or it would miss the button that just moved.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onFocus = (event: FocusEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches?.("textarea, input:not([type=checkbox]):not([type=radio])")) {
        clearTimeout(timer);
        root.dataset.typing = "true";
      }
    };
    const onBlur = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        delete root.dataset.typing;
      }, BLUR_SETTLE_MS);
    };
    document.addEventListener("focusin", onFocus);
    document.addEventListener("focusout", onBlur);
    if (!viewport) {
      return () => {
        document.removeEventListener("focusin", onFocus);
        document.removeEventListener("focusout", onBlur);
      };
    }
    const update = () => {
      const gap = window.innerHeight - viewport.height - viewport.offsetTop;
      root.style.setProperty("--kb", gap > KEYBOARD_THRESHOLD_PX ? `${Math.round(gap)}px` : "0px");
    };
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      document.removeEventListener("focusin", onFocus);
      document.removeEventListener("focusout", onBlur);
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
    };
  }, []);
}

/** An async action that can be stopped, ignores double submits and never sets state after unmounting. */
export function useAbortable() {
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const begin = useCallback(() => {
    controller.current?.abort();
    controller.current = new AbortController();
    return controller.current;
  }, []);
  const stop = useCallback(() => controller.current?.abort(), []);
  return { begin, stop };
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Older browsers, or a page not served over HTTPS.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}

export interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

/** Chrome offers install through this event. Without it, the menu item simply doesn't show. */
export function useInstallPrompt(): { canInstall: boolean; install: () => void } {
  const [event, setEvent] = useState<InstallPromptEvent | null>(null);
  useEffect(() => {
    const capture = (e: Event) => {
      e.preventDefault();
      setEvent(e as InstallPromptEvent);
    };
    const done = () => setEvent(null);
    window.addEventListener("beforeinstallprompt", capture);
    window.addEventListener("appinstalled", done);
    return () => {
      window.removeEventListener("beforeinstallprompt", capture);
      window.removeEventListener("appinstalled", done);
    };
  }, []);
  const install = useCallback(() => {
    if (!event) return;
    void event.prompt().finally(() => setEvent(null));
  }, [event]);
  return { canInstall: Boolean(event), install };
}
