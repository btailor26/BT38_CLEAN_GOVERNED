"""
Persistent governed webhook capture.

Purpose:
- Store the complete marketplace request before parsing or business processing.
- Keep eBay and Amazon storage completely separate.
- Preserve headers, query parameters, raw body and decoded JSON.
- Return the database notification record ID for downstream alignment.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from flask import Request
from sqlalchemy import text

from extensions import db


_SECRET_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
}


def _safe_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    for key, value in headers.items():
        name = str(key)
        lowered = name.lower()

        if lowered in _SECRET_HEADERS:
            captured[name] = "[REDACTED]"
        else:
            captured[name] = str(value)

    return captured


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"unserializable_value": str(value)}


def _request_payload(request: Request) -> tuple[str, Any]:
    raw_body = request.get_data(cache=True, as_text=True) or ""

    payload = request.get_json(silent=True)

    if payload is None and raw_body:
        try:
            payload = json.loads(raw_body)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None

    return raw_body, _safe_json(payload)


def _request_metadata(request: Request) -> dict[str, Any]:
    forwarded_for = request.headers.get("X-Forwarded-For")
    source_ip = (
        forwarded_for.split(",", 1)[0].strip()
        if forwarded_for
        else request.remote_addr
    )

    return {
        "request_method": request.method,
        "request_path": request.path,
        "source_ip": source_ip,
        "user_agent": request.headers.get("User-Agent"),
        "content_type": request.content_type,
        "headers_json": _safe_headers(request.headers),
        "query_params_json": {
            key: request.args.getlist(key)
            for key in request.args.keys()
        },
    }


def _first_value(payload: Any, *paths: tuple[str, ...]) -> Optional[Any]:
    if not isinstance(payload, dict):
        return None

    for path in paths:
        current: Any = payload

        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break

            current = current[key]

        if current not in (None, ""):
            return current

    return None


def capture_ebay_notification(
    request: Request,
    *,
    commit: bool = True,
) -> int:
    """
    Persist the raw eBay request before validation or parsing.

    The function extracts only obvious metadata for indexing.
    The complete request remains preserved in payload_json and raw_body.
    """

    raw_body, payload = _request_payload(request)
    metadata = _request_metadata(request)

    notification_id = _first_value(
        payload,
        ("notificationId",),
        ("notification", "notificationId"),
        ("metadata", "notificationId"),
    )

    event_id = _first_value(
        payload,
        ("eventId",),
        ("notification", "eventId"),
        ("metadata", "eventId"),
    )

    topic = _first_value(
        payload,
        ("topic",),
        ("notification", "topic"),
        ("metadata", "topic"),
    )

    seller_account_id = _first_value(
        payload,
        ("sellerAccountId",),
        ("seller", "sellerAccountId"),
        ("metadata", "sellerAccountId"),
    )

    marketplace_id = _first_value(
        payload,
        ("marketplaceId",),
        ("marketplace", "marketplaceId"),
        ("metadata", "marketplaceId"),
    )

    signature = (
        request.headers.get("X-EBAY-SIGNATURE")
        or request.headers.get("X-Ebay-Signature")
        or request.headers.get("Signature")
    )

    result = db.session.execute(
        text(
            """
            INSERT INTO webhooks.ebay_notifications (
                notification_id,
                event_id,
                topic,
                seller_account_id,
                marketplace_id,
                request_method,
                request_path,
                source_ip,
                user_agent,
                content_type,
                signature,
                verification_status,
                processing_status,
                headers_json,
                query_params_json,
                payload_json,
                raw_body,
                received_at
            )
            VALUES (
                :notification_id,
                :event_id,
                :topic,
                :seller_account_id,
                :marketplace_id,
                :request_method,
                :request_path,
                :source_ip,
                :user_agent,
                :content_type,
                :signature,
                'PENDING',
                'RECEIVED',
                CAST(:headers_json AS JSONB),
                CAST(:query_params_json AS JSONB),
                CAST(:payload_json AS JSONB),
                :raw_body,
                NOW()
            )
            ON CONFLICT (notification_id)
            WHERE notification_id IS NOT NULL
            DO UPDATE SET
                retry_count = webhooks.ebay_notifications.retry_count + 1,
                last_error = NULL
            RETURNING id
            """
        ),
        {
            "notification_id": (
                str(notification_id) if notification_id is not None else None
            ),
            "event_id": str(event_id) if event_id is not None else None,
            "topic": str(topic) if topic is not None else None,
            "seller_account_id": (
                str(seller_account_id)
                if seller_account_id is not None
                else None
            ),
            "marketplace_id": (
                str(marketplace_id)
                if marketplace_id is not None
                else None
            ),
            "request_method": metadata["request_method"],
            "request_path": metadata["request_path"],
            "source_ip": metadata["source_ip"],
            "user_agent": metadata["user_agent"],
            "content_type": metadata["content_type"],
            "signature": signature,
            "headers_json": json.dumps(metadata["headers_json"]),
            "query_params_json": json.dumps(metadata["query_params_json"]),
            "payload_json": json.dumps(payload),
            "raw_body": raw_body,
        },
    )

    notification_record_id = int(result.scalar_one())

    if commit:
        db.session.commit()

    return notification_record_id


def capture_amazon_notification(
    request: Request,
    *,
    commit: bool = True,
) -> int:
    """
    Persist the raw Amazon/SNS request before validation or parsing.

    Both the SNS envelope and decoded notification payload are retained.
    Direct SQS/SP-API notification bodies are also accepted as the payload.
    """

    raw_body, envelope = _request_payload(request)
    metadata = _request_metadata(request)

    message = (
        envelope.get("Message")
        if isinstance(envelope, dict)
        else None
    )

    payload: Any = message

    if isinstance(message, str):
        try:
            payload = json.loads(message)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {"raw_message": message}
    elif message is None and isinstance(envelope, dict):
        # The governed SQS runtime already unwraps Amazon's transport and
        # forwards the SP-API notification body directly. Preserve that same
        # body as the parsed payload so indexed metadata is not lost.
        payload = envelope

    payload = _safe_json(payload)

    notification_id = _first_value(
        payload,
        ("notificationId",),
        ("NotificationMetadata", "NotificationId"),
        ("notificationMetadata", "notificationId"),
    )

    notification_type = _first_value(
        payload,
        ("notificationType",),
        ("NotificationType",),
        ("notificationMetadata", "notificationType"),
    )

    subscription_id = _first_value(
        payload,
        ("subscriptionId",),
        ("SubscriptionId",),
    )

    seller_id = _first_value(
        payload,
        ("sellerId",),
        ("SellerId",),
        ("payload", "SellerId"),
    )

    marketplace_id = _first_value(
        payload,
        ("marketplaceId",),
        ("MarketplaceId",),
        ("payload", "MarketplaceId"),
    )

    message_id = _first_value(
        envelope,
        ("MessageId",),
        ("messageId",),
    )

    topic_arn = _first_value(
        envelope,
        ("TopicArn",),
        ("topicArn",),
    )

    subject = _first_value(
        envelope,
        ("Subject",),
        ("subject",),
    )

    signature_version = _first_value(
        envelope,
        ("SignatureVersion",),
        ("signatureVersion",),
    )

    signature = _first_value(
        envelope,
        ("Signature",),
        ("signature",),
    )

    signing_cert_url = _first_value(
        envelope,
        ("SigningCertURL",),
        ("SigningCertUrl",),
        ("signingCertUrl",),
    )

    unsubscribe_url = _first_value(
        envelope,
        ("UnsubscribeURL",),
        ("UnsubscribeUrl",),
        ("unsubscribeUrl",),
    )

    result = db.session.execute(
        text(
            """
            INSERT INTO webhooks.amazon_notifications (
                notification_id,
                notification_type,
                subscription_id,
                seller_id,
                marketplace_id,
                request_method,
                request_path,
                source_ip,
                user_agent,
                content_type,
                message_id,
                topic_arn,
                subject,
                signature_version,
                signature,
                signing_cert_url,
                unsubscribe_url,
                verification_status,
                processing_status,
                headers_json,
                query_params_json,
                envelope_json,
                payload_json,
                raw_body,
                received_at
            )
            VALUES (
                :notification_id,
                :notification_type,
                :subscription_id,
                :seller_id,
                :marketplace_id,
                :request_method,
                :request_path,
                :source_ip,
                :user_agent,
                :content_type,
                :message_id,
                :topic_arn,
                :subject,
                :signature_version,
                :signature,
                :signing_cert_url,
                :unsubscribe_url,
                'PENDING',
                'RECEIVED',
                CAST(:headers_json AS JSONB),
                CAST(:query_params_json AS JSONB),
                CAST(:envelope_json AS JSONB),
                CAST(:payload_json AS JSONB),
                :raw_body,
                NOW()
            )
            ON CONFLICT (message_id)
            WHERE message_id IS NOT NULL
            DO UPDATE SET
                retry_count = webhooks.amazon_notifications.retry_count + 1,
                last_error = NULL
            RETURNING id
            """
        ),
        {
            "notification_id": (
                str(notification_id) if notification_id is not None else None
            ),
            "notification_type": (
                str(notification_type)
                if notification_type is not None
                else None
            ),
            "subscription_id": (
                str(subscription_id)
                if subscription_id is not None
                else None
            ),
            "seller_id": str(seller_id) if seller_id is not None else None,
            "marketplace_id": (
                str(marketplace_id)
                if marketplace_id is not None
                else None
            ),
            "request_method": metadata["request_method"],
            "request_path": metadata["request_path"],
            "source_ip": metadata["source_ip"],
            "user_agent": metadata["user_agent"],
            "content_type": metadata["content_type"],
            "message_id": str(message_id) if message_id is not None else None,
            "topic_arn": str(topic_arn) if topic_arn is not None else None,
            "subject": str(subject) if subject is not None else None,
            "signature_version": (
                str(signature_version)
                if signature_version is not None
                else None
            ),
            "signature": str(signature) if signature is not None else None,
            "signing_cert_url": (
                str(signing_cert_url)
                if signing_cert_url is not None
                else None
            ),
            "unsubscribe_url": (
                str(unsubscribe_url)
                if unsubscribe_url is not None
                else None
            ),
            "headers_json": json.dumps(metadata["headers_json"]),
            "query_params_json": json.dumps(metadata["query_params_json"]),
            "envelope_json": json.dumps(envelope),
            "payload_json": json.dumps(payload),
            "raw_body": raw_body,
        },
    )

    notification_record_id = int(result.scalar_one())

    if commit:
        db.session.commit()

    return notification_record_id


def _assert_amazon_order_change_canonical_order(
    notification_record_id: int,
    *,
    commit: bool,
) -> None:
    """Refuse a silent COMPLETED state when canonical order intake is missing."""
    notification = db.session.execute(
        text(
            """
            SELECT
                notification_type,
                payload_json #>> '{Payload,OrderChangeNotification,AmazonOrderId}' AS order_id
            FROM webhooks.amazon_notifications
            WHERE id = :notification_record_id
            """
        ),
        {"notification_record_id": notification_record_id},
    ).mappings().first()

    if not notification:
        return

    notification_type = str(
        notification.get("notification_type") or ""
    ).strip().upper()
    order_id = str(notification.get("order_id") or "").strip()

    if notification_type != "ORDER_CHANGE" or not order_id:
        return

    order_exists = db.session.execute(
        text(
            """
            SELECT 1
            FROM marketplace_orders
            WHERE marketplace_order_id = :order_id
            LIMIT 1
            """
        ),
        {"order_id": order_id},
    ).scalar()

    if order_exists:
        return

    error = f"canonical_order_missing_after_order_change:{order_id}"
    db.session.execute(
        text(
            """
            UPDATE webhooks.amazon_notifications
            SET processing_status = 'FAILED',
                last_error = :error,
                completed_at = NOW()
            WHERE id = :notification_record_id
            """
        ),
        {
            "notification_record_id": notification_record_id,
            "error": error,
        },
    )
    if commit:
        db.session.commit()
    raise RuntimeError(error)


def mark_notification_status(
    marketplace: str,
    notification_record_id: int,
    *,
    processing_status: Optional[str] = None,
    verification_status: Optional[str] = None,
    signature_valid: Optional[bool] = None,
    last_error: Optional[str] = None,
    parsed: bool = False,
    completed: bool = False,
    increment_retry: bool = False,
    commit: bool = True,
) -> None:
    platform = marketplace.strip().lower()

    if platform not in {"ebay", "amazon"}:
        raise ValueError(f"Unsupported marketplace: {marketplace!r}")

    if (
        platform == "amazon"
        and completed
        and str(processing_status or "").strip().upper() == "COMPLETED"
    ):
        _assert_amazon_order_change_canonical_order(
            notification_record_id,
            commit=commit,
        )

    table_name = (
        "webhooks.ebay_notifications"
        if platform == "ebay"
        else "webhooks.amazon_notifications"
    )

    assignments = []
    parameters: dict[str, Any] = {
        "notification_record_id": notification_record_id,
    }

    if processing_status is not None:
        assignments.append("processing_status = :processing_status")
        parameters["processing_status"] = processing_status

    if verification_status is not None:
        assignments.append("verification_status = :verification_status")
        parameters["verification_status"] = verification_status

    if signature_valid is not None:
        assignments.append("signature_valid = :signature_valid")
        parameters["signature_valid"] = signature_valid

    if last_error is not None:
        assignments.append("last_error = :last_error")
        parameters["last_error"] = last_error

    if parsed:
        assignments.append("parsed_at = NOW()")

    if completed:
        assignments.append("completed_at = NOW()")

    if increment_retry:
        assignments.append("retry_count = retry_count + 1")

    if not assignments:
        return

    db.session.execute(
        text(
            f"""
            UPDATE {table_name}
            SET {", ".join(assignments)}
            WHERE id = :notification_record_id
            """
        ),
        parameters,
    )

    if commit:
        db.session.commit()