"""LLM wrapper, নকল provider দিয়ে। Network নেই, key নেই।"""

import json

import pytest

from llm.client import LLMClient, LLMError


class FakeProvider:
    def __init__(self, name="gemini", model="m1", replies=None):
        self.name, self.model = name, model
        self.replies = list(replies or [])
        self.calls = 0

    def call(self, system, user, json_mode, schema, temperature, timeout_s):
        self.calls += 1
        self.last = (system, user, json_mode, schema, temperature, timeout_s)
        reply = self.replies.pop(0) if self.replies else "ok"
        if isinstance(reply, Exception):
            raise reply
        return reply, 10, 5


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def client(providers, tmp_path=None, **kw):
    clock = kw.pop("clock", Clock())
    defaults = {"min_interval_s": 0.0, "backoff_s": 1.0, "max_retries": 3, "sleep": clock.sleep, "clock": clock.time}
    if tmp_path is not None:
        defaults.update(cache_path=tmp_path / "cache.jsonl", log_path=tmp_path / "log.jsonl")
    return LLMClient(providers, **{**defaults, **kw})


def test_returns_text_tokens_and_provider():
    got = client([FakeProvider(replies=["hello"])]).complete("s", "u")
    assert (got.text, got.provider, got.input_tokens, got.output_tokens, got.cached, got.attempts) == ("hello", "gemini", 10, 5, False, 1)


def test_retries_then_succeeds_with_backoff():
    clock = Clock()
    p = FakeProvider(replies=[RuntimeError("503"), RuntimeError("503"), "fine"])
    got = client([p], clock=clock).complete("s", "u")
    assert (got.text, got.attempts, p.calls) == ("fine", 3, 3)
    assert clock.sleeps == [1.0, 2.0]


def test_falls_back_to_the_next_provider():
    first = FakeProvider(replies=[RuntimeError("x")] * 3)
    second = FakeProvider(name="groq", model="m2", replies=["from groq"])
    got = client([first, second]).complete("s", "u")
    assert (got.provider, got.text, first.calls) == ("groq", "from groq", 3)


def test_all_failing_raises_with_every_error():
    with pytest.raises(LLMError, match="all providers failed"):
        client([FakeProvider(replies=[RuntimeError("boom")] * 3)]).complete("s", "u")


def test_empty_response_counts_as_a_failure():
    got = client([FakeProvider(replies=["", "real"])]).complete("s", "u")
    assert got.text == "real" and got.attempts == 2


def test_exclude_keeps_the_judge_off_the_generator_provider():
    gemini, groq = FakeProvider(), FakeProvider(name="groq", model="m2")
    got = client([gemini, groq]).complete("s", "u", exclude=("gemini",))
    assert got.provider == "groq" and gemini.calls == 0


def test_excluding_everything_is_an_error():
    with pytest.raises(LLMError, match="no provider left"):
        client([FakeProvider()]).complete("s", "u", exclude=("gemini",))


def test_cache_survives_restart_and_skips_the_provider(tmp_path):
    p = FakeProvider(replies=["first"])
    client([p], tmp_path).complete("s", "u")
    again = FakeProvider(replies=["second"])
    got = client([again], tmp_path).complete("s", "u")
    assert (got.text, got.cached, again.calls) == ("first", True, 0)


def test_cache_key_changes_with_prompt_model_and_temperature():
    base = LLMClient.key("p", "m", "s", "u", True, None, 0.0)
    assert base != LLMClient.key("p", "m2", "s", "u", True, None, 0.0)
    assert base != LLMClient.key("p", "m", "s", "other", True, None, 0.0)
    assert base != LLMClient.key("p", "m", "s", "u", True, None, 0.1)


def test_rate_limit_waits_between_calls_to_one_provider():
    clock = Clock()
    c = client([FakeProvider()], clock=clock, min_interval_s=4.0)
    c.complete("s", "one")
    c.complete("s", "two")
    assert clock.sleeps == [4.0]


def test_log_has_tokens_but_never_the_prompt_text(tmp_path):
    client([FakeProvider(replies=["answer text"])], tmp_path).complete("SECRET SYSTEM", "SECRET USER")
    log = (tmp_path / "log.jsonl").read_text(encoding="utf-8")
    assert "SECRET" not in log and "answer text" not in log
    row = json.loads(log.splitlines()[0])
    assert (row["input_tokens"], row["output_tokens"], row["provider"]) == (10, 5, "gemini")


def test_unknown_price_is_none_not_zero():
    assert client([FakeProvider()]).complete("s", "u").cost_usd is None
    assert client([FakeProvider()], prices={"m1": {"input_per_mtok": None, "output_per_mtok": None}}).complete("s", "u").cost_usd is None


def test_known_price_gives_a_cost():
    got = client([FakeProvider()], prices={"m1": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}).complete("s", "u")
    assert got.cost_usd == pytest.approx((10 * 1.0 + 5 * 2.0) / 1_000_000)


def test_no_providers_is_an_error():
    with pytest.raises(LLMError):
        LLMClient([])
