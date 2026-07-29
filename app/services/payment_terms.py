"""Payment terms parsing service — parse human-readable payment terms into due dates."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Regex-based fallback for common payment term patterns
PAYMENT_TERM_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"net\s*(\d+)", re.IGNORECASE), 30),  # Net 30 → 30 days
    (re.compile(r"(\d+)\s*days?", re.IGNORECASE), 30),  # 30 days → 30 days
    (re.compile(r"due\s+on\s+receipt", re.IGNORECASE), 0),  # Due on receipt → 0 days
    (re.compile(r"upon\s+receipt", re.IGNORECASE), 0),
    (re.compile(r"immediate", re.IGNORECASE), 0),
    (re.compile(r"(\d+)/10\s+net\s+(\d+)", re.IGNORECASE), None),  # 2/10 Net 30
    (re.compile(r"eom", re.IGNORECASE), 30),  # End of month → ~30 days
    (re.compile(r"(\d+)\s*-\s*(\d+)", re.IGNORECASE), 30),
]

# Discount pattern: "2/10 Net 30" → 2% discount if paid within 10 days, net due in 30
DISCOUNT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)\s+net\s+(\d+)", re.IGNORECASE)


def parse_payment_terms(
    payment_terms_text: str | None,
    issue_date: date | None = None,
) -> dict[str, Any]:
    """Parse payment terms text and return due_date and early payment discount info.

    Uses Claude LLM for intelligent parsing, with regex fallback.

    Args:
        payment_terms_text: Raw payment terms string (e.g. "Net 30", "2/10 Net 30")
        issue_date: Invoice issue date (defaults to today if not provided)

    Returns:
        dict with:
            - due_date: ISO date string or None
            - early_payment_discount: dict with discount_pct, discount_date, or None
    """
    if not payment_terms_text:
        return {"due_date": None, "early_payment_discount": None}

    base_date = issue_date or date.today()

    # Try regex-based parsing first (fast path)
    regex_result = _parse_with_regex(payment_terms_text, base_date)
    if regex_result["due_date"]:
        return regex_result

    # Fall back to LLM-based parsing
    return _parse_with_llm(payment_terms_text, base_date)


def _parse_with_regex(terms: str, base_date: date) -> dict[str, Any]:
    """Parse payment terms using regex patterns."""
    terms_clean = terms.strip()

    # Check for discount pattern first: "2/10 Net 30"
    discount_match = DISCOUNT_PATTERN.search(terms_clean)
    if discount_match:
        discount_pct = float(discount_match.group(1))
        discount_days = int(discount_match.group(2))
        net_days = int(discount_match.group(3))

        discount_date = base_date + timedelta(days=discount_days)
        due_date = base_date + timedelta(days=net_days)

        return {
            "due_date": due_date.isoformat(),
            "early_payment_discount": {
                "discount_pct": discount_pct,
                "discount_date": discount_date.isoformat(),
                "net_days": net_days,
            },
        }

    # Check net term pattern
    for pattern, default_days in PAYMENT_TERM_PATTERNS:
        match = pattern.search(terms_clean)
        if match:
            if match.lastindex and match.lastindex >= 1:
                try:
                    days = int(match.group(1))
                except (ValueError, IndexError):
                    days = default_days or 30
            else:
                days = default_days or 30

            due = base_date + timedelta(days=days)
            return {"due_date": due.isoformat(), "early_payment_discount": None}

    return {"due_date": None, "early_payment_discount": None}


def _parse_with_llm(terms: str, base_date: date) -> dict[str, Any]:
    """Parse payment terms using Claude LLM."""
    prompt = f"""Parse the following payment terms and return a JSON object.

Payment terms: "{terms}"
Invoice issue date: {base_date.isoformat()}

Return JSON with:
- due_date: the payment due date in YYYY-MM-DD format (calculate from the terms and issue date)
- early_payment_discount: null if no early payment discount, or an object with:
  - discount_pct: the discount percentage as a number (e.g., 2 for 2%)
  - discount_date: the last date to qualify for the discount in YYYY-MM-DD format

Examples:
- "Net 30" → {{"due_date": "2026-08-25", "early_payment_discount": null}}
- "2/10 Net 30" → {{"due_date": "2026-09-14",
   "early_payment_discount": {{"discount_pct": 2, "discount_date": "2026-08-25"}}}}
- "Due on receipt" → {{"due_date": "2026-07-26", "early_payment_discount": null}}

Return ONLY valid JSON, no other text."""

    try:
        # Use the LLM client's existing Anthropic/OpenAI infrastructure
        # but send a text-only prompt since there are no images
        import httpx

        from app.config import settings

        if settings.llm_provider == "anthropic":
            headers = {
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": settings.anthropic_model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
                "system": "You are a payment terms parser. Return only valid JSON.",
            }
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            full_text = "\n".join(text_blocks)
        else:
            headers = {
                "Authorization": f"Bearer {settings.openai_api_key}",
                "content-type": "application/json",
            }
            payload = {
                "model": settings.openai_model,
                "max_tokens": 1024,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a payment terms parser. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
            }
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            full_text = data["choices"][0]["message"]["content"]

        # Parse the JSON from the response
        full_text = full_text.strip()
        if full_text.startswith("```"):
            full_text = full_text.split("\n", 1)[-1]
            full_text = full_text.rsplit("```", 1)[0].strip()
        if full_text.startswith("json"):
            full_text = full_text[4:].strip()
        if full_text.startswith("JSON"):
            full_text = full_text[4:].strip()

        result = json.loads(full_text)
        return {
            "due_date": result.get("due_date"),
            "early_payment_discount": result.get("early_payment_discount"),
        }

    except Exception as exc:
        logger.warning("LLM payment terms parsing failed: %s", exc)
        # Fall back: add 30 days from issue date as a reasonable default
        fallback_due = base_date + timedelta(days=30)
        return {
            "due_date": fallback_due.isoformat(),
            "early_payment_discount": None,
        }
