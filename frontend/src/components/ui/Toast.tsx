import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { TOAST_MS } from "@/lib/config";
import { cn } from "@/lib/cn";

type Tone = "info" | "success" | "danger";
type ToastFn = (message: string, tone?: Tone) => void;
interface Item {
  id: number;
  message: string;
  tone: Tone;
}

const Context = createContext<ToastFn>(() => {});
export const useToast = () => useContext(Context);

const ICON = { info: Info, success: CircleCheck, danger: CircleAlert };
const TONE = { info: "text-info", success: "text-success", danger: "text-danger" };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const nextId = useRef(1);
  const dismiss = useCallback((id: number) => setItems((all) => all.filter((t) => t.id !== id)), []);
  const toast = useCallback<ToastFn>(
    (message, tone = "info") => {
      const id = nextId.current++;
      setItems((all) => [...all.slice(-2), { id, message, tone }]);
      setTimeout(() => dismiss(id), TOAST_MS);
    },
    [dismiss],
  );
  const value = useMemo(() => toast, [toast]);
  return (
    <Context.Provider value={value}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-[calc(var(--bottom-inset)+80px)] z-toast flex flex-col items-center gap-2 px-4 md:bottom-6"
      >
        {items.map((item) => {
          const Icon = ICON[item.tone];
          return (
            <div
              key={item.id}
              className="rise-in pointer-events-auto flex w-full max-w-[420px] items-center gap-2 rounded-card border border-line-2 bg-raised py-1 pl-3 pr-1 text-14 text-fg-1 shadow-3"
            >
              <Icon size={18} strokeWidth={1.75} className={cn(TONE[item.tone])} aria-hidden="true" />
              <span className="min-w-0 flex-1 py-1">{item.message}</span>
              <button
                type="button"
                aria-label="বন্ধ করুন"
                onClick={() => dismiss(item.id)}
                className="press inline-flex h-9 w-9 shrink-0 items-center justify-center rounded text-fg-2 hover:bg-field hover:text-fg-1"
              >
                <X size={16} strokeWidth={1.75} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </Context.Provider>
  );
}
