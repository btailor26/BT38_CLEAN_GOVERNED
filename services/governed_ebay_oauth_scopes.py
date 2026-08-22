"""Single governed source of truth for BT38's production eBay OAuth scopes."""

from __future__ import annotations

import os


LISTING_READ_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.listing.read"

DEFAULT_EBAY_OAUTH_SCOPES = (
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription",
    LISTING_READ_SCOPE,
)

# Existing refresh tokens cannot gain a scope they were not originally granted.
# Keep refresh operations limited to the historical grant until the seller
# explicitly re-authorizes through the governed OAuth flow.
LEGACY_EBAY_OAUTH_SCOPES = tuple(
    scope for scope in DEFAULT_EBAY_OAUTH_SCOPES if scope != LISTING_READ_SCOPE
)


def governed_ebay_oauth_scopes() -> str:
    """Return the operator override plus BT38's complete governed scope set."""

    configured = (os.getenv("EBAY_SCOPES") or "").split()
    aligned = list(dict.fromkeys([*configured, *DEFAULT_EBAY_OAUTH_SCOPES]))
    return " ".join(aligned)


def governed_ebay_refresh_scopes(credentials: dict | None = None) -> str:
    """Refresh only already granted scopes; existing tokens stay usable until reauth."""

    credentials = credentials or {}
    granted = str(credentials.get("oauth_granted_scope") or "").strip()
    if granted:
        return granted
    return " ".join(LEGACY_EBAY_OAUTH_SCOPES)
