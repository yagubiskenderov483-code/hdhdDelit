from __future__ import annotations

from typing import Awaitable, Callable, Optional

from openai import APIError, AsyncOpenAI, AuthenticationError, RateLimitError

from bot.config import GPT, GROK, Config

SYSTEM_PROMPTS = {
    GPT: (
        "Ты ChatGPT в Telegram. Отвечай ясно и по делу, без канцелярита. "
        "Если пишут по-русски — отвечай по-русски. Это мессенджер: короче, чем на сайте. "
        "Сложное раскладывай по пунктам. Можно характер, но без воды."
    ),
    GROK: (
        "Ты Grok в Telegram: прямой, с юмором, без корпоративной морали. "
        "Помогаешь по-настоящему. Если пишут по-русски — отвечай по-русски. "
        "Коротко, с характером, как умный друг в чате, а не справка."
    ),
}

OnDelta = Callable[[str], Awaitable[None]]


class MissingKeyError(RuntimeError):
    def __init__(self, model: str) -> None:
        self.model = model
        if model == GPT:
            hint = "Нет ключа OpenAI. Добавь OPENAI_API_KEY в .env (platform.openai.com)."
        elif model == GROK:
            hint = "Нет ключа xAI. Добавь XAI_API_KEY в .env (console.x.ai)."
        else:
            hint = "Нужен хотя бы один ключ: OPENAI_API_KEY или XAI_API_KEY."
        super().__init__(hint)


class AIError(RuntimeError):
    pass


class LLM:
    def __init__(self, api_key: str, base_url: str, model: str, label: str) -> None:
        self.model = model
        self.label = label
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=90.0)

    async def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        on_delta: Optional[OnDelta] = None,
    ) -> str:
        try:
            if on_delta is None:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                )
                return (response.choices[0].message.content or "").strip()

            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
            )
            parts: list[str] = []
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    parts.append(delta)
                    await on_delta("".join(parts))
            return "".join(parts).strip()
        except AuthenticationError as exc:
            raise AIError(f"{self.label}: ключ не принят. Проверь API-ключ.") from exc
        except RateLimitError as exc:
            raise AIError(f"{self.label}: слишком много запросов, подожди немного.") from exc
        except APIError as exc:
            raise AIError(f"{self.label}: ошибка API ({exc.message}).") from exc


def build_llms(config: Config) -> dict[str, LLM]:
    llms: dict[str, LLM] = {}
    if config.openai_api_key:
        llms[GPT] = LLM(
            config.openai_api_key,
            config.openai_base_url,
            config.openai_model,
            "ChatGPT",
        )
    if config.xai_api_key:
        llms[GROK] = LLM(
            config.xai_api_key,
            config.xai_base_url,
            config.xai_model,
            "Grok",
        )
    return llms


def require_llm(llms: dict[str, LLM], model: str) -> LLM:
    llm = llms.get(model)
    if llm is None:
        raise MissingKeyError(model)
    return llm


def pack_messages(system: str, history: list[dict[str, str]], user_content) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_content})
    return messages


async def transcribe_voice(config: Config, audio_path: str) -> str:
    if not config.openai_api_key:
        raise MissingKeyError(GPT)
    client = AsyncOpenAI(api_key=config.openai_api_key, timeout=90.0)
    with open(audio_path, "rb") as audio:
        result = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio,
        )
    return (result.text or "").strip()
