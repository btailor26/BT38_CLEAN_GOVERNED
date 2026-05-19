"""BT38 governed Amazon SP-API live PATCH layer.

This module is called only from the governed Amazon FBM service method after
approval, runtime gate, store validation, listing validation, and MFN/FBM
eligibility have passed. It does not import or call old sync, push, queue,
worker, scheduler, or legacy marketplace code.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SP_API_ENDPOINT = "https://sellingpartnerapi-eu.amazon.com"
SP_API_HOST = "sellingpartnerapi-eu.amazon.com"
SP_API_REGION = "eu-west-1"
SP_API_SERVICE = "execute-api"
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
    """Run one governed Amazon FBM/MFN Listings Items quantity PATCH."""
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
        access_token = _get_lwa_access_token(credentials)
        response = _patch_listing_quantity(
            access_token=access_token,
            credentials=credentials,
            seller_id=credentials["seller_id"],
            sku=clean_sku,
            quantity=clean_quantity,
            marketplace_id=resolved_marketplace_id,
        )
        success = 200 <= response["status_code"] < 300
        return {
            "success": success,
            "ok": success,
            "governed": True,
            "execution_blocked": not success,
            "method": "governed_amazon_quantity_patch",
            "delegated_method": None,
            "sku": clean_sku,
            "quantity": clean_quantity,
            "marketplace_id": resolved_marketplace_id,
            "command_id": command_id,
            "approval_id": approval_id,
            "store_id": getattr(store, "id", None),
            "listing_id": getattr(listing, "id", None),
            "marketplace_status_code": response["status_code"],
            "marketplace_response": response["body"],
            "latency_ms": _latency_ms(started),
            "failure_reason": None if success else "Amazon SP-API returned non-success status.",
            "reason": "Amazon SP-API PATCH executed successfully." if success else "Amazon SP-API PATCH returned a failure response.",
        }
    except Exception as exc:
        return {
            "success": False,
            "ok": False,
            "governed": True,
            "execution_blocked": True,
            "method": "governed_amazon_quantity_patch",
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
            "reason": "Amazon SP-API PATCH failed before a success response was returned.",
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
        }
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "reason": "Amazon credentials JSON could not be parsed"}
    elif isinstance(raw, Mapping):
        parsed = raw

    credentials = {
        "refresh_token": parsed.get("refresh_token") or "",
        "lwa_app_id": parsed.get("lwa_app_id") or parsed.get("client_id") or "",
        "lwa_client_secret": parsed.get("lwa_client_secret") or parsed.get("client_secret") or "",
        "seller_id": parsed.get("seller_id") or parsed.get("selling_partner_id") or "",
        "marketplace_id": parsed.get("marketplace_id") or DEFAULT_UK_MARKETPLACE_ID,
        "aws_access_key_id": parsed.get("aws_access_key_id") or "",
        "aws_secret_access_key": parsed.get("aws_secret_access_key") or "",
    }
    missing = [key for key, value in credentials.items() if key != "marketplace_id" and not value]
    if missing:
        return {"ok": False, "reason": f"Missing Amazon credential fields: {', '.join(missing)}"}
    credentials["ok"] = True
    return credentials


def _get_lwa_access_token(credentials: Mapping[str, str]) -> str:
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": credentials["refresh_token"],
        "client_id": credentials["lwa_app_id"],
        "client_secret": credentials["lwa_client_secret"],
    }).encode("utf-8")
    request = urllib.request.Request(
        LWA_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    response = _open_json(request)
    token = response["body"].get("access_token")
    if not token:
        raise RuntimeError("Amazon LWA access token was not returned")
    return str(token)


def _patch_listing_quantity(*, access_token: str, credentials: Mapping[str, str], seller_id: str, sku: str, quantity: int, marketplace_id: str) -> dict[str, Any]:
    canonical_uri = f"/listings/2021-08-01/items/{urllib.parse.quote(str(seller_id), safe='')}/{urllib.parse.quote(str(sku), safe='')}"
    query_string = urllib.parse.urlencode({"marketplaceIds": marketplace_id})
    body = {
        "productType": "PRODUCT",
        "patches": [{
            "op": "replace",
            "path": "/attributes/fulfillment_availability",
            "value": [{"fulfillment_channel_code": "DEFAULT", "quantity": quantity}],
        }],
    }
    body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = _signed_headers(
        method="PATCH",
        canonical_uri=canonical_uri,
        query_string=query_string,
        body_bytes=body_bytes,
        access_token=access_token,
        credentials=credentials,
    )
    request = urllib.request.Request(
        f"{SP_API_ENDPOINT}{canonical_uri}?{query_string}",
        data=body_bytes,
        headers=headers,
        method="PATCH",
    )
    return _open_json(request)


def _signed_headers(*, method: str, canonical_uri: str, query_string: str, body_bytes: bytes, access_token: str, credentials: Mapping[str, str]) -> dict[str, str]:
    now = datetime.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body_bytes).hexdigest()
    canonical_headers = f"host:{SP_API_HOST}\nx-amz-access-token:{access_token}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-access-token;x-amz-date"
    canonical_request = "\n".join([method, canonical_uri, query_string, canonical_headers, signed_headers, payload_hash])
    credential_scope = f"{date_stamp}/{SP_API_REGION}/{SP_API_SERVICE}/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, credential_scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signing_key = _signature_key(credentials["aws_secret_access_key"], date_stamp, SP_API_REGION, SP_API_SERVICE)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Authorization": f"AWS4-HMAC-SHA256 Credential={credentials['aws_access_key_id']}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}",
        "Content-Type": "application/json",
        "Host": SP_API_HOST,
        "x-amz-access-token": access_token,
        "x-amz-date": amz_date,
    }


def _signature_key(secret_key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
    key_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    key_region = hmac.new(key_date, region_name.encode("utf-8"), hashlib.sha256).digest()
    key_service = hmac.new(key_region, service_name.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()


def _open_json(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
            return {"status_code": int(getattr(response, "status", 200)), "body": json.loads(text) if text else {}}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(text) if text else {}
        except json.JSONDecodeError:
            body = {"raw": text}
        return {"status_code": int(exc.code), "body": body}


def _blocked(reason: str, *, store, listing, sku, quantity, command_id, approval_id) -> dict[str, Any]:
    return {
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "method": "governed_amazon_quantity_patch",
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
