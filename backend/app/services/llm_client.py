"""Universal LLM extraction client supporting OpenAI-compatible and Anthropic APIs.

Iron Rule #1: This client may ONLY be used as an optional fallback for structured
factual extraction or verbatim OCR transcription.
It MUST NEVER compute statutory deadlines or write to the database.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class UniversalLLMClient:
    """Thin async client supporting `openai_compatible` and `anthropic` providers."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    @property
    def is_enabled(self) -> bool:
        return (
            self.settings.llm_provider != "none"
            and bool(self.settings.llm_api_key.strip())
            and bool(self.settings.llm_model.strip())
        )

    async def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Optional[dict[str, Any]]:
        """Invoke configured LLM provider and parse a JSON object response."""
        raw_text = await self._dispatch_prompt(
            system_prompt=system_prompt,
            openai_user_content=user_prompt,
            anthropic_user_content=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not raw_text:
            return None
        return self._parse_json_block(raw_text)

    async def transcribe_images(
        self,
        images_png: list[bytes],
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 2400,
    ) -> Optional[str]:
        """Transcribe verbatim text from rendered PDF/image bytes via multimodal LLM."""
        if not images_png or not self.is_enabled:
            return None

        openai_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        anthropic_content: list[dict[str, Any]] = []
        for img_bytes in images_png:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            openai_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )
            anthropic_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                }
            )
        anthropic_content.append({"type": "text", "text": user_prompt})

        raw_text = await self._dispatch_prompt(
            system_prompt=system_prompt,
            openai_user_content=openai_content,
            anthropic_user_content=anthropic_content,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not raw_text:
            return None
        return self._strip_code_fences(raw_text)

    async def _dispatch_prompt(
        self,
        *,
        system_prompt: str,
        openai_user_content: Any,
        anthropic_user_content: Any,
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        if not self.is_enabled:
            return None
        provider = self.settings.llm_provider
        try:
            if provider == "openai_compatible":
                return await self._call_openai_compatible(
                    system_prompt=system_prompt,
                    user_content=openai_user_content,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            if provider == "anthropic":
                return await self._call_anthropic(
                    system_prompt=system_prompt,
                    user_content=anthropic_user_content,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM extraction fallback failed (%s): %s", provider, exc)
            return None

    async def _call_openai_compatible(
        self,
        system_prompt: str,
        user_content: Any,
        temperature: float,
        max_tokens: int,
    ) -> str:
        base_url = (self.settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return str(data["choices"][0]["message"]["content"] or "")

    async def _call_anthropic(
        self,
        system_prompt: str,
        user_content: Any,
        temperature: float,
        max_tokens: int,
    ) -> str:
        base_url = (self.settings.llm_base_url or "https://api.anthropic.com").rstrip("/")
        url = f"{base_url}/v1/messages"
        headers = {
            "x-api-key": self.settings.llm_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "system": system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_content}],
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        blocks = data.get("content", [])
        text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        return "\n".join(text_parts)

    @staticmethod
    def _strip_code_fences(raw_text: str) -> str:
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    @classmethod
    def _parse_json_block(cls, raw_text: str) -> Optional[dict[str, Any]]:
        text = cls._strip_code_fences(raw_text)
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                try:
                    parsed = json.loads(text[start : end + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
            return None
