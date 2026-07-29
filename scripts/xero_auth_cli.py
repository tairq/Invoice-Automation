"""Xero desktop auth helper — PKCE OAuth flow via local callback server.

Usage:
    python scripts/xero_auth_cli.py

Starts a temporary HTTP server on port 18080, prints the Xero authorization
URL, waits for the OAuth callback, and exchanges the code for tokens via
the running Invoice-Processor API (PKCE Desktop flow — no client_secret).

Prerequisites:
    1. The FastAPI server must be running (e.g. ``uvicorn app.main:app``)
    2. ``XERO_CLIENT_ID`` set in .env (no client_secret needed for Desktop)
    3. Xero app registered as "Desktop" in Xero Developer Portal
       with redirect URI: http://localhost:18080/xero-callback
"""

from __future__ import annotations

import http.server
import logging
import os
import sys
import urllib.parse
import webbrowser
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("xero-auth")

# Default — override with --api-url or XERO_API_URL env var
API_BASE = os.environ.get("XERO_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("XERO_API_KEY", "")
CALLBACK_PORT = int(os.environ.get("XERO_CALLBACK_PORT", "18080"))

# Shared state between the server handler and main
received_code: Optional[str] = None
received_state: Optional[str] = None
server_instance: Optional[http.server.HTTPServer] = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Tiny HTTP handler that catches Xero's OAuth redirect."""

    def do_GET(self) -> None:
        global received_code, received_state

        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path.rstrip("/") != "/xero-callback":
            self._respond(404, "Not found — expected /xero-callback")
            return

        codes = params.get("code", [])
        states = params.get("state", [])

        if not codes:
            self._respond(400, "No authorization code received.")
            return

        received_code = codes[0]
        received_state = states[0] if states else None

        self._respond(
            200,
            "Authorization received! You can close this tab.",
        )

        # Shut down the server after responding
        if server_instance:
            server_instance.shutdown()

    def _respond(self, status: int, body: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[misc]
        logger.debug(fmt, *args)


def _get_auth_data() -> Optional[dict[str, Any]]:
    """Fetch the Xero authorization URL + PKCE code_verifier from the API."""
    import httpx

    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    try:
        resp = httpx.get(
            f"{API_BASE}/api/v1/integrations/xero/connect",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Failed to get authorization URL from %s", API_BASE)
        logger.error("  Make sure the FastAPI server is running and reachable.")
        logger.error("  Error: %s", exc)
        return None


def _exchange_code(code: str, code_verifier: str, state: str) -> bool:
    """Submit the authorization code, PKCE verifier, and OAuth state."""
    import httpx

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    try:
        resp = httpx.post(
            f"{API_BASE}/api/v1/integrations/xero/callback",
            json={"code": code, "code_verifier": code_verifier, "state": state},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logger.error("Token exchange failed (HTTP %s):", exc.response.status_code)
        logger.error("  %s", exc.response.text)
        return False
    except Exception as exc:
        logger.error("Token exchange error: %s", exc)
        return False


def main() -> int:
    global server_instance

    # ── 1. Get the auth URL + PKCE verifier ───────────────────────────
    auth_data = _get_auth_data()
    if not auth_data:
        return 1

    auth_url = auth_data["authorization_url"]
    code_verifier = auth_data["code_verifier"]

    # ── 2. Start local callback server ───────────────────────────────
    server_instance = http.server.HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║              Xero Desktop Auth — Step 1 of 2              ║")
    logger.info("╠══════════════════════════════════════════════════════════════╣")
    logger.info("║  Listening for OAuth callback on http://localhost:%-4s    ║", CALLBACK_PORT)
    logger.info("╚══════════════════════════════════════════════════════════════╝")
    logger.info("")
    logger.info("Opening your browser to authorize with Xero...")
    logger.info("")
    logger.info("If the browser doesn't open, copy this URL manually:")
    logger.info("")
    logger.info("  %s", auth_url)
    logger.info("")

    webbrowser.open(auth_url)

    # ── 3. Wait for the callback ─────────────────────────────────────
    try:
        server_instance.serve_forever()
    except KeyboardInterrupt:
        logger.info("\nInterrupted.")
        return 1

    if not received_code:
        logger.error("No authorization code received.")
        return 1

    logger.info("")
    logger.info("Authorization code received! Exchanging for tokens...")

    # ── 4. Exchange the code ─────────────────────────────────────────
    if not received_state:
        logger.error("OAuth callback did not include a state parameter.")
        return 1

    if not _exchange_code(received_code, code_verifier, received_state):
        logger.error("")
        logger.error("Token exchange failed. You can retry manually:")
        logger.error("  curl -X POST %s/api/v1/integrations/xero/callback \\", API_BASE)
        logger.error("    -H 'Content-Type: application/json' \\")
        logger.error(
            '    -d \'{"code": "%s", "code_verifier": "%s"}\'', received_code, code_verifier
        )
        return 1

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║   Xero integration connected successfully!                ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")
    logger.info("")
    logger.info("Invoices processed now will automatically sync to Xero.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
