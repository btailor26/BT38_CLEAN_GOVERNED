"""Governed exact eBay shipping-label finance readback.

Reads only the exact eBay order's Finances API SHIPPING_LABEL transactions and
persists confirmed monetary truth into the existing shipping_spend_ledger.
No marketplace write, no order creation, no shipment creation, no polling.

For UK/EU sellers eBay requires RFC 9421 digital-signature headers on all
Finances API calls. This module therefore remains safely dormant unless the
existing production runtime has an eBay signing private key and public-key JWE.
"""
from __future__ import annotations

import base64
import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import requests
from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC, RSA
from Crypto.Signature import eddsa, pkcs1_15
from sqlalchemy import text

from extensions import db
from services.governed_marketplace_order_import import (
    EBAY_TOKEN_URL,
    _parse_ebay_datetime,
    _store_credentials,
    _text,
)

EBAY_FINANCES_TRANSACTIONS_URL = "https://api.ebay.com/sell/finances/v1/transaction"
EBAY_FINANCES_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.finances"


def _finance_access_token(store) -> str:
    creds = _store_credentials(store)
    refresh_token = creds.get("refresh_token")
    client_id = os.getenv("EBAY_CLIENT_ID") or creds.get("client_id")
    client_secret = os.getenv("EBAY_CLIENT_SECRET") or creds.get("client_secret")
    if not refresh_token or not client_id or not client_secret:
        raise RuntimeError("missing_ebay_credentials_for_finances_read")

    response = requests.post(
        EBAY_TOKEN_URL,
        auth=(client_id, client_secret),
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": EBAY_FINANCES_SCOPE,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"ebay_finances_token_refresh_failed:{response.status_code}:{response.text[:500]}"
        )
    token = _text((response.json() or {}).get("access_token"))
    if not token:
        raise RuntimeError("ebay_finances_token_refresh_missing_access_token")
    return token


def _signature_material() -> tuple[str, str] | tuple[None, None]:
    private_key = _text(os.getenv("EBAY_SIGNATURE_PRIVATE_KEY"))
    public_jwe = _text(os.getenv("EBAY_SIGNATURE_PUBLIC_KEY_JWE"))
    if not private_key or not public_jwe:
        return None, None
    private_key = private_key.replace("\\n", "\n")
    return private_key, public_jwe


def _signature_headers(*, method: str, url: str) -> dict[str, str]:
    private_key, public_jwe = _signature_material()
    if not private_key or not public_jwe:
        raise RuntimeError("ebay_finances_signature_credentials_missing")

    parsed = urlsplit(url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    authority = parsed.netloc
    created = int(time.time())
    signature_input = (
        'sig1=("x-ebay-signature-key" "@method" "@path" "@authority");'
        f"created={created}"
    )
    signature_base = "\n".join(
        [
            f'"x-ebay-signature-key": {public_jwe}',
            f'"@method": {method.upper()}',
            f'"@path": {path}',
            f'"@authority": {authority}',
            (
                '"@signature-params": '
                f'("x-ebay-signature-key" "@method" "@path" "@authority");created={created}'
            ),
        ]
    ).encode("ascii")

    try:
        rsa_key = RSA.import_key(private_key)
    except (ValueError, IndexError, TypeError):
        rsa_key = None

    if rsa_key is not None:
        digest = SHA256.new(signature_base)
        signed = pkcs1_15.new(rsa_key).sign(digest)
    else:
        try:
            ecc_key = ECC.import_key(private_key)
            signed = eddsa.new(ecc_key, "rfc8032").sign(signature_base)
        except Exception as exc:
            raise RuntimeError("ebay_finances_signature_private_key_invalid") from exc

    return {
        "x-ebay-signature-key": public_jwe,
        "Signature-Input": signature_input,
        "Signature": f"sig1=:{base64.b64encode(signed).decode('ascii')}:",
    }


def _transaction_amount(transaction: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    amount = transaction.get("amount") or {}
    try:
        value = Decimal(str(amount.get("value")))
    except (InvalidOperation, TypeError, ValueError):
        return None, None
    currency = _text(amount.get("currency")).upper()
    return value, currency or None


def _persist_shipping_label_transaction(*, store, order_id: str, transaction: dict[str, Any]) -> bool:
    transaction_id = _text(transaction.get("transactionId"))
    if not transaction_id:
        return False
    if _text(transaction.get("transactionType")).upper() != "SHIPPING_LABEL":
        return False
    if _text(transaction.get("orderId")) != order_id:
        return False

    amount, currency = _transaction_amount(transaction)
    if amount is None or not currency:
        return False

    transaction_date = _parse_ebay_datetime(transaction.get("transactionDate")) or datetime.utcnow()
    memo = _text(transaction.get("transactionMemo")).lower()
    booking_entry = _text(transaction.get("bookingEntry")).upper()
    source = "ebay_finances_shipping_label"
    if "refund" in memo or booking_entry == "CREDIT":
        source = "ebay_finances_shipping_label_refund"
    elif "adjust" in memo:
        source = "ebay_finances_shipping_label_adjustment"

    dispatch_key = f"ebay_shipping_label:{store.id}:{transaction_id}"
    result = db.session.execute(
        text(
            """
            INSERT INTO shipping_spend_ledger (
                dispatch_key,
                shipment_id,
                store_id,
                marketplace_order_id,
                fulfillment_family,
                provider,
                amount,
                currency,
                source,
                source_reference,
                confirmed,
                recorded_at,
                created_at,
                updated_at
            ) VALUES (
                :dispatch_key,
                NULL,
                :store_id,
                :order_id,
                'FBM',
                'ebay',
                :amount,
                :currency,
                :source,
                :source_reference,
                TRUE,
                :recorded_at,
                :recorded_at,
                :recorded_at
            )
            ON CONFLICT (dispatch_key) DO UPDATE SET
                amount = EXCLUDED.amount,
                currency = EXCLUDED.currency,
                source = EXCLUDED.source,
                source_reference = EXCLUDED.source_reference,
                confirmed = TRUE,
                recorded_at = EXCLUDED.recorded_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "dispatch_key": dispatch_key,
            "store_id": int(store.id),
            "order_id": order_id,
            "amount": amount,
            "currency": currency,
            "source": source,
            "source_reference": transaction_id,
            "recorded_at": transaction_date,
        },
    )
    return result is not None


def read_and_persist_exact_ebay_shipping_label_purchase(*, store, marketplace_order_id: str) -> dict[str, Any]:
    """Read exact eBay SHIPPING_LABEL finance truth for one existing order.

    This is an explicit/session-driven read helper. It does not schedule, poll,
    create FBMShipment rows, or infer carrier ownership from tracking formats.
    """
    order_id = _text(marketplace_order_id)
    if not order_id:
        return {"success": False, "skipped": True, "reason": "ebay_order_id_missing"}

    private_key, public_jwe = _signature_material()
    if not private_key or not public_jwe:
        return {
            "success": False,
            "skipped": True,
            "reason": "ebay_finances_signature_credentials_missing",
            "order_id": order_id,
        }

    try:
        token = _finance_access_token(store)
    except Exception as exc:
        return {"success": False, "skipped": False, "reason": str(exc), "order_id": order_id}

    request = requests.Request(
        "GET",
        EBAY_FINANCES_TRANSACTIONS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        params={
            "filter": f"transactionType:{{SHIPPING_LABEL}},orderId:{{{order_id}}}",
            "limit": "100",
        },
    )
    prepared = request.prepare()
    prepared.headers.update(_signature_headers(method="GET", url=prepared.url))

    response = requests.Session().send(prepared, timeout=30)
    if response.status_code >= 400:
        return {
            "success": False,
            "skipped": False,
            "reason": "ebay_finances_shipping_label_read_failed",
            "status_code": response.status_code,
            "error": response.text[:1000],
            "order_id": order_id,
        }

    payload = response.json() or {}
    transactions = [
        row for row in (payload.get("transactions") or [])
        if isinstance(row, dict)
        and _text(row.get("transactionType")).upper() == "SHIPPING_LABEL"
        and _text(row.get("orderId")) == order_id
    ]
    persisted = 0
    purchase_transactions = 0
    for transaction in transactions:
        memo = _text(transaction.get("transactionMemo")).lower()
        booking_entry = _text(transaction.get("bookingEntry")).upper()
        if booking_entry == "DEBIT" and "refund" not in memo:
            purchase_transactions += 1
        if _persist_shipping_label_transaction(
            store=store,
            order_id=order_id,
            transaction=transaction,
        ):
            persisted += 1

    if persisted:
        db.session.commit()

    return {
        "success": True,
        "skipped": False,
        "order_id": order_id,
        "transactions_seen": len(transactions),
        "transactions_persisted": persisted,
        "purchase_transactions": purchase_transactions,
        "purchase_confirmed": purchase_transactions > 0,
        "marketplace_write_started": False,
        "shipment_created": False,
    }
