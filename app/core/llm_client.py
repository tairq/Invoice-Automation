"""LLM API client for AI-powered invoice extraction."""
from __future__ import annotations

import json
import logging
import time
from base64 import b64encode
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You are an invoice extraction specialist. Analyze the provided invoice image(s) and extract ALL requested fields with high precision.

## Rules
1. Extract values EXACTLY as they appear — do not normalize, correct, or guess
2. For ambiguous or unreadable fields, set confidence < 1.0 and explain why
3. If a field is genuinely not present in the document, set it to null — do NOT fabricate
4. For line items, extract EVERY row visible on the invoice, preserving order
5. Currency: use ISO 4217 three-letter code (USD, EUR, GBP, JPY, ILS, etc.)
6. Dates: return in YYYY-MM-DD format
7. Numbers: return as numeric values WITHOUT currency symbols or commas
8. Tax rates: return as decimal percentages (e.g., 17.00 for 17%)
9. If the document is NOT an invoice, set invoice_type to "unknown" and explain

## Output Format
Return a JSON object matching the extraction schema precisely."""


class LLMClient:
    """Client for LLM-based invoice extraction."""

    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self.timeout = 120.0
        self.max_retries = 3

        if self.provider == "anthropic":
            self.api_key = settings.anthropic_api_key
            self.model = settings.anthropic_model
            self.base_url = "https://api.anthropic.com/v1"
        elif self.provider == "custom":
            self.api_key = settings.custom_api_key
            self.model = settings.custom_model
            self.base_url = (settings.custom_api_base or "").rstrip("/")
        else:
            self.api_key = settings.openai_api_key
            self.model = settings.openai_model
            self.base_url = "https://api.openai.com/v1"

    def _encode_image(self, image_bytes: bytes) -> str:
        return b64encode(image_bytes).decode("utf-8")

    def _build_messages(
        self, image_bytes_list: list[bytes], mime_type: str = "image/png"
    ) -> list[dict]:
        content: list[dict] = []

        for img_bytes in image_bytes_list:
            b64 = self._encode_image(img_bytes)
            if self.provider == "anthropic":
                # Anthropic format
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": b64,
                    },
                })
            else:
                # OpenAI-compatible format (OpenAI, custom endpoints)
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{b64}",
                    },
                })

        content.append({
            "type": "text",
            "text": "Extract all invoice fields from the image(s) above following the extraction schema.",
        })

        return [{"role": "user", "content": content}]

    def _call_anthropic(self, messages: list[dict]) -> dict[str, Any]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": messages,
            "system": EXTRACTION_SYSTEM_PROMPT,
        }

        resp = httpx.post(
            f"{self.base_url}/messages",
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract text content from response
        text_blocks = [
            b["text"]
            for b in data.get("content", [])
            if b.get("type") == "text"
        ]
        full_text = "\n".join(text_blocks)
        return self._parse_response(full_text)

    def _call_openai(self, messages: list[dict]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                *messages,
            ],
        }

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # Handle SSE streaming response — accumulate deltas
            content = self._parse_sse_response(resp.text)
        else:
            # Standard JSON response
            data = resp.json()
            content = data["choices"][0]["message"]["content"]

        return self._parse_response(content.strip())

    def _parse_sse_response(self, text: str) -> str:
        """Parse an SSE (server-sent events) response and accumulate content deltas.

        Each line looks like: data: {"choices":[{"delta":{"content":"..."},...}]}
        Ends with: data: [DONE]
        """
        content_parts: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]  # Strip "data: " prefix
            if data_str.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                choices = chunk.get("choices", [])
                for choice in choices:
                    delta = choice.get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        content_parts.append(content)
            except json.JSONDecodeError:
                continue
        return "".join(content_parts)

    def _parse_response(self, text: str) -> dict[str, Any]:
        """Parse JSON from LLM response (handles markdown code fences)."""
        text = text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()

        return json.loads(text)

    def extract(self, image_bytes_list: list[bytes], mime_type: str = "image/png") -> dict[str, Any]:
        """Extract invoice data from image(s) using the configured LLM.

        Returns structured dict with all extracted fields and confidences.
        Supports Anthropic, OpenAI, and any OpenAI-compatible endpoint.
        """
        messages = self._build_messages(image_bytes_list, mime_type)

        last_error = None
        for attempt in range(self.max_retries):
            try:
                if self.provider == "anthropic":
                    return self._call_anthropic(messages)
                else:
                    return self._call_openai(messages)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "LLM API call attempt %d/%d failed: %s",
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)

        raise RuntimeError(f"LLM extraction failed after {self.max_retries} retries: {last_error}")


# Singleton
llm_client = LLMClient()
