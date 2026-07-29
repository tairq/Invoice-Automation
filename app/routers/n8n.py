"""n8n integration API routes."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.core.auth import get_api_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/n8n",
    tags=["n8n"],
    dependencies=[Depends(get_api_key)],
)


@router.post("/trigger")
async def trigger_n8n_workflow(
    payload: dict,
) -> dict:
    """Trigger an n8n workflow with an invoice payload.

    Sends the full invoice data to the configured N8N_WEBHOOK_URL.
    This endpoint is called automatically at the end of the Celery pipeline
    when N8N_ENABLED=true.
    """
    if not settings.n8n_enabled:
        raise HTTPException(status_code=400, detail="n8n integration is not enabled")

    webhook_url = settings.n8n_webhook_url
    if not webhook_url:
        raise HTTPException(status_code=400, detail="N8N_WEBHOOK_URL is not configured")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()

        logger.info(
            "n8n workflow triggered: status=%s invoice_id=%s",
            resp.status_code,
            payload.get("id"),
        )
        return {
            "success": True,
            "status_code": resp.status_code,
            "response": resp.text[:500] if resp.text else None,
        }
    except httpx.RequestError as exc:
        logger.warning("n8n webhook call failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach n8n webhook: {exc}",
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("n8n webhook returned error: %s", exc)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"n8n webhook error: {exc.response.text[:500]}",
        )
