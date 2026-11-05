import * as ToggleGroup from "@radix-ui/react-toggle-group";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface Option<T extends string> {
  value: T;
  label: string;
}

/** One choice out of a few, always set. Radix reports "" when the active item is pressed again, which is ignored. */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: T;
  options: readonly Option<T>[];
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <ToggleGroup.Root
      type="single"
      aria-label={label}
      value={value}
      onValueChange={(next) => next && onChange(next as T)}
      className={cn("inline-flex min-w-0 gap-1 rounded border border-line-2 bg-surface p-1", className)}
    >
      {options.map((option) => (
        <ToggleGroup.Item
          key={option.value}
          value={option.value}
          className="press hit-target relative h-9 flex-1 whitespace-nowrap rounded-sm px-3 text-14 font-medium text-fg-2 hover:text-fg-1 data-[state=on]:bg-gold-tint data-[state=on]:text-accent"
        >
          {option.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

/** A row of single-choice chips that scrolls sideways on a phone. */
export function ChipGroup<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly Option<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <ToggleGroup.Root
      type="single"
      aria-label={label}
      value={value}
      onValueChange={(next) => next && onChange(next as T)}
      className="no-scrollbar -mx-4 flex snap-x gap-2 overflow-x-auto px-4 py-1 md:mx-0 md:flex-wrap md:overflow-visible md:px-0"
    >
      {options.map((option) => (
        <ToggleGroup.Item
          key={option.value}
          value={option.value}
          className="press hit-target relative h-9 shrink-0 snap-start whitespace-nowrap rounded-full border border-line-strong px-3 text-14 text-fg-2 hover:border-fg-3 hover:text-fg-1 data-[state=on]:border-gold-500 data-[state=on]:bg-gold-tint data-[state=on]:text-accent"
        >
          {option.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

export function FilterChip({
  children,
  onRemove,
  removeLabel,
}: {
  children: ReactNode;
  onRemove: () => void;
  removeLabel: string;
}) {
  return (
    <span className="inline-flex h-9 max-w-full items-center gap-1 rounded-full border border-gold-500 bg-gold-tint pl-3 pr-1 text-14 text-accent">
      <span className="truncate">{children}</span>
      <button
        type="button"
        aria-label={removeLabel}
        onClick={onRemove}
        className="press hit-target relative inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full hover:bg-gold-tint"
      >
        <X size={16} strokeWidth={1.75} aria-hidden="true" />
      </button>
    </span>
  );
}

export function Chip({
  children,
  selected = false,
  onClick,
  className,
}: {
  children: ReactNode;
  selected?: boolean;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "press hit-target relative inline-flex min-h-9 max-w-full items-center rounded-full border px-3 py-1 text-left text-14 leading-snug",
        selected
          ? "border-gold-500 bg-gold-tint text-accent"
          : "border-line-strong text-fg-2 hover:border-fg-3 hover:text-fg-1",
        className,
      )}
    >
      {children}
    </button>
  );
}
