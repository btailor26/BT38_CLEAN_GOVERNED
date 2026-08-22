"""Governed eBay Notification API destination and subscription registration."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from services.governed_ebay_oauth_scopes import LISTING_READ_SCOPE

NOTIFICATION_BASE_URL = "https://api.ebay.com/commerce/notification/v1"
ORDER_TOPIC_ID = "ORDER_CONFIRMATION"
LISTING_TOPIC_ID = "LISTING"
REQUIRED_TOPIC_IDS = (
    ORDER_TOPIC_ID,
    LISTING_TOPIC_ID,
)
TOPIC_SCHEMA_VERSIONS = {
    ORDER_TOPIC_ID: "1.1",
    LISTING_TOPIC_ID: "1.0",
}
DEFAULT_SCHEMA_VERSION = TOPIC_SCHEMA_VERSIONS[ORDER_TOPIC_ID]
DEFAULT_DESTINATION_NAME = "BT38 Production eBay Webhook"


class EbayNotificationRegistrationError(RuntimeError):
    """Raised when governed Notification API registration cannot complete."""


def _decode_store_credentials(store: Any) -> dict[str, Any]:
    raw = getattr(store, "api_key", None)

    if isinstance(raw, dict):
        return dict(raw)

    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    return {}


def _safe_response_payload(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"raw": response.text[:5000]}


def _extract_resource_id(response: requests.Response, field_name: str) -> str | None:
    payload = _safe_response_payload(response)

    if isinstance(payload, dict) and payload.get(field_name):
        return str(payload[field_name])

    location = response.headers.get("Location") or response.headers.get("location")

    if location:
        return location.rstrip("/").split("/")[-1]

    return None


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)

    if parsed.scheme != "https":
        raise EbayNotificationRegistrationError(
            "eBay notification endpoint must use HTTPS."
        )

    if not parsed.netloc:
        raise EbayNotificationRegistrationError(
            "eBay notification endpoint must include a public hostname."
        )

    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise EbayNotificationRegistrationError(
            "eBay notification endpoint cannot use localhost."
        )


def _validate_verification_token(token: str) -> None:
    if not 32 <= len(token) <= 80:
        raise EbayNotificationRegistrationError(
            "EBAY_NOTIFICATION_VERIFICATION_TOKEN must contain 32 to 80 characters."
        )

    if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise EbayNotificationRegistrationError(
            "EBAY_NOTIFICATION_VERIFICATION_TOKEN may contain only letters, "
            "numbers, underscores and hyphens."
        )


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _api_error(operation: str, response: requests.Response) -> None:
    payload = _safe_response_payload(response)

    raise EbayNotificationRegistrationError(
        f"{operation} failed: HTTP {response.status_code}: "
        f"{json.dumps(payload, default=str)}"
    )


def _get_destinations(access_token: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{NOTIFICATION_BASE_URL}/destination",
        headers=_headers(access_token),
        params={"limit": 100},
        timeout=30,
    )

    if response.status_code != 200:
        _api_error("get eBay notification destinations", response)

    payload = _safe_response_payload(response)

    if not isinstance(payload, dict):
        return []

    rows = payload.get("destinations")
    return rows if isinstance(rows, list) else []


def _create_destination(
    *,
    access_token: str,
    endpoint: str,
    verification_token: str,
    destination_name: str,
) -> str:
    response = requests.post(
        f"{NOTIFICATION_BASE_URL}/destination",
        headers=_headers(access_token),
        json={
            "name": destination_name,
            "status": "ENABLED",
            "deliveryConfig": {
                "endpoint": endpoint,
                "verificationToken": verification_token,
            },
        },
        timeout=30,
    )

    if response.status_code not in {200, 201}:
        _api_error("create eBay notification destination", response)

    destination_id = _extract_resource_id(response, "destinationId")

    if not destination_id:
        raise EbayNotificationRegistrationError(
            "eBay created the destination but returned no destination ID."
        )

    return destination_id


def _ensure_destination(
    *,
    access_token: str,
    endpoint: str,
    verification_token: str,
    destination_name: str,
) -> tuple[str, bool]:
    destinations = _get_destinations(access_token)

    for destination in destinations:
        delivery = destination.get("deliveryConfig") or {}

        if delivery.get("endpoint") == endpoint:
            destination_id = destination.get("destinationId")

            if not destination_id:
                continue

            status = str(destination.get("status") or "").upper()

            if status == "DISABLED":
                response = requests.put(
                    f"{NOTIFICATION_BASE_URL}/destination/{destination_id}",
                    headers=_headers(access_token),
                    json={
                        "name": destination.get("name") or destination_name,
                        "status": "ENABLED",
                        "deliveryConfig": {
                            "endpoint": endpoint,
                            "verificationToken": verification_token,
                        },
                    },
                    timeout=30,
                )

                if response.status_code not in {200, 204}:
                    _api_error("enable eBay notification destination", response)

            elif status == "MARKED_DOWN":
                raise EbayNotificationRegistrationError(
                    f"Existing eBay destination {destination_id} is MARKED_DOWN."
                )

            return str(destination_id), False

    destination_id = _create_destination(
        access_token=access_token,
        endpoint=endpoint,
        verification_token=verification_token,
        destination_name=destination_name,
    )

    return destination_id, True


def _get_topic_subscriptions(
    *,
    access_token: str,
    topic_id: str,
) -> list[dict[str, Any]]:
    response = requests.get(
        f"{NOTIFICATION_BASE_URL}/subscription",
        headers=_headers(access_token),
        params={
            "topic_id": topic_id,
            "limit": 100,
        },
        timeout=30,
    )

    if response.status_code == 409:
        return []

    if response.status_code != 200:
        _api_error("get eBay notification subscriptions", response)

    payload = _safe_response_payload(response)

    if not isinstance(payload, dict):
        return []

    rows = payload.get("subscriptions")
    return rows if isinstance(rows, list) else []


def _create_subscription(
    *,
    access_token: str,
    destination_id: str,
    topic_id: str,
    schema_version: str,
) -> str:
    response = requests.post(
        f"{NOTIFICATION_BASE_URL}/subscription",
        headers=_headers(access_token),
        json={
            "topicId": topic_id,
            "destinationId": destination_id,
            "status": "ENABLED",
            "payload": {
                "format": "JSON",
                "schemaVersion": schema_version,
                "deliveryProtocol": "HTTPS",
            },
        },
        timeout=30,
    )

    if response.status_code not in {200, 201}:
        _api_error("create eBay notification subscription", response)

    subscription_id = _extract_resource_id(response, "subscriptionId")

    if not subscription_id:
        raise EbayNotificationRegistrationError(
            "eBay created the subscription but returned no subscription ID."
        )

    return subscription_id


def _ensure_subscription(
    *,
    access_token: str,
    destination_id: str,
    topic_id: str,
    schema_version: str,
) -> tuple[str, bool]:
    subscriptions = _get_topic_subscriptions(
        access_token=access_token,
        topic_id=topic_id,
    )

    for subscription in subscriptions:
        if (
            subscription.get("topicId") == topic_id
            and str(subscription.get("destinationId")) == str(destination_id)
        ):
            subscription_id = subscription.get("subscriptionId")

            if not subscription_id:
                continue

            status = str(subscription.get("status") or "").upper()

            if status == "DISABLED":
                response = requests.post(
                    f"{NOTIFICATION_BASE_URL}/subscription/"
                    f"{subscription_id}/enable",
                    headers=_headers(access_token),
                    timeout=30,
                )

                if response.status_code != 204:
                    _api_error("enable eBay notification subscription", response)

            return str(subscription_id), False

    subscription_id = _create_subscription(
        access_token=access_token,
        destination_id=destination_id,
        topic_id=topic_id,
        schema_version=schema_version,
    )

    return subscription_id, True


def ensure_ebay_order_notification_registration(
    *,
    store: Any,
    access_token: str,
) -> dict[str, Any]:
    """Idempotently ensure BT38's production eBay order and listing webhooks."""

    from app import db

    endpoint = (
        os.getenv("EBAY_NOTIFICATION_ENDPOINT")
        or "https://bt38-prod.fly.dev/governed/webhooks/ebay"
    ).strip()

    verification_token = (
        os.getenv("EBAY_NOTIFICATION_VERIFICATION_TOKEN") or ""
    ).strip()

    destination_name = (
        os.getenv("EBAY_NOTIFICATION_DESTINATION_NAME")
        or DEFAULT_DESTINATION_NAME
    ).strip()

    _validate_endpoint(endpoint)
    _validate_verification_token(verification_token)

    destination_id, destination_created = _ensure_destination(
        access_token=access_token,
        endpoint=endpoint,
        verification_token=verification_token,
        destination_name=destination_name,
    )

    subscriptions = []

    order_subscription_id, order_created = _ensure_subscription(
        access_token=access_token,
        destination_id=destination_id,
        topic_id=ORDER_TOPIC_ID,
        schema_version=TOPIC_SCHEMA_VERSIONS[ORDER_TOPIC_ID],
    )
    order_subscription = {
        "topic_id": ORDER_TOPIC_ID,
        "schema_version": TOPIC_SCHEMA_VERSIONS[ORDER_TOPIC_ID],
        "subscription_id": order_subscription_id,
        "subscription_created": order_created,
        "status": "ENABLED",
        "ok": True,
    }
    subscriptions.append(order_subscription)

    creds = _decode_store_credentials(store)
    listing_status = str(
        creds.get("ebay_notification_listing_subscription_status") or ""
    ).upper()
    granted_scopes = set(
        str(creds.get("oauth_granted_scope") or "").split()
    )
    listing_scope_granted = LISTING_READ_SCOPE in granted_scopes

    if listing_status == "AUTHORIZATION_REQUIRED" and not listing_scope_granted:
        listing_subscription = {
            "topic_id": LISTING_TOPIC_ID,
            "schema_version": TOPIC_SCHEMA_VERSIONS[LISTING_TOPIC_ID],
            "subscription_id": None,
            "subscription_created": False,
            "status": "AUTHORIZATION_REQUIRED",
            "ok": False,
            "skipped": True,
            "error": (
                creds.get("ebay_notification_listing_subscription_error")
                or "eBay LISTING notifications require sell.listing.read; reconnect eBay to grant the governed listing-read scope."
            ),
        }
    else:
        try:
            listing_subscription_id, listing_created = _ensure_subscription(
                access_token=access_token,
                destination_id=destination_id,
                topic_id=LISTING_TOPIC_ID,
                schema_version=TOPIC_SCHEMA_VERSIONS[LISTING_TOPIC_ID],
            )
            listing_subscription = {
                "topic_id": LISTING_TOPIC_ID,
                "schema_version": TOPIC_SCHEMA_VERSIONS[LISTING_TOPIC_ID],
                "subscription_id": listing_subscription_id,
                "subscription_created": listing_created,
                "status": "ENABLED",
                "ok": True,
            }
        except EbayNotificationRegistrationError as exc:
            listing_error = str(exc)
            authorization_required = (
                "195011" in listing_error
                or "not authorized for this topic" in listing_error.lower()
            )
            listing_status = (
                "AUTHORIZATION_REQUIRED" if authorization_required else "FAILED"
            )
            listing_subscription = {
                "topic_id": LISTING_TOPIC_ID,
                "schema_version": TOPIC_SCHEMA_VERSIONS[LISTING_TOPIC_ID],
                "subscription_id": None,
                "subscription_created": False,
                "status": listing_status,
                "ok": False,
                "error": listing_error,
            }

    subscriptions.append(listing_subscription)

    subscription_id = order_subscription_id
    subscription_created = any(
        item["subscription_created"]
        for item in subscriptions
    )
    now = datetime.utcnow().isoformat()
    registration_ok = bool(order_subscription["ok"] and listing_subscription["ok"])
    registration_status = "SUCCESS" if registration_ok else "PARTIAL"

    creds.update({
        "ebay_notification_registration_status": registration_status,
        "ebay_notification_registration_error": listing_subscription.get("error"),
        "ebay_notification_endpoint": endpoint,
        "ebay_notification_destination_id": destination_id,
        "ebay_notification_destination_name": destination_name,
        "ebay_notification_destination_status": "ENABLED",
        "ebay_notification_order_topic_id": ORDER_TOPIC_ID,
        "ebay_notification_order_subscription_id": subscription_id,
        "ebay_notification_order_subscription_status": "ENABLED",
        "ebay_notification_listing_topic_id": LISTING_TOPIC_ID,
        "ebay_notification_listing_subscription_id": listing_subscription.get("subscription_id"),
        "ebay_notification_listing_subscription_status": listing_subscription["status"],
        "ebay_notification_listing_subscription_error": listing_subscription.get("error"),
        "ebay_notification_subscriptions": subscriptions,
        "ebay_notification_schema_version": TOPIC_SCHEMA_VERSIONS[ORDER_TOPIC_ID],
        "ebay_notification_topic_schema_versions": TOPIC_SCHEMA_VERSIONS,
        "ebay_notification_registered_at": now,
    })

    store.api_key = json.dumps(creds)
    db.session.commit()

    return {
        "ok": registration_ok,
        "destination_id": destination_id,
        "destination_created": destination_created,
        "subscription_id": subscription_id,
        "subscription_created": subscription_created,
        "subscriptions": subscriptions,
        "topic_id": ORDER_TOPIC_ID,
        "endpoint": endpoint,
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "listing_subscription": listing_subscription,
        "registration_status": registration_status,
    }
