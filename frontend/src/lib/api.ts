import { API_BASE, NETWORK_RETRIES, RETRY_DELAY_MS, TIMEOUT_MS } from "./config";
import type {
  ChatRequest,
  ChatResponse,
  DocRequest,
  DocResponse,
  PredictRequest,
  PredictResponse,
  SearchRequest,
  SearchResponse,
  StatsResponse,
} from "./types";

export type ApiErrorKind =
  "offline" | "timeout" | "aborted" | "busy" | "rate_limited" | "bad_request" | "server" | "bad_response";

const MESSAGES: Record<ApiErrorKind, string> = {
  offline: "সার্ভারের সঙ্গে সংযোগ করা যাচ্ছে না। আপনার ইন্টারনেট সংযোগ দেখে আবার চেষ্টা করুন।",
  timeout: "উত্তর আসতে অনেক বেশি সময় লাগছে। একটু পরে আবার চেষ্টা করুন।",
  aborted: "অনুরোধটি থামানো হয়েছে।",
  busy: "এই মুহূর্তে উত্তর তৈরির সেবাটি ব্যস্ত। এক মিনিট পরে আবার চেষ্টা করুন।",
  rate_limited: "অল্প সময়ে অনেক অনুরোধ হয়েছে। এক মিনিট পরে আবার চেষ্টা করুন।",
  bad_request: "অনুরোধটি বোঝা যায়নি। লেখাটি দেখে আবার চেষ্টা করুন।",
  server: "সার্ভারে একটি সমস্যা হয়েছে। একটু পরে আবার চেষ্টা করুন।",
  bad_response: "সার্ভার থেকে অপ্রত্যাশিত উত্তর এসেছে। আবার চেষ্টা করুন।",
};

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | undefined;
  constructor(kind: ApiErrorKind, status?: number) {
    super(MESSAGES[kind]);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

export function toApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError("server");
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 429) return "rate_limited";
  if (status === 503 || status === 502 || status === 504) return "busy";
  if (status >= 400 && status < 500) return "bad_request";
  return "server";
}

interface Options {
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs: number;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function request<T>(path: string, { body, signal, timeoutMs }: Options): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const onAbort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener("abort", onAbort);

    try {
      const response = await fetch(`${API_BASE}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) throw new ApiError(kindForStatus(response.status), response.status);
      try {
        return (await response.json()) as T;
      } catch {
        throw new ApiError("bad_response", response.status);
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (timedOut) throw new ApiError("timeout");
      if (signal?.aborted) throw new ApiError("aborted");
      // fetch only rejects like this when no response arrived, so nothing was answered yet.
      if (attempt < NETWORK_RETRIES) {
        await wait(RETRY_DELAY_MS);
        if (signal?.aborted) throw new ApiError("aborted");
        continue;
      }
      throw new ApiError("offline");
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }
}

export const api = {
  stats: (signal?: AbortSignal) => request<StatsResponse>("/api/stats", { signal, timeoutMs: TIMEOUT_MS.stats }),
  chat: (body: ChatRequest, signal?: AbortSignal) =>
    request<ChatResponse>("/api/chat", { body, signal, timeoutMs: TIMEOUT_MS.chat }),
  predict: (body: PredictRequest, signal?: AbortSignal) =>
    request<PredictResponse>("/api/predict", { body, signal, timeoutMs: TIMEOUT_MS.predict }),
  search: (body: SearchRequest, signal?: AbortSignal) =>
    request<SearchResponse>("/api/search", { body, signal, timeoutMs: TIMEOUT_MS.search }),
  generateDoc: (body: DocRequest, signal?: AbortSignal) =>
    request<DocResponse>("/api/generate-doc", { body, signal, timeoutMs: TIMEOUT_MS.docs }),
};
