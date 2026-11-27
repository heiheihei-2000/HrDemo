import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL


class LLMNotConfigured(RuntimeError):
    pass


def _messages(system: str, user: str, history: list[dict] | None) -> list[dict]:
    return [{"role": "system", "content": system}, *(history or []), {"role": "user", "content": user}]


class LLM:
    """One instance per turn: the async HTTP client is bound to the turn's event loop."""

    def __init__(self) -> None:
        self._client = (
            AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL, timeout=60, max_retries=0)
            if DEEPSEEK_API_KEY
            else None
        )

    def _c(self) -> AsyncOpenAI:
        if self._client is None:
            raise LLMNotConfigured("未配置 DEEPSEEK_API_KEY，请在 .env 中填写后重启应用")
        return self._client

    async def json(self, system: str, user: str, history: list[dict] | None = None) -> dict:
        resp = await self._c().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=_messages(system, user, history),
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        text = (resp.choices[0].message.content or "").strip().removeprefix("```json").removesuffix("```")
        return json.loads(text)

    async def stream(self, system: str, user: str, history: list[dict] | None = None) -> AsyncIterator[str]:
        resp = await self._c().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=_messages(system, user, history),
            temperature=0.4,
            stream=True,
        )
        async for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
