import type { Answer, ChatMessage, Filters } from "./types.ts";

export type Turn =
  | { role: "user"; text: string }
  | { role: "ai"; status: "done"; answer: Answer | string }
  | { role: "ai"; status: "failed" | "loading" | "stopped" };

/**
 * The history sent with a question. Same shape and same last-six window as the old UI.
 * One deliberate difference (D-212): a turn that failed is left out, along with the question that caused it.
 * The old UI sent its own "❌" error text to the model as if the assistant had said it.
 */
export function buildHistory(turns: Turn[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  turns.forEach((turn, index) => {
    if (turn.role === "user") {
      const next = turns[index + 1];
      if (next?.role === "ai" && next.status === "done") out.push({ role: "user", content: turn.text });
    } else if (turn.status === "done") {
      out.push({ role: "assistant", content: JSON.stringify(turn.answer) });
    }
  });
  return out.slice(-6);
}

/** Same rule as the old UI: an empty filter set is sent as null. */
export function filtersOrNull(filters: Filters): Filters | null {
  return Object.keys(filters).length ? filters : null;
}
