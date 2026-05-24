from __future__ import annotations

import re
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings


MAX_HISTORY_MESSAGES = 4
MAX_REPLY_SENTENCES = 2
MAX_REPLY_WORDS = 28


class OpenAIResponder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    @staticmethod
    def _normalize_for_speech(text: str) -> str:
        normalized = text.replace("**", "").replace("#", "")
        normalized = re.sub(r"(?m)^\s*[-*]\s+", "", normalized)
        normalized = re.sub(r"(?m)^\s*\d+\.\s+", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    @classmethod
    def _truncate_for_call(cls, text: str) -> str:
        normalized = cls._normalize_for_speech(text)
        if not normalized:
            return ""

        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", normalized)
            if sentence.strip()
        ]
        compact = " ".join(sentences[:MAX_REPLY_SENTENCES]) or normalized

        words = compact.split()
        if len(words) > MAX_REPLY_WORDS:
            if len(sentences) > 1:
                first_sentence = sentences[0]
                if len(first_sentence.split()) <= MAX_REPLY_WORDS:
                    return first_sentence

            compact = " ".join(words[:MAX_REPLY_WORDS]).rstrip(",;:")
            while compact.split() and compact.split()[-1].lower() in {
                "and",
                "or",
                "but",
                "so",
                "because",
                "with",
                "that",
            }:
                compact = " ".join(compact.split()[:-1]).rstrip(",;:")
            if compact and compact[-1] not in ".!?":
                compact += "."

        return compact

    async def generate_reply(
        self,
        *,
        history: list[dict[str, str]],
        user_text: str,
        system_prompt: str | None = None,
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt or self._settings.agent_system_prompt}
        ]

        if history:
            messages.extend(history[-MAX_HISTORY_MESSAGES:])

        messages.append({"role": "user", "content": user_text})

        completion = await self._client.chat.completions.create(
            model=self._settings.openai_model,
            messages=messages,
            temperature=0.35,
            max_tokens=72,
        )

        message = completion.choices[0].message.content
        if not message:
            return "I heard you, but I do not have a response yet."

        if isinstance(message, str):
            return self._truncate_for_call(message)

        return self._truncate_for_call(
            "".join(part.text for part in message if getattr(part, "text", None))
        )
