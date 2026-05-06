from __future__ import annotations

import logging
from typing import Optional

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def get_llm_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )
    return _client


def generate_text(prompt: str, max_tokens: int = 1000) -> str:
    """Generate text using the local LLM."""
    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model="local-model",  # LM Studio handles model selection
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.exception("Failed to generate text with LLM.")
        raise RuntimeError(f"LLM generation failed: {exc}") from exc