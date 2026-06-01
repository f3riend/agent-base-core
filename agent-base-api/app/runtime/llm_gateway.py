from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from app.integrations.ai_client import resolve_openai_key


class LLMGateway:
    """Provider abstraction for runtime conversational generation."""

    def __init__(self, model: str | None = None) -> None:
        env_model = str(os.getenv("OPENAI_MODEL") or "").strip()
        self.model = (model or "").strip() or env_model or "gpt-4.1-mini"

    async def generate_with_stream(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        api_key = resolve_openai_key(None)
        client = AsyncOpenAI(api_key=api_key)
        chunks: list[str] = []
        async with client.responses.stream(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.55,
            max_output_tokens=480,
        ) as stream:
            async for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = str(getattr(event, "delta", "") or "")
                    if not delta:
                        continue
                    chunks.append(delta)
                    if on_chunk is not None:
                        await on_chunk(delta)
            final_response = await stream.get_final_response()
        text = "".join(chunks).strip()
        if text:
            return text
        output_text = str(getattr(final_response, "output_text", "") or "").strip()
        if output_text:
            return output_text
        raise RuntimeError("LLM bos yanit dondurdu.")
