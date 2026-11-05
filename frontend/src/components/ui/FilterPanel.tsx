import * as Popover from "@radix-ui/react-popover";
import { SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { bnDigits } from "@/lib/format";
import { useIsDesktopNav } from "@/lib/hooks";
import { BottomSheet } from "./BottomSheet";
import { Button } from "./Button";
import { Select, type SelectOption } from "./Select";

export interface FilterField {
  key: string;
  label: string;
  noneLabel: string;
  options: readonly SelectOption[];
}
export type FilterValues = Record<string, string>;

export function countActive(values: FilterValues): number {
  return Object.values(values).filter(Boolean).length;
}

/** Filters edit a draft and take effect on "প্রয়োগ করুন": a popover on desktop, a bottom sheet on a phone. */
export function FilterPanel({
  title,
  fields,
  values,
  onApply,
  iconOnly = false,
}: {
  title: string;
  fields: readonly FilterField[];
  values: FilterValues;
  onApply: (values: FilterValues) => void;
  iconOnly?: boolean;
}) {
  const desktop = useIsDesktopNav();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<FilterValues>(values);
  const active = countActive(values);

  const toggle = (next: boolean) => {
    if (next) setDraft(values);
    setOpen(next);
  };
  const apply = (next: FilterValues) => {
    onApply(next);
    setOpen(false);
  };

  const trigger = (
    <button
      type="button"
      aria-label={active ? `${title}: ${bnDigits(active)}টি চালু` : title}
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={desktop ? undefined : () => toggle(true)}
      className={
        iconOnly
          ? "press relative inline-flex h-11 w-11 shrink-0 items-center justify-center rounded text-fg-2 hover:bg-field hover:text-fg-1"
          : "press relative inline-flex h-11 shrink-0 items-center gap-2 rounded border border-line-2 bg-raised px-3 text-14 font-medium text-fg-1 hover:border-line-strong hover:bg-field"
      }
    >
      <SlidersHorizontal size={18} strokeWidth={1.75} aria-hidden="true" />
      {!iconOnly && <span>{title}</span>}
      {active > 0 && (
        <span
          aria-hidden="true"
          className={
            iconOnly
              ? "absolute right-1 top-1 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-gold-500 px-1 text-12 font-semibold leading-none text-on-gold"
              : "flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-gold-500 px-1 text-12 font-semibold leading-none text-on-gold"
          }
        >
          {bnDigits(active)}
        </span>
      )}
    </button>
  );

  const body = (
    <div className="flex flex-col gap-3">
      {fields.map((field) => (
        <Select
          key={field.key}
          label={field.label}
          noneLabel={field.noneLabel}
          options={field.options}
          value={draft[field.key] ?? ""}
          onChange={(value) => setDraft((d) => ({ ...d, [field.key]: value }))}
        />
      ))}
    </div>
  );
  const footer = (
    <>
      <Button className="flex-1" onClick={() => apply({})}>
        সব মুছুন
      </Button>
      <Button className="flex-1" variant="primary" onClick={() => apply(draft)}>
        প্রয়োগ করুন
      </Button>
    </>
  );

  if (!desktop) {
    return (
      <>
        {trigger}
        {open && (
          <BottomSheet open={open} onOpenChange={toggle} title={title} footer={footer}>
            {body}
          </BottomSheet>
        )}
      </>
    );
  }
  return (
    <Popover.Root open={open} onOpenChange={toggle}>
      <Popover.Trigger asChild>{trigger}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={16}
          aria-label={title}
          className="popover-in z-sheet w-[320px] rounded-panel border border-line-2 bg-raised p-4 shadow-3"
        >
          <p className="mb-3 text-16 font-semibold text-fg-1">{title}</p>
          {body}
          <div className="mt-4 flex gap-2">{footer}</div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
