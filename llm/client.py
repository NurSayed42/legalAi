"""
llm/client.py
=============
এক জায়গা থেকে LLM call (CLAUDE.md নিয়ম ৫): retry, timeout, fallback, token আর খরচের log, prompt hash ধরে cache।

এখন evals আর Phase 3 এর পরীক্ষা এটা ব্যবহার করে। api_v3.py এর নিজের call_llm আগের মতোই আছে: production কোড বদলানো
হয়নি, API migration এর সিদ্ধান্ত আলাদা। Log এ prompt এর লেখা যায় না, শুধু hash।
"""

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
PRICING = Path(__file__).with_name("pricing.json")


class LLMResult(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached: bool = False
    attempts: int = 1
    latency_s: float = 0.0
    cost_usd: float | None = None  # দাম জানা না থাকলে None, শূন্য নয়
    prompt_hash: str = ""


class LLMError(RuntimeError):
    pass


class Provider(Protocol):
    name: str
    model: str

    def call(self, system: str, user: str, json_mode: bool, schema, temperature: float, timeout_s: float) -> tuple[str, int | None, int | None]: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        self.model = model
        self._key = api_key
        self._client = None

    def call(self, system, user, json_mode, schema, temperature, timeout_s):
        from google import genai
        from google.genai import types

        if self._client is None:
            self._client = genai.Client(api_key=self._key)
        config = types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json" if json_mode else "text/plain", response_schema=schema,
            temperature=temperature, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )
        r = self._client.models.generate_content(model=self.model, contents=user, config=config)
        usage = getattr(r, "usage_metadata", None)
        return r.text, getattr(usage, "prompt_token_count", None), getattr(usage, "candidates_token_count", None)


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str):
        self.model = model
        self._key = api_key
        self._client = None

    def call(self, system, user, json_mode, schema, temperature, timeout_s):
        from groq import Groq

        if self._client is None:
            self._client = Groq(api_key=self._key)
        r = self._client.chat.completions.create(
            model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=temperature, max_tokens=6000, timeout=timeout_s,
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
        )
        usage = getattr(r, "usage", None)
        return r.choices[0].message.content, getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)


class LLMClient:
    def __init__(self, providers: list[Provider], cache_path: Path | None = None, log_path: Path | None = None, timeout_s: float = 60.0, max_retries: int = 3, backoff_s: float = 2.0,
                 min_interval_s: float = 4.0, prices: dict | None = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        if not providers:
            raise LLMError("no LLM provider configured")
        self.providers = providers
        self.cache_path = cache_path
        self.log_path = log_path
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.min_interval_s = min_interval_s  # free tier: প্রতি model এ মিনিটে ১৫ টা request মানে ৪ সেকেন্ড
        self.prices = prices or {}
        self.sleep = sleep
        self.clock = clock
        self._last_call: dict[str, float] = {}
        self._cache: dict[str, dict] = {}
        if cache_path and cache_path.exists():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._cache[row["key"]] = row["result"]

    @staticmethod
    def key(provider: str, model: str, system: str, user: str, json_mode: bool, schema, temperature: float) -> str:
        schema_name = getattr(schema, "__name__", str(schema)) if schema is not None else ""
        raw = json.dumps([provider, model, system, user, json_mode, schema_name, temperature], ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cost(self, model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
        price = self.prices.get(model) or {}
        per_in, per_out = price.get("input_per_mtok"), price.get("output_per_mtok")
        if per_in is None or per_out is None or input_tokens is None or output_tokens is None:
            return None
        return (input_tokens * per_in + output_tokens * per_out) / 1_000_000

    def _append(self, path: Path | None, row: dict) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as fh:
            fh.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))

    def _wait_turn(self, name: str) -> None:
        last = self._last_call.get(name)
        if last is not None:
            gap = self.min_interval_s - (self.clock() - last)
            if gap > 0:
                self.sleep(gap)
        self._last_call[name] = self.clock()

    def complete(self, system: str, user: str, json_mode: bool = True, schema=None, temperature: float = 0.0, exclude: tuple[str, ...] = ()) -> LLMResult:
        """ক্রমে প্রতিটা provider, প্রতিটায় কয়েকবার। exclude: judge যেন generator এর provider না হয়।"""
        candidates = [p for p in self.providers if p.name not in exclude]
        if not candidates:
            raise LLMError(f"no provider left after excluding {exclude}")
        errors = []
        for provider in candidates:
            key = self.key(provider.name, provider.model, system, user, json_mode, schema, temperature)
            if key in self._cache:
                result = LLMResult(**{**self._cache[key], "cached": True, "cost_usd": None})
                self._append(self.log_path, {"prompt_hash": key, "provider": provider.name, "model": provider.model, "cached": True})
                return result
            for attempt in range(1, self.max_retries + 1):
                self._wait_turn(provider.name)
                started = self.clock()
                try:
                    text, tokens_in, tokens_out = provider.call(system, user, json_mode, schema, temperature, self.timeout_s)
                    if not text:
                        raise LLMError("empty response")
                except Exception as e:  # noqa: BLE001 সব provider এর error এক রকম নয়
                    errors.append(f"{provider.name}#{attempt}: {type(e).__name__}: {str(e)[:160]}")
                    self._append(self.log_path, {"prompt_hash": key, "provider": provider.name, "model": provider.model, "attempt": attempt, "error": type(e).__name__})
                    if attempt < self.max_retries:
                        self.sleep(self.backoff_s * 2 ** (attempt - 1))
                    continue
                result = LLMResult(text=text, provider=provider.name, model=provider.model, input_tokens=tokens_in, output_tokens=tokens_out, attempts=attempt,
                                   latency_s=round(self.clock() - started, 3), cost_usd=self._cost(provider.model, tokens_in, tokens_out), prompt_hash=key)
                self._cache[key] = result.model_dump()
                self._append(self.cache_path, {"key": key, "result": result.model_dump()})
                self._append(self.log_path, {"prompt_hash": key, "provider": provider.name, "model": provider.model, "attempt": attempt, "input_tokens": tokens_in,
                                             "output_tokens": tokens_out, "latency_s": result.latency_s, "cost_usd": result.cost_usd, "cached": False})
                return result
        raise LLMError("all providers failed: " + " | ".join(errors))


def load_prices(path: Path = PRICING) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def providers_from_env() -> list[Provider]:
    """api_v3 এর মতো: Gemini আগে, Groq fallback। Key এখানে শুধু পড়া হয়, কোথাও লেখা হয় না।"""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    out: list[Provider] = []
    if os.getenv("GEMINI_API_KEY"):
        out.append(GeminiProvider(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.5-flash")))
    if os.getenv("GROQ_API_KEY"):
        out.append(GroqProvider(os.environ["GROQ_API_KEY"], os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")))
    return out
