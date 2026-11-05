import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { StatsResponse } from "@/lib/types";

export type ConnectionStatus = "connecting" | "connected" | "unreachable";
interface Value {
  status: ConnectionStatus;
  stats: StatsResponse | null;
}

const Context = createContext<Value>({ status: "connecting", stats: null });
export const useStats = () => useContext(Context);

/** The status dot reflects a real answer from the API. The old UI said "Live" whatever happened. */
export function StatsProvider({ children }: { children: ReactNode }) {
  const [value, setValue] = useState<Value>({ status: "connecting", stats: null });
  useEffect(() => {
    let controller = new AbortController();
    const load = () => {
      controller.abort();
      controller = new AbortController();
      const { signal } = controller;
      api
        .stats(signal)
        .then((stats) => setValue({ status: "connected", stats }))
        .catch(() => {
          if (!signal.aborted) setValue((v) => ({ ...v, status: "unreachable" }));
        });
    };
    load();
    window.addEventListener("online", load);
    return () => {
      window.removeEventListener("online", load);
      controller.abort();
    };
  }, []);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
