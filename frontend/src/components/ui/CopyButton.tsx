import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { copyToClipboard } from "@/lib/hooks";
import { Button, IconButton } from "./Button";
import { useToast } from "./Toast";

export function CopyButton({
  text,
  label = "কপি করুন",
  iconOnly = false,
  compact = false,
}: {
  text: string;
  label?: string;
  iconOnly?: boolean;
  compact?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const toast = useToast();
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    if (await copyToClipboard(text)) {
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } else {
      toast("কপি করা যায়নি। লেখাটি নির্বাচন করে কপি করুন।", "danger");
    }
  };
  const icon = copied ? (
    <Check size={16} strokeWidth={1.75} className="text-success" aria-hidden="true" />
  ) : (
    <Copy size={16} strokeWidth={1.75} aria-hidden="true" />
  );
  const shown = copied ? (compact ? "হয়েছে" : "কপি হয়েছে") : label;

  return iconOnly ? (
    <IconButton size="sm" label={shown} onClick={copy}>
      {icon}
    </IconButton>
  ) : (
    <Button
      size="sm"
      variant="ghost"
      className={compact ? "!px-1 md:!px-3" : undefined}
      aria-label={copied ? "কপি হয়েছে" : "কপি করুন"}
      icon={icon}
      onClick={copy}
    >
      {/* Reserves the longer label's width, so the button doesn't jump when it changes. */}
      {compact ? (
        <>
          <span className="inline-block min-w-[3em] text-left md:hidden">{shown}</span>
          <span className="hidden min-w-[5.5em] text-left md:inline-block">{copied ? "কপি হয়েছে" : "কপি করুন"}</span>
        </>
      ) : (
        <span className="inline-block min-w-[5.5em] text-left">{shown}</span>
      )}
    </Button>
  );
}
