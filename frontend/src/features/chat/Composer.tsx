import { SendHorizontal, Square } from "lucide-react";
import { forwardRef, type FormEvent, type KeyboardEvent } from "react";
import { Textarea } from "@/components/ui/Field";
import { FilterPanel, type FilterField, type FilterValues } from "@/components/ui/FilterPanel";
import { cn } from "@/lib/cn";
import { COMPOSER_HINT_FROM, COMPOSER_MAX_CHARS, COMPOSER_MAX_LINES } from "@/lib/config";
import { bnDigits } from "@/lib/format";
import { useCoarsePointer } from "@/lib/hooks";
import { COURTS, CASE_TYPES, YEARS } from "@/lib/labels";

const FIELDS: readonly FilterField[] = [
  { key: "case_type", label: "মামলার ধরন", noneLabel: "সব ধরনের মামলা", options: CASE_TYPES },
  { key: "division", label: "আদালত", noneLabel: "সব আদালত", options: COURTS },
  { key: "year_from", label: "বছর", noneLabel: "সব বছর", options: YEARS },
];

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  filters: FilterValues;
  onFilters: (values: FilterValues) => void;
}

export const Composer = forwardRef<HTMLTextAreaElement, Props>(function Composer(
  { value, onChange, onSend, onStop, busy, filters, onFilters },
  ref,
) {
  const coarse = useCoarsePointer();
  const nearLimit = value.length >= COMPOSER_MAX_CHARS * COMPOSER_HINT_FROM;
  const canSend = value.trim().length > 0 && !busy;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (busy) {
      onStop();
    } else if (canSend) {
      onSend();
      // On a phone, close the keyboard so the answer can be read.
      if (coarse) (document.activeElement as HTMLElement | null)?.blur();
    }
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // A touch keyboard has no Shift, so Enter makes a new line there and the button sends.
    // isComposing: Bangla input methods use Enter to confirm a word, which must not send the message.
    if (event.key === "Enter" && !event.shiftKey && !coarse && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (canSend) onSend();
    }
  };

  return (
    <form
      onSubmit={onSubmit}
      className="no-print sticky bottom-[calc(var(--bottom-inset)+8px)] z-[5] mt-4 md:bottom-4"
      aria-label="প্রশ্ন লিখুন"
    >
      <div className="glass flex items-end gap-1 rounded-panel border border-line-2 p-1.5 shadow-2 focus-within:border-gold-500">
        <FilterPanel iconOnly title="ফিল্টার" fields={FIELDS} values={filters} onApply={onFilters} />
        <div className="min-w-0 flex-1 self-center">
          <label htmlFor="chat-input" className="sr-only">
            আপনার আইনি প্রশ্ন
          </label>
          <Textarea
            id="chat-input"
            ref={ref}
            bare
            value={value}
            maxLines={COMPOSER_MAX_LINES}
            maxLength={COMPOSER_MAX_CHARS}
            enterKeyHint={coarse ? "enter" : "send"}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="আপনার প্রশ্ন লিখুন…"
            className="block px-1 py-[9px] leading-[26px] focus-visible:outline-none"
          />
          {nearLimit && (
            <p className="px-1 pb-1 text-14 text-fg-3" aria-live="polite">
              {bnDigits(value.length)} / {bnDigits(COMPOSER_MAX_CHARS)} অক্ষর
            </p>
          )}
        </div>
        <button
          type="submit"
          aria-label={busy ? "উত্তর তৈরি থামান" : "পাঠান"}
          disabled={!busy && !canSend}
          // Keeps focus in the text box, so the keyboard and layout don't move mid-tap.
          onMouseDown={(event) => event.preventDefault()}
          className={cn(
            "press inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-card disabled:opacity-50",
            busy
              ? "border border-line-strong bg-raised text-fg-1 hover:bg-field"
              : "bg-gold-500 text-on-gold hover:bg-gold-400 disabled:hover:bg-gold-500",
          )}
        >
          {busy ? (
            <Square size={16} strokeWidth={1.75} fill="currentColor" aria-hidden="true" />
          ) : (
            <SendHorizontal size={20} strokeWidth={1.75} aria-hidden="true" />
          )}
        </button>
      </div>
    </form>
  );
});
