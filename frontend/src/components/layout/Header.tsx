import * as Popover from "@radix-ui/react-popover";
import { BookOpen, Download, EllipsisVertical, Info, Landmark, Moon, Scale, Sun } from "lucide-react";
import { useState } from "react";
import { useStats, type ConnectionStatus } from "@/app/StatsProvider";
import { BottomSheet, prefetchBottomSheet } from "@/components/ui/BottomSheet";
import { Button, IconButton } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { cn } from "@/lib/cn";
import { bnNumber } from "@/lib/format";
import { useHideOnScrollDown, useInstallPrompt, useIsDesktopNav, useTheme } from "@/lib/hooks";

/** The launcher adds this tag when it serves a test copy. A production build has none, so no pill appears. */
const INSTANCE = document.querySelector('meta[name="legalai-instance"]')?.getAttribute("content") ?? null;

const STATUS: Record<ConnectionStatus, { label: string; dot: string }> = {
  connecting: { label: "সংযোগ হচ্ছে", dot: "bg-fg-3" },
  connected: { label: "সংযুক্ত", dot: "bg-success" },
  unreachable: { label: "সংযোগ নেই", dot: "bg-danger" },
};

function StatusDot({ withLabel = false }: { withLabel?: boolean }) {
  const { status } = useStats();
  const info = STATUS[status];
  return (
    <span className="inline-flex items-center gap-2 text-14 text-fg-2" role="status" aria-live="polite">
      <span aria-hidden="true" className={cn("h-2 w-2 rounded-full", info.dot)} />
      <span className={withLabel ? undefined : "sr-only"}>{info.label}</span>
    </span>
  );
}

export function BrandMark({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const box = { sm: "h-8 w-8", md: "h-9 w-9", lg: "h-14 w-14 rounded-panel" }[size];
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex shrink-0 items-center justify-center rounded border border-gold-600 bg-gold-tint text-accent",
        box,
      )}
    >
      <Scale size={size === "lg" ? 28 : 18} strokeWidth={1.75} />
    </span>
  );
}

function StatsList() {
  const { stats } = useStats();
  const cases = stats?.judgements?.estimated_cases;
  const acts = stats?.laws?.total_acts;
  return (
    <div className="flex flex-col gap-3">
      <dl className="m-0 flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <dt className="flex items-center gap-2 text-14 text-fg-2">
            <Landmark size={16} strokeWidth={1.75} aria-hidden="true" /> রায়
          </dt>
          <dd className="m-0 text-16 font-semibold text-accent">{cases ? bnNumber(cases) : "—"}</dd>
        </div>
        <div className="flex items-center justify-between gap-4">
          <dt className="flex items-center gap-2 text-14 text-fg-2">
            <BookOpen size={16} strokeWidth={1.75} aria-hidden="true" /> আইন
          </dt>
          <dd className="m-0 text-16 font-semibold text-accent">{acts ? bnNumber(acts) : "—"}</dd>
        </div>
      </dl>
      <p className="border-t border-line-1 pt-3 text-14 text-fg-2">
        শুধু হাইকোর্ট বিভাগের রায়। সংগ্রহের বেশিরভাগ রায় ২০১০ সালের পরের।
      </p>
    </div>
  );
}

function AboutContent() {
  return (
    <div className="flex flex-col gap-3">
      <p>
        এই অ্যাপটি বাংলাদেশের হাইকোর্ট বিভাগের রায় ও প্রচলিত আইন খুঁজে আপনার প্রশ্নের সঙ্গে প্রাসঙ্গিক তথ্য দেখায়।
      </p>
      <p>
        উত্তর কৃত্রিম বুদ্ধিমত্তায় তৈরি, তাই ভুল থাকতে পারে। প্রতিটি সূত্র মিলিয়ে দেখুন। এটি আইনি পরামর্শ নয়;
        গুরুত্বপূর্ণ সিদ্ধান্তের আগে একজন আইনজীবীর সঙ্গে কথা বলুন।
      </p>
      <p>
        আইনের উৎস: <span lang="en">bdlaws.minlaw.gov.bd</span>
      </p>
    </div>
  );
}

function OverflowMenu() {
  const [open, setOpen] = useState(false);
  const [about, setAbout] = useState(false);
  const { theme, toggle } = useTheme();
  const { canInstall, install } = useInstallPrompt();
  return (
    <>
      <IconButton
        label="আরও"
        showTooltip={false}
        onPointerEnter={prefetchBottomSheet}
        onFocus={prefetchBottomSheet}
        onClick={() => setOpen(true)}
      >
        <EllipsisVertical size={20} strokeWidth={1.75} aria-hidden="true" />
      </IconButton>
      {open && (
        <BottomSheet open={open} onOpenChange={setOpen} title="আরও">
          <div className="flex flex-col gap-4">
            <StatsList />
            <div className="flex items-center justify-between border-t border-line-1 pt-3">
              <span className="text-14 text-fg-2">সংযোগ</span>
              <StatusDot withLabel />
            </div>
            {INSTANCE && (
              <p className="rounded border border-line-2 px-3 py-2 text-14 text-fg-2">
                এটি পরীক্ষার কপি, চালু সিস্টেম নয়।
              </p>
            )}
            <div className="flex flex-col gap-1">
              <Button
                variant="ghost"
                align="start"
                icon={
                  theme === "dark" ? (
                    <Sun size={18} strokeWidth={1.75} aria-hidden="true" />
                  ) : (
                    <Moon size={18} strokeWidth={1.75} aria-hidden="true" />
                  )
                }
                onClick={toggle}
              >
                {theme === "dark" ? "হালকা থিম" : "গাঢ় থিম"}
              </Button>
              <Button
                variant="ghost"
                align="start"
                icon={<Info size={18} strokeWidth={1.75} aria-hidden="true" />}
                onClick={() => {
                  setOpen(false);
                  setAbout(true);
                }}
              >
                এই অ্যাপ সম্পর্কে
              </Button>
              {canInstall && (
                <Button
                  variant="ghost"
                  align="start"
                  icon={<Download size={18} strokeWidth={1.75} aria-hidden="true" />}
                  onClick={install}
                >
                  অ্যাপ হিসেবে ইনস্টল করুন
                </Button>
              )}
            </div>
          </div>
        </BottomSheet>
      )}
      <Dialog open={about} onOpenChange={setAbout} title="এই অ্যাপ সম্পর্কে">
        <AboutContent />
      </Dialog>
    </>
  );
}

/* The last icon's 44px hit area hangs past the content edge, so the glyph itself lines up with the column. */
function DesktopActions() {
  const { theme, toggle } = useTheme();
  const { canInstall, install } = useInstallPrompt();
  const [about, setAbout] = useState(false);
  const { stats } = useStats();
  const cases = stats?.judgements?.estimated_cases;
  return (
    <div className="-mr-3 flex items-center gap-1">
      {INSTANCE && (
        <span
          className="mr-2 hidden rounded-full border border-line-2 px-3 py-0.5 text-14 text-fg-2 xl:inline-block"
          title="চালু সিস্টেম নয়। প্রশ্ন করলে আসল উত্তর তৈরি হয়।"
        >
          পরীক্ষার কপি
        </span>
      )}
      <span className="mr-2">
        <StatusDot withLabel />
      </span>
      <Popover.Root>
        <Popover.Trigger asChild>
          <Button variant="ghost" size="sm" icon={<Landmark size={16} strokeWidth={1.75} aria-hidden="true" />}>
            {/* Fixed width, so the header doesn't move when the numbers arrive. */}
            <span className="inline-block min-w-[3.25em] text-left">{cases ? `${bnNumber(cases)}` : "—"}</span>
            <span className="sr-only">টি রায়। সংগ্রহের বিবরণ দেখুন</span>
          </Button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            align="end"
            sideOffset={8}
            collisionPadding={16}
            aria-label="সংগ্রহের বিবরণ"
            className="popover-in z-sheet w-[280px] rounded-panel border border-line-2 bg-raised p-4 shadow-3"
          >
            <StatsList />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      <IconButton label="এই অ্যাপ সম্পর্কে" onClick={() => setAbout(true)}>
        <Info size={18} strokeWidth={1.75} aria-hidden="true" />
      </IconButton>
      {canInstall && (
        <IconButton label="অ্যাপ হিসেবে ইনস্টল করুন" onClick={install}>
          <Download size={18} strokeWidth={1.75} aria-hidden="true" />
        </IconButton>
      )}
      <IconButton label={theme === "dark" ? "হালকা থিম" : "গাঢ় থিম"} onClick={toggle}>
        {theme === "dark" ? (
          <Sun size={18} strokeWidth={1.75} aria-hidden="true" />
        ) : (
          <Moon size={18} strokeWidth={1.75} aria-hidden="true" />
        )}
      </IconButton>
      <Dialog open={about} onOpenChange={setAbout} title="এই অ্যাপ সম্পর্কে">
        <AboutContent />
      </Dialog>
    </div>
  );
}

export function Header() {
  const desktop = useIsDesktopNav();
  const hidden = useHideOnScrollDown(!desktop);
  return (
    <header
      className={cn(
        "glass sticky top-0 z-header h-[var(--header-h)] border-b border-line-1 transition-transform duration-slow",
        hidden && "header-hidden",
      )}
    >
      <div className="mx-auto flex h-full w-full max-w-content items-center justify-between gap-2 px-4 md:px-6 lg:px-8">
        <a href="#qa" className="flex min-w-0 items-center gap-2 rounded no-underline">
          <BrandMark size={desktop ? "md" : "sm"} />
          <span className="min-w-0">
            <span
              lang="en"
              className="block truncate font-serif text-18 font-semibold leading-tight text-fg-1 md:text-20"
            >
              LegalAI Bangladesh
            </span>
            <span className="hidden text-14 leading-tight text-fg-3 lg:block">
              হাইকোর্ট বিভাগের রায় ও বাংলাদেশের আইন
            </span>
          </span>
        </a>
        {desktop ? (
          <DesktopActions />
        ) : (
          <div className="-mr-3 flex items-center gap-1">
            <StatusDot />
            <OverflowMenu />
          </div>
        )}
      </div>
    </header>
  );
}
