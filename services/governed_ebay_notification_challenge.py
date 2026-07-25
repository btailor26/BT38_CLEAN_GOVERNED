"""eBay Notification API endpoint challenge alignment.

This is a narrow startup-installed guard for the existing governed webhook route.
It handles only eBay's GET destination verification challenge and leaves all POST
capture/execution behaviour on the existing governed route unchanged.
"""

from __future__ import annotations

import hashlib
import os
from urllib.parse import urlparse

from flask import jsonify, request


DEFAULT_ENDPOINT = "https://bt38-prod.fly.dev/governed/webhooks/ebay"


def _configured_endpoint() -> str:
    return (os.getenv("EBAY_NOTIFICATION_ENDPOINT") or DEFAULT_ENDPOINT).strip()


def _verification_token() -> str:
    return (os.getenv("EBAY_NOTIFICATION_VERIFICATION_TOKEN") or "").strip()


def _validate_configuration(endpoint: str, verification_token: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("EBAY_NOTIFICATION_ENDPOINT must be a public HTTPS URL.")
    if not 32 <= len(verification_token) <= 80:
        raise RuntimeError(
            "EBAY_NOTIFICATION_VERIFICATION_TOKEN must contain 32 to 80 characters."
        )


def build_ebay_challenge_response(challenge_code: str) -> str:
    """Return eBay's required SHA-256 challenge response."""
    challenge_code = str(challenge_code or "").strip()
    endpoint = _configured_endpoint()
    verification_token = _verification_token()

    if not challenge_code:
        raise RuntimeError("Missing eBay challenge_code.")

    _validate_configuration(endpoint, verification_token)

    raw = f"{challenge_code}{verification_token}{endpoint}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def install_ebay_notification_challenge_handler(app) -> None:
    """Install the challenge handler before the existing webhook view executes."""
    if getattr(app, "_bt38_ebay_challenge_handler_installed", False):
        return

    app._bt38_ebay_challenge_handler_installed = True

    @app.before_request
    def _bt38_ebay_notification_challenge():
        if request.method != "GET":
            return None

        if request.path.rstrip("/") != "/governed/webhooks/ebay":
            return None

        challenge_code = request.args.get("challenge_code")
        if not challenge_code:
            return None

        try:
            challenge_response = build_ebay_challenge_response(challenge_code)
        except Exception as exc:
            app.logger.exception("eBay notification challenge failed")
            return jsonify({
                "ok": False,
                "success": False,
                "governed": True,
                "error": "ebay_notification_challenge_failed",
                "message": str(exc),
            }), 500

        return jsonify({"challengeResponse": challenge_response}), 200
