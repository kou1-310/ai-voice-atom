import asyncio
from types import SimpleNamespace

import pytest

from app.adapters.openai_client import OpenAIClient


class _FakeCompletions:
    def __init__(self, response: object | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _FakeAsyncOpenAI:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.completions = _FakeCompletions(
            response=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="  テスト応答  "))]
            )
        )
        self.chat = SimpleNamespace(completions=self.completions)


def test_complete_returns_first_choice_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, object] = {}

    def _factory(**kwargs: object) -> _FakeAsyncOpenAI:
        captured_kwargs.update(kwargs)
        return _FakeAsyncOpenAI(**kwargs)

    monkeypatch.setattr(
        "app.adapters.openai_client.AsyncOpenAI",
        _factory,
    )

    client = OpenAIClient(
        base_url="http://localhost:1234/v1",
        api_key="dummy",
        model="test-model",
    )

    result = asyncio.run(
        client.complete(
            messages=[{"role": "user", "content": "こんにちは"}],
            system_prompt="丁寧に返答してください",
            max_tokens=64,
        )
    )

    assert result == "テスト応答"
    assert captured_kwargs["base_url"] == "http://localhost:1234/v1"
    assert captured_kwargs["api_key"] == "dummy"
    assert captured_kwargs["timeout"] == 30.0
    assert client._get_client().completions.calls == [
        {
            "model": "test-model",
            "messages": [
                {"role": "system", "content": "丁寧に返答してください"},
                {"role": "user", "content": "こんにちは"},
            ],
            "max_tokens": 64,
        }
    ]


def test_complete_passes_reasoning_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(**kwargs: object) -> _FakeAsyncOpenAI:
        return _FakeAsyncOpenAI(**kwargs)

    monkeypatch.setattr("app.adapters.openai_client.AsyncOpenAI", _factory)

    client = OpenAIClient(
        base_url="http://localhost:1234/v1",
        api_key="dummy",
        model="test-model",
        reasoning_effort="none",
    )

    asyncio.run(client.complete(messages=[{"role": "user", "content": "こんにちは"}]))

    assert client._get_client().completions.calls[0]["extra_body"] == {"reasoning_effort": "none"}


def test_complete_omits_reasoning_effort_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(**kwargs: object) -> _FakeAsyncOpenAI:
        return _FakeAsyncOpenAI(**kwargs)

    monkeypatch.setattr("app.adapters.openai_client.AsyncOpenAI", _factory)

    client = OpenAIClient(
        base_url="http://localhost:1234/v1",
        api_key="dummy",
        model="test-model",
    )

    asyncio.run(client.complete(messages=[{"role": "user", "content": "こんにちは"}]))

    assert "extra_body" not in client._get_client().completions.calls[0]


def test_complete_passes_sampling_params_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(**kwargs: object) -> _FakeAsyncOpenAI:
        return _FakeAsyncOpenAI(**kwargs)

    monkeypatch.setattr("app.adapters.openai_client.AsyncOpenAI", _factory)

    client = OpenAIClient(
        base_url="http://localhost:1234/v1",
        api_key="dummy",
        model="test-model",
    )

    asyncio.run(
        client.complete(
            messages=[{"role": "user", "content": "こんにちは"}],
            temperature=0.8,
            presence_penalty=0.5,
            frequency_penalty=0.4,
        )
    )

    call = client._get_client().completions.calls[0]
    assert call["temperature"] == 0.8
    assert call["presence_penalty"] == 0.5
    assert call["frequency_penalty"] == 0.4


def test_complete_omits_sampling_params_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(**kwargs: object) -> _FakeAsyncOpenAI:
        return _FakeAsyncOpenAI(**kwargs)

    monkeypatch.setattr("app.adapters.openai_client.AsyncOpenAI", _factory)

    client = OpenAIClient(
        base_url="http://localhost:1234/v1",
        api_key="dummy",
        model="test-model",
    )

    asyncio.run(client.complete(messages=[{"role": "user", "content": "こんにちは"}]))

    call = client._get_client().completions.calls[0]
    assert "temperature" not in call
    assert "presence_penalty" not in call
    assert "frequency_penalty" not in call


def test_complete_requires_configuration() -> None:
    client = OpenAIClient(base_url="http://localhost:1234/v1", api_key="dummy")

    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(client.complete(messages=[{"role": "user", "content": "hello"}]))
