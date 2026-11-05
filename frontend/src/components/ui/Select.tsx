import * as Primitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

/** Radix doesn't allow an empty value on an item, so "no filter" has a stand-in here that never reaches the API. */
const NONE = "__none__";

export interface SelectOption {
  value: string;
  label: string;
}

interface Props {
  label: string;
  value: string;
  options: readonly SelectOption[];
  noneLabel: string;
  onChange: (value: string) => void;
  hideLabel?: boolean;
  className?: string;
}

export function Select({ label, value, options, noneLabel, onChange, hideLabel = false, className }: Props) {
  const active = value !== "";
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      {!hideLabel && <span className="text-14 font-medium text-fg-2">{label}</span>}
      <Primitive.Root value={value || NONE} onValueChange={(next) => onChange(next === NONE ? "" : next)}>
        <Primitive.Trigger
          aria-label={label}
          className={cn(
            "press inline-flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded border bg-field px-3 text-14 hover:border-fg-3",
            active ? "border-gold-500 text-accent" : "border-line-strong text-fg-1",
          )}
        >
          <span className="truncate">
            <Primitive.Value />
          </span>
          <Primitive.Icon>
            <ChevronDown size={16} strokeWidth={1.75} aria-hidden="true" />
          </Primitive.Icon>
        </Primitive.Trigger>
        <Primitive.Portal>
          <Primitive.Content
            position="popper"
            sideOffset={6}
            collisionPadding={8}
            className="popover-in z-toast max-h-[min(320px,var(--radix-select-content-available-height))] min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-card border border-line-2 bg-raised shadow-3"
          >
            <Primitive.Viewport className="p-1">
              {[{ value: NONE, label: noneLabel }, ...options].map((option) => (
                <Primitive.Item
                  key={option.value}
                  value={option.value}
                  className="relative flex min-h-11 cursor-pointer select-none items-center rounded py-1 pl-3 pr-8 text-14 text-fg-1 outline-none data-[highlighted]:bg-field data-[state=checked]:text-accent"
                >
                  <Primitive.ItemText>{option.label}</Primitive.ItemText>
                  <Primitive.ItemIndicator className="absolute right-2">
                    <Check size={16} strokeWidth={1.75} aria-hidden="true" />
                  </Primitive.ItemIndicator>
                </Primitive.Item>
              ))}
            </Primitive.Viewport>
          </Primitive.Content>
        </Primitive.Portal>
      </Primitive.Root>
    </div>
  );
}
