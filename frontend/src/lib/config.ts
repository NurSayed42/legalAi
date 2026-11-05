/**
 * Every threshold here is a judgement call, kept in one place (CLAUDE.md rule 9, logged in docs/DECISIONS.md D-212).
 * None of them changes what is sent to the API.
 */

export const API_BASE: string = (import.meta.env?.VITE_API_BASE as string | undefined) ?? "";

/** Answers took about a minute in testing and take longer when an LLM provider is busy. */
export const TIMEOUT_MS = {
  stats: 15_000,
  search: 60_000,
  chat: 300_000,
  predict: 300_000,
  docs: 180_000,
} as const;

/** Retried only when the request never reached a server, so an answer isn't paid for twice. */
export const NETWORK_RETRIES = 1;
export const RETRY_DELAY_MS = 800;

export const COMPOSER_MAX_CHARS = 2000;
export const COMPOSER_HINT_FROM = 0.8;
export const COMPOSER_MAX_LINES = 6;

/** Sent exactly as the old UI sent it. */
export const SEARCH_PAGE_SIZE = 8;
/** Show a reassurance after this long without an answer. */
export const SLOW_ANSWER_AFTER_MS = 20_000;
export const SEARCH_HISTORY_DEBOUNCE_MS = 400;

/** Below this many decided cases, the outcome bar is labelled as too small a sample to read much into. */
export const SMALL_SAMPLE_BELOW = 10;

export const TOAST_MS = 4000;
export const KEYBOARD_THRESHOLD_PX = 120;
export const HEADER_HIDE_AFTER_PX = 80;
export const BLUR_SETTLE_MS = 180;
