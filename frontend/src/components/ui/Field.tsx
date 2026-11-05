import {
  forwardRef,
  useCallback,
  useLayoutEffect,
  useRef,
  type InputHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "@/lib/cn";

const FIELD =
  "w-full min-w-0 rounded border border-line-strong bg-field text-16 text-fg-1 transition hover:border-fg-3 focus-visible:border-transparent disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cn(FIELD, "h-11 px-3", className)} {...rest} />;
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Grows with its content up to this many lines, then scrolls. */
  maxLines?: number;
  bare?: boolean;
}

/** Text size stays at 16px so iOS doesn't zoom the page when the field takes focus. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { maxLines = 12, bare = false, className, value, rows = 1, ...rest },
  forwardedRef,
) {
  const inner = useRef<HTMLTextAreaElement | null>(null);
  const setRefs = useCallback(
    (node: HTMLTextAreaElement | null) => {
      inner.current = node;
      if (typeof forwardedRef === "function") forwardedRef(node);
      else if (forwardedRef) forwardedRef.current = node;
    },
    [forwardedRef],
  );

  useLayoutEffect(() => {
    const el = inner.current;
    if (!el) return;
    const style = getComputedStyle(el);
    const line = parseFloat(style.lineHeight) || 27;
    const chrome =
      parseFloat(style.paddingTop) +
      parseFloat(style.paddingBottom) +
      parseFloat(style.borderTopWidth) +
      parseFloat(style.borderBottomWidth);
    el.style.height = "auto";
    const height = Math.min(
      el.scrollHeight + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth),
      line * maxLines + chrome,
    );
    el.style.height = `${Math.max(height, line * rows + chrome)}px`;
    el.style.overflowY = el.scrollHeight + 2 > line * maxLines + chrome ? "auto" : "hidden";
  }, [value, maxLines, rows]);

  return (
    <textarea
      ref={setRefs}
      rows={rows}
      value={value}
      className={cn(
        bare ? "w-full min-w-0 resize-none bg-transparent text-16 text-fg-1" : cn(FIELD, "resize-none px-3 py-2"),
        className,
      )}
      {...rest}
    />
  );
});
