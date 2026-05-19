"""BT38 governed Amazon SP-API live PATCH layer.

This module is called only from the governed Amazon FBM service method after
approval, runtime gate, store validation, listing validation, and MFN/FBM
eligibility have passed. It does not import or call old sync, push, queue,
worker, scheduler, or legacy marketplace code.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping

DEFAULT_UK_MARKETPLACE_ID = "A1F83G8C2ARO7P"


def governed_amazon_quantity_patch(
    *,
    store,
    listing,
    sku: str,
    quantity: int,
    marketplace_id: str | None,
    command_id: str | None,
    approval_id: str | None,
) -> dict[str, Any]:
    """Run one governed Amazon FBM/MFN ListingsItems quantity PATCH.

    Uses the installed python-amazon-sp-api package instead of a raw manual
    SigV4 implementation. This keeps marketplace mutation inside the governed
    lane while avoiding old sync/push/import code.
    """
    started = time.monotonic()
    clean_sku = str(sku or "").strip()

    if not clean_sku:
        return _blocked("Missing Amazon SKU", store=store, listing=listing, sku=sku, quantity=quantity, command_id=command_id, approval_id=approval_id)
    if clean_sku.upper().startswith("FBA-"):
        return _blocked("FBA/AFN is read-only", store=store, listing=listing, sku=clean_sku, quantity=quantity, command_id=command_id, approval_id=approval_id)

    try:
        clean_quantity = int(quantity)
    except (TypeError, ValueError):
        return _blocked("Quantity must be an integer", store=store, listing=listing, sku=clean_sku, quantity=quantity, command_id=command_id, approval_id=approval_id)
    if clean_quantity < 0:
        return _blocked("Quantity cannot be negative", store=store, listing=listing, sku=clean_sku, quantity=clean_quantity, command_id=command_id, approval_id=approval_id)

    credentials = _load_credentials(store)
    if not credentials["ok"]:
        return _blocked(credentials["reason"], store=store, listing=listing, sku=clean_sku, quantity=clean_quantity, command_id=command_id, approval_id=approval_id)

    resolved_marketplace_id = marketplace_id or credentials["marketplace_id"] or DEFAULT_UK_MARKETPLACE_ID

    try:
        response = _patch_listing_quantity_with_sp_api(
            credentials=credentials,
            sku=clean_sku,
            quantity=clean_quantity,
            marketplace_id=resolved_marketplace_id,
        )
        status_code = int(response.get("status_code") or 0)
        success = 200 <= status_code < 300
        return {
            "success": success,
            "ok": success,
            "governed": True,
            "execution_blocked": not success,
            "method": "governed_amazon_quantity_patch",
            "execution_library": "python-amazon-sp-api",
            "delegated_method": None,
            "sku": clean_sku,
            "quantity": clean_quantity,
            "marketplace_id": resolved_marketplace_id,
            "command_id": command_id,
            "approval_id": approval_id,
            "store_id": getattr(store, "id", None),
            "listing_id": getattr(listing, "id", None),
            "marketplace_status_code": status_code,
            "marketplace_response": response.get("body"),
            "latency_ms": _latency_ms(started),
            "failure_reason": None if success else "Amazon SP-API library returned non-success status.",
            "reason": "Amazon SP-API library PATCH executed successfully." if success else "Amazon SP-API library PATCH returned a failure response.",
        }
    except Exception as exc:
        return {
            "success": False,
            "ok": False,
            "governed": True,
            "execution_blocked": True,
            "method": "governed_amazon_quantity_patch",
            "execution_library": "python-amazon-sp-api",
            "delegated_method": None,
            "sku": clean_sku,
            "quantity": clean_quantity,
            "marketplace_id": resolved_marketplace_id,
            "command_id": command_id,
            "approval_id": approval_id,
            "store_id": getattr(store, "id", None),
            "listing_id": getattr(listing, "id", None),
            "marketplace_status_code": None,
            "marketplace_response": None,
            "latency_ms": _latency_ms(started),
            "failure_reason": str(exc),
            "reason": "Amazon SP-API library PATCH failed before a success response was returned.",
        }


def _load_credentials(store) -> dict[str, Any]:
    raw = getattr(store, "api_key", None)
    parsed: Mapping[str, Any] = {}
    amazon_credentials = getattr(store, "amazon_credentials", None)
    if amazon_credentials is not None:
        parsed = {
            "refresh_token": getattr(amazon_credentials, "refresh_token", ""),
            "lwa_app_id": getattr(amazon_credentials, "lwa_app_id", ""),
            "lwa_client_secret": getattr(amazon_credentials, "lwa_client_secret", ""),
            "seller_id": getattr(amazon_credentials, "seller_id", ""),
            "marketplace_id": getattr(amazon_credentials, "marketplace_id", DEFAULT_UK_MARKETPLACE_ID),
            "aws_access_key_id": getattr(amazon_credentials, "aws_access_key_id", ""),
            "aws_secret_access_key": getattr(amazon_credentials, "aws_secret_access_key", ""),
            "role_arn": getattr(amazon_credentials, "aws_user_arn", ""),
        }
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "reason": "Amazon credentials JSON could not be parsed"}
    elif isinstance(raw, Mapping):
        parsed = raw

    credentials = {
        "refresh_token": _first_value(parsed, "refresh_token", "AMAZON_REFRESH_TOKEN", "SP_API_REFRESH_TOKEN"),
        "lwa_app_id": _first_value(parsed, "lwa_app_id", "client_id", "AMAZON_LWA_CLIENT_ID", "AMAZON_LWA_APP_ID", "SP_API_LWA_CLIENT_ID"),
        "lwa_client_secret": _first_value(parsed, "lwa_client_secret", "client_secret", "AMAZON_LWA_CLIENT_SECRET", "SP_API_LWA_CLIENT_SECRET"),
        "seller_id": _first_value(parsed, "seller_id", "selling_partner_id", "AMAZON_SELLER_ID", "SP_API_SELLER_ID"),
        "marketplace_id": _first_value(parsed, "marketplace_id", "AMAZON_MARKETPLACE_ID", "SP_API_MARKETPLACE_ID") or DEFAULT_UK_MARKETPLACE_ID,
        "aws_access_key_id": _first_value(parsed, "aws_access_key_id", "AWS_ACCESS_KEY_ID", "AMAZON_AWS_ACCESS_KEY_ID", "SP_API_AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": _first_value(parsed, "aws_secret_access_key", "AWS_SECRET_ACCESS_KEY", "AMAZON_AWS_SECRET_ACCESS_KEY", "SP_API_AWS_SECRET_ACCESS_KEY"),
        "role_arn": _first_value(parsed, "role_arn", "aws_user_arn", "AWS_ROLE_ARN", "AMAZON_AWS_ROLE_ARN", "SP_API_ROLE_ARN"),
    }

    missing = [key for key in ("refresh_token", "lwa_app_id", "lwa_client_secret", "seller_id") if not credentials[key]]
    if missing:
        return {"ok": False, "reason": f"Missing Amazon credential fields: {', '.join(missing)}"}

    credentials["ok"] = True
    return credentials


def _patch_listing_quantity_with_sp_api(*, credentials: Mapping[str, str], sku: str, quantity: int, marketplace_id: str) -> dict[str, Any]:
    from sp_api.api import ListingsItems
    from sp_api.base import Marketplaces

    client = ListingsItems(
        credentials=_sp_api_credentials(credentials),
        marketplace=_marketplace_for_id(marketplace_id, Marketplaces),
    )

    body = {
        "productType": "PRODUCT",
        "patches": [
            {
                "op": "replace",
                "path": "/attributes/fulfillment_availability",
                "value": [
                    {
                        "fulfillment_channel_code": "DEFAULT",
                        "quantity": quantity,
                    }
                ],
            }
        ],
    }

    response = client.patch_listings_item(
        sellerId=credentials["seller_id"],
        sku=sku,
        body=body,
        marketplaceIds=[marketplace_id],
    )

    status_code = int(getattr(response, "status_code", None) or getattr(response, "status", None) or 200)
    payload = getattr(response, "payload", None)
    if payload is None and hasattr(response, "json"):
        payload = response.json()
    if payload is None:
        payload = str(response)

    return {"status_code": status_code, "body": payload}


def _sp_api_credentials(credentials: Mapping[str, str]) -> dict[str, str]:
    result = {
        "refresh_token": credentials["refresh_token"],
        "lwa_app_id": credentials["lwa_app_id"],
        "lwa_client_secret": credentials["lwa_client_secret"],
    }
    if credentials.get("aws_access_key_id"):
        result["aws_access_key"] = credentials["aws_access_key_id"]
    if credentials.get("aws_secret_access_key"):
        result["aws_secret_key"] = credentials["aws_secret_access_key"]
    if credentials.get("role_arn"):
        result["role_arn"] = credentials["role_arn"]
    return result


def _marketplace_for_id(marketplace_id: str, market_type):
    if marketplace_id == DEFAULT_UK_MARKETPLACE_ID:
        return market_type.UK
    return market_type.UK


def _first_value(source: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = source.get(name)
        if value:
            return str(value).strip()
        env_value = os.getenv(name)
        if env_value:
            return env_value.strip()
    return ""


def _blocked(reason: str, *, store, listing, sku, quantity, command_id, approval_id) -> dict[str, Any]:
    return {
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "method": "governed_amazon_quantity_patch",
        "execution_library": "python-amazon-sp-api",
        "delegated_method": None,
        "sku": sku,
        "quantity": quantity,
        "command_id": command_id,
        "approval_id": approval_id,
        "store_id": getattr(store, "id", None),
        "listing_id": getattr(listing, "id", None),
        "failure_reason": reason,
        "reason": reason,
    }


def _latency_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
