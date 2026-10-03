from collections.abc import Sequence
from typing import Any

from openai import APIError, APITimeoutError, AsyncOpenAI


class OpenAIClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        reasoning_effort: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # reasoning 系モデル向け。"none" を渡すと思考出力を抑止し content を確実に埋める。
        # 空文字 / None の場合はパラメータ自体を送らない。
        self.reasoning_effort = reasoning_effort or None
        self._client: AsyncOpenAI | None = None

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    async def complete(
        self,
        messages: Sequence[dict[str, str]],
        system_prompt: str | None = None,
        max_tokens: int = 256,
        temperature: float | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
    ) -> str:
        if not self.is_configured():
            raise RuntimeError("OpenAI client is not configured")

        request_messages: list[dict[str, str]] = []
        if system_prompt and system_prompt.strip():
            request_messages.append({"role": "system", "content": system_prompt.strip()})
        request_messages.extend(messages)

        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": request_messages,
            "max_tokens": max_tokens,
        }
        # サンプリング系は指定があるときだけ送る（None ならバックエンド既定に従う）。
        if temperature is not None:
            create_kwargs["temperature"] = temperature
        if presence_penalty is not None:
            create_kwargs["presence_penalty"] = presence_penalty
        if frequency_penalty is not None:
            create_kwargs["frequency_penalty"] = frequency_penalty
        if self.reasoning_effort:
            # OpenAI 互換 (Ollama 等) のリクエストボディへトップレベルで載せる
            create_kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}

        try:
            response = await self._get_client().chat.completions.create(**create_kwargs)
        except APITimeoutError as exc:
            raise RuntimeError("OpenAI completion timed out") from exc
        except APIError as exc:
            raise RuntimeError(f"OpenAI completion failed: {exc.message}") from exc
        except Exception as exc:
            raise RuntimeError(f"Unexpected OpenAI completion failure: {exc}") from exc

        content = self._extract_text(response)
        if not content:
            raise RuntimeError("OpenAI completion returned empty content")
        return content

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
        return self._client

    @staticmethod
    def _extract_text(response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""

        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return " ".join(part.strip() for part in text_parts if part.strip())

        return ""
