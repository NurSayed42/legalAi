import * as Tabs from "@radix-ui/react-tabs";
import { useLayoutEffect, useRef, useState } from "react";
import { prefetchTab, TABS } from "@/app/tabs";
import { cn } from "@/lib/cn";
import type { TabId } from "@/lib/hooks";

/** Desktop and tablet: a tab bar with a gold underline that slides between tabs. */
export function TabBar({ active }: { active: TabId }) {
  const list = useRef<HTMLDivElement>(null);
  const [bar, setBar] = useState({ x: 0, w: 0 });

  useLayoutEffect(() => {
    const node = list.current;
    if (!node) return;
    const measure = () => {
      const trigger = node.querySelector<HTMLElement>('[data-state="active"]');
      if (trigger) setBar({ x: trigger.offsetLeft, w: trigger.offsetWidth });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    document.fonts?.ready.then(measure).catch(() => {});
    return () => observer.disconnect();
  }, [active]);

  return (
    <nav aria-label="প্রধান" className="glass sticky top-[var(--header-h)] z-nav border-b border-line-1">
      <div className="mx-auto max-w-content px-6 lg:px-8">
        <Tabs.List ref={list} aria-label="বিভাগ" className="relative -ml-3 flex gap-1">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <Tabs.Trigger
                key={tab.id}
                value={tab.id}
                onPointerEnter={() => prefetchTab(tab.id)}
                onFocus={() => prefetchTab(tab.id)}
                className="press inline-flex h-12 items-center gap-2 rounded-t px-3 text-14 font-medium text-fg-2 hover:text-fg-1 focus-visible:-outline-offset-2 data-[state=active]:text-accent lg:px-4"
              >
                <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
                {tab.label}
              </Tabs.Trigger>
            );
          })}
          <span
            aria-hidden="true"
            className="absolute bottom-0 left-0 h-0.5 rounded-full bg-gold-500 transition-[transform,width] duration-slow"
            style={{ width: bar.w, transform: `translateX(${bar.x}px)` }}
          />
        </Tabs.List>
      </div>
    </nav>
  );
}

/** Phone: a fixed bottom nav. It steps aside while the keyboard is up (data-typing, in tokens.css and below). */
export function BottomNav() {
  return (
    <nav
      aria-label="প্রধান"
      className="glass fixed inset-x-0 bottom-0 z-nav border-t border-line-1 pb-[env(safe-area-inset-bottom,0px)] transition-transform duration-fast [[data-typing=true]_&]:translate-y-full"
    >
      <Tabs.List aria-label="বিভাগ" className="flex h-16">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <Tabs.Trigger
              key={tab.id}
              value={tab.id}
              onTouchStart={() => prefetchTab(tab.id)}
              className={cn(
                "press group/nav flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-14 leading-tight text-fg-2 focus-visible:-outline-offset-4 data-[state=active]:text-accent",
              )}
            >
              <span className="flex h-7 w-14 items-center justify-center rounded-full transition group-data-[state=active]/nav:bg-gold-tint">
                <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
              </span>
              <span className="truncate">{tab.short}</span>
            </Tabs.Trigger>
          );
        })}
      </Tabs.List>
    </nav>
  );
}
