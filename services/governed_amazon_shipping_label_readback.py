"""Recover Seller Central-purchased Amazon Buy Shipping shipment authority.

This is an exact, on-demand readback for existing Amazon FBM orders. It does not
import orders, buy postage, confirm shipment, mutate inventory, start a worker,
or create a parallel shipment model. Amazon Finances is used only to discover
shipment identifiers related to the exact order. Every candidate is then
validated with Merchant Fulfillment getShipment before the existing FBMShipment
row/table is updated.

Tracking is deliberately optional. A valid Amazon ShipmentId is durable shipment
and label authority even when the purchased service is untracked.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder
from services.fbm_amazon_shipping_adapter import AmazonShippingAdapter
from services.governed_amazon_tracking_readback import (
    SP_API_EU_ENDPOINT,
    _lwa_access_token,
    _request_headers,
)


FINANCES_TRANSACTIONS_PATH = "/finances/2024-06-19/transactions"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_iso(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _payload(response: Any) -> dict[str, Any]:
    value = getattr(response, "payload", None)
    if value is None and hasattr(response, "json"):
        value = response.json()
    if value is None and isinstance(response, dict):
        value = response.get("payload", response)
    if isinstance(value, dict) and isinstance(value.get("payload"), dict):
        value = value["payload"]
    return value if isinstance(value, dict) else {}


def _finance_payload(body: Any) -> dict[str, Any]:
    """Accept both Amazon's documented and observed payload envelopes."""
    if not isinstance(body, dict):
        return {}
    nested = body.get("payload")
    return nested if isinstance(nested, dict) else body


def _identifier_pairs(value: Any) -> list[tuple[str, str]]:
    """Collect transaction/item identifiers without trusting enum completeness."""
    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        name = _text(
            value.get("relatedIdentifierName")
            or value.get("itemRelatedIdentifierName")
            or value.get("name")
        )
        identifier = _text(
            value.get("relatedIdentifierValue")
            or value.get("itemRelatedIdentifierValue")
            or value.get("value")
        )
        if name and identifier:
            pairs.append((name.upper(), identifier))
        for child in value.values():
            pairs.extend(_identifier_pairs(child))
    elif isinstance(value, list):
        for child in value:
            pairs.extend(_identifier_pairs(child))
    return pairs


def _transaction_has_exact_order(transaction: dict[str, Any], order_id: str) -> bool:
    return any(
        name == "ORDER_ID" and value == order_id
        for name, value in _identifier_pairs(transaction.get("relatedIdentifiers") or [])
    )


def _shipment_candidates(transactions: list[dict[str, Any]], order_id: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for transaction in transactions:
        if not isinstance(transaction, dict) or not _transaction_has_exact_order(transaction, order_id):
            continue
        for name, value in _identifier_pairs(transaction):
            if "SHIPMENT" not in name or name == "ORDER_ID" or not value or value in seen:
                continue
            seen.add(value)
            candidates.append(value)
    return candidates


def _list_finance_transactions(*, store: Any, order_id: str) -> dict[str, Any]:
    """Read Amazon Transaction View data for one exact order; never writes Amazon."""
    access_token = _lwa_access_token(store)
    response = requests.get(
        f"{SP_API_EU_ENDPOINT}{FINANCES_TRANSACTIONS_PATH}",
        params={
            "relatedIdentifierName": "ORDER_ID",
            "relatedIdentifierValue": order_id,
        },
        headers=_request_headers(access_token),
        timeout=30,
    )
    if response.status_code >= 400:
        return {
            "success": False,
            "reason": "amazon_finances_transactions_read_failed",
            "status_code": response.status_code,
            "error": response.text[:1000],
            "transactions": [],
        }
    body = _finance_payload(response.json() or {})
    rows = [row for row in (body.get("transactions") or []) if isinstance(row, dict)]
    return {
        "success": True,
        "reason": None,
        "transactions": rows,
        "next_token": _text(body.get("nextToken")) or None,
    }


def _merchant_shipment(*, store: Any, shipment_id: str) -> dict[str, Any]:
    adapter = AmazonShippingAdapter(store)
    client = adapter._client()
    method = getattr(client, "get_shipment", None)
    if method is None:
        return {"success": False, "reason": "amazon_get_shipment_unavailable"}
    try:
        response = method(shipment_id)
    except Exception as exc:
        return {
            "success": False,
            "reason": "amazon_get_shipment_failed",
            "error": str(exc)[:1000],
        }
    payload = _payload(response)
    if not payload:
        return {"success": False, "reason": "amazon_get_shipment_payload_invalid"}
    return {"success": True, "shipment": payload}


def _shipment_values(payload: dict[str, Any]) -> dict[str, Any]:
    service = payload.get("ShippingService") if isinstance(payload.get("ShippingService"), dict) else {}
    label = payload.get("Label") if isinstance(payload.get("Label"), dict) else {}
    dimensions = label.get("Dimensions") if isinstance(label.get("Dimensions"), dict) else {}
    file_contents = label.get("FileContents") if isinstance(label.get("FileContents"), dict) else {}
    tracking = _text(payload.get("TrackingId") or service.get("TrackingId")) or None
    carrier = _text(service.get("CarrierName") or payload.get("CarrierName")) or None
    service_name = _text(service.get("ShippingServiceName") or payload.get("ShippingServiceName")) or None
    service_id = _text(service.get("ShippingServiceId") or payload.get("ShippingServiceId")) or None
    status = _text(payload.get("Status") or payload.get("ShipmentStatus")) or None
    shipment_id = _text(payload.get("ShipmentId")) or None
    order_id = _text(payload.get("AmazonOrderId")) or None
    label_format = _text(label.get("LabelFormat")) or None
    if label_format == "ShippingServiceDefault":
        file_type = _text(file_contents.get("FileType")).lower()
        if "pdf" in file_type:
            label_format = "PDF"
        elif "png" in file_type:
            label_format = "PNG"
        elif "zpl" in file_type:
            label_format = "ZPL"
        else:
            label_format = None
    return {
        "shipment_id": shipment_id,
        "order_id": order_id,
        "carrier": carrier,
        "service": service_name,
        "service_id": service_id,
        "tracking_number": tracking,
        "status": status,
        "created_at": _parse_iso(payload.get("CreatedDate")),
        "label_format": label_format,
        "label_width": dimensions.get("Width"),
        "label_length": dimensions.get("Length"),
        "label_unit": _text(dimensions.get("Unit")) or None,
    }


def _persist_validated_shipment(
    *,
    store: Any,
    order_id: str,
    candidate_shipment_id: str,
    payload: dict[str, Any],
) -> FBMShipment:
    values = _shipment_values(payload)
    returned_order_id = values["order_id"]
    returned_shipment_id = values["shipment_id"]
    if returned_order_id != order_id:
        raise ValueError(f"amazon_shipment_order_identity_mismatch:{returned_order_id or 'missing'}")
    if returned_shipment_id and returned_shipment_id != candidate_shipment_id:
        raise ValueError(f"amazon_shipment_identity_mismatch:{returned_shipment_id}")

    shipment = (
        FBMShipment.query.filter_by(
            store_id=store.id,
            provider="amazon_buy_shipping",
            provider_shipment_id=candidate_shipment_id,
        ).order_by(FBMShipment.id.desc()).first()
    )
    if shipment is None:
        shipment = FBMShipment(
            store_id=store.id,
            marketplace_order_id=order_id,
            provider="amazon_buy_shipping",
            provider_shipment_id=candidate_shipment_id,
            purchase_key=f"amazon_buy_shipping_recovered:{store.id}:{candidate_shipment_id}",
        )

    shipment.marketplace_order_id = order_id
    shipment.provider = "amazon_buy_shipping"
    shipment.provider_shipment_id = candidate_shipment_id
    if values["carrier"]:
        shipment.carrier = values["carrier"]
        shipment.provider_carrier_id = values["carrier"]
    if values["service"]:
        shipment.service = values["service"]
    if values["service_id"]:
        shipment.provider_service_id = values["service_id"]
    if values["tracking_number"]:
        shipment.tracking_number = values["tracking_number"]
    shipment.purchase_status = "purchased"
    shipment.purchase_error = None
    shipment.label_purchased_at = shipment.label_purchased_at or values["created_at"] or datetime.utcnow()
    shipment.label_source = "amazon_buy_shipping"
    if values["label_format"]:
        shipment.label_format = values["label_format"]
    if values["label_width"] is not None:
        shipment.label_width = values["label_width"]
    if values["label_length"] is not None:
        shipment.label_length = values["label_length"]
    if values["label_unit"]:
        shipment.label_size_unit = values["label_unit"]
    shipment.last_provider_status = values["status"] or shipment.last_provider_status
    shipment.last_provider_checked_at = datetime.utcnow()
    if _text(values["status"]).lower() in {"purchased", "created"}:
        shipment.status = "purchased"
    elif values["status"]:
        shipment.status = _text(values["status"]).lower()
    else:
        shipment.status = shipment.status or "purchased"
    db.session.add(shipment)

    # Keep the existing marketplace order aligned with Amazon's proven physical
    # carrier/tracking truth. Tracking remains optional for untracked services.
    rows = MarketplaceOrder.query.filter_by(
        store_id=store.id,
        marketplace_order_id=order_id,
    ).all()
    for row in rows:
        if values["carrier"]:
            row.carrier = values["carrier"]
        if values["tracking_number"]:
            row.tracking_number = values["tracking_number"]
        row.updated_at = datetime.utcnow()

    db.session.commit()
    return shipment


def hydrate_amazon_purchased_label_for_order(
    *,
    store: Any,
    marketplace_order_id: str,
    source: str = "amazon_finances_merchant_fulfillment",
) -> dict[str, Any]:
    """Recover one Seller Central-purchased label into existing BT38 shipment state."""
    order_id = _text(marketplace_order_id)
    if not order_id:
        return {"success": False, "skipped": True, "reason": "amazon_order_id_missing"}

    rows = MarketplaceOrder.query.filter_by(
        store_id=store.id,
        marketplace_order_id=order_id,
    ).all()
    eligible = [
        row for row in rows
        if _text(getattr(row, "fulfillment_type", "")).upper() not in {"FBA", "AFN", "MCF"}
        and not _text(getattr(row, "status", "")).lower().startswith("mcf_")
    ]
    if not eligible:
        return {
            "success": True,
            "skipped": True,
            "reason": "existing_amazon_fbm_order_missing",
            "marketplace_write_started": False,
        }

    existing = (
        FBMShipment.query.filter_by(
            store_id=store.id,
            marketplace_order_id=order_id,
            provider="amazon_buy_shipping",
        ).filter(FBMShipment.provider_shipment_id.isnot(None))
        .order_by(FBMShipment.id.desc()).first()
    )
    if existing is not None and _text(existing.purchase_status).lower() == "purchased":
        return {
            "success": True,
            "skipped": True,
            "reason": "amazon_purchased_label_already_persisted",
            "shipment_id": existing.provider_shipment_id,
            "tracking_number": existing.tracking_number,
            "marketplace_write_started": False,
        }

    finance = _list_finance_transactions(store=store, order_id=order_id)
    if not finance.get("success"):
        return {
            **finance,
            "order_id": order_id,
            "marketplace_write_started": False,
        }

    candidates = _shipment_candidates(finance.get("transactions") or [], order_id)
    if not candidates:
        return {
            "success": True,
            "skipped": True,
            "reason": "amazon_finances_shipment_identifier_not_available",
            "order_id": order_id,
            "finance_transactions": len(finance.get("transactions") or []),
            "next_token_present": bool(finance.get("next_token")),
            "marketplace_write_started": False,
        }

    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        result = _merchant_shipment(store=store, shipment_id=candidate)
        if not result.get("success"):
            rejected.append({"shipment_id": candidate, "reason": result.get("reason")})
            continue
        payload = result.get("shipment") or {}
        values = _shipment_values(payload)
        if values.get("order_id") != order_id:
            rejected.append({
                "shipment_id": candidate,
                "reason": "amazon_shipment_order_identity_mismatch",
                "returned_order_id": values.get("order_id"),
            })
            continue
        if values.get("shipment_id") and values.get("shipment_id") != candidate:
            rejected.append({
                "shipment_id": candidate,
                "reason": "amazon_shipment_identity_mismatch",
                "returned_shipment_id": values.get("shipment_id"),
            })
            continue

        shipment = _persist_validated_shipment(
            store=store,
            order_id=order_id,
            candidate_shipment_id=candidate,
            payload=payload,
        )
        return {
            "success": True,
            "skipped": False,
            "reason": None,
            "order_id": order_id,
            "shipment_id": shipment.provider_shipment_id,
            "carrier": shipment.carrier,
            "service": shipment.service,
            "tracking_number": shipment.tracking_number,
            "untracked": not bool(_text(shipment.tracking_number)),
            "purchase_status": shipment.purchase_status,
            "source": source,
            "marketplace_write_started": False,
        }

    return {
        "success": True,
        "skipped": True,
        "reason": "amazon_finances_shipment_candidates_not_merchant_fulfillment_labels",
        "order_id": order_id,
        "shipment_candidates": candidates,
        "rejected": rejected,
        "marketplace_write_started": False,
    }
