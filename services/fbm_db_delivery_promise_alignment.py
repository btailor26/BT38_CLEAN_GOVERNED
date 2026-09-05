"""Read-only FBM delivery-promise alignment for the server-rendered desk.

Delivery promises are marketplace-owned facts already persisted by the governed
order/profile hydration paths. The FBM page must display those stored facts
without performing a marketplace read while the page renders.
"""
from __future__ import annotations

from typing import Any

from flask import before_render_template
from sqlalchemy import bindparam, text, tuple_

from extensions import db
from fbm_models import FBMOrderProfile


_OPERATIONAL_FIELDS = (
    "shipping_service",
    "ship_by_at",
    "earliest_delivery_at",
    "latest_delivery_at",
)


def _profile_promises(keys: set[tuple[int, str]]) -> dict[tuple[int, str], dict[str, Any]]:
    """Use the existing FBM profile as the safe persisted fallback.

    FBMOrderProfile already owns the marketplace shipping service and latest ship
    time for both Amazon and eBay. This prevents the desk from showing Pending
    merely because an older operational-state table is absent or only partially
    migrated.
    """
    if not keys:
        return {}

    identities = sorted(keys)
    rows = (
        db.session.query(FBMOrderProfile)
        .filter(tuple_(FBMOrderProfile.store_id, FBMOrderProfile.marketplace_order_id).in_(identities))
        .order_by(FBMOrderProfile.updated_at.desc(), FBMOrderProfile.id.desc())
        .all()
    )
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row.store_id), str(row.marketplace_order_id))
        if key in result:
            continue
        result[key] = {
            "shipping_service": row.shipment_service_level,
            "ship_by_at": row.latest_ship_at,
            "earliest_delivery_at": None,
            "latest_delivery_at": None,
            "source": "fbm_order_profiles",
        }
    return result


def _operational_promises(keys: set[tuple[int, str]]) -> dict[tuple[int, str], dict[str, Any]]:
    """Read whatever delivery-promise columns exist in the additive state table.

    Older deployed schemas may not contain every optional promise column. The
    previous all-or-nothing SELECT caused one missing column to discard valid
    Ship by / Deliver by facts for every FBM row. Discovering the available
    columns first keeps this read backward compatible without mutating schema.
    """
    if not keys:
        return {}

    try:
        available = {
            str(row[0])
            for row in db.session.execute(
                text(
                    """
                    SELECT column_name
                      FROM information_schema.columns
                     WHERE table_schema = current_schema()
                       AND table_name = 'fbm_order_operational_state'
                    """
                )
            ).all()
        }
    except Exception:
        db.session.rollback()
        return {}

    required = {"store_id", "marketplace_order_id"}
    if not required.issubset(available):
        return {}

    store_ids = sorted({key[0] for key in keys})
    order_ids = sorted({key[1] for key in keys})
    select_fields = []
    for field in _OPERATIONAL_FIELDS:
        select_fields.append(field if field in available else f"NULL AS {field}")

    statement = text(
        f"""
        SELECT store_id,
               marketplace_order_id,
               {', '.join(select_fields)}
          FROM fbm_order_operational_state
         WHERE store_id IN :store_ids
           AND marketplace_order_id IN :order_ids
        """
    ).bindparams(
        bindparam("store_ids", expanding=True),
        bindparam("order_ids", expanding=True),
    )

    try:
        rows = db.session.execute(
            statement,
            {"store_ids": store_ids, "order_ids": order_ids},
        ).mappings().all()
    except Exception:
        db.session.rollback()
        return {}

    return {
        (int(row["store_id"]), str(row["marketplace_order_id"])): {
            "shipping_service": row["shipping_service"],
            "ship_by_at": row["ship_by_at"],
            "earliest_delivery_at": row["earliest_delivery_at"],
            "latest_delivery_at": row["latest_delivery_at"],
            "source": "fbm_order_operational_state",
        }
        for row in rows
        if (int(row["store_id"]), str(row["marketplace_order_id"])) in keys
    }


def _merge_promise(
    fallback: dict[str, Any] | None,
    operational: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if fallback is None and operational is None:
        return None
    merged = dict(fallback or {})
    if operational:
        for field in _OPERATIONAL_FIELDS:
            value = operational.get(field)
            if value is not None:
                merged[field] = value
        merged["source"] = operational.get("source") or merged.get("source")
    return merged


def install_fbm_db_delivery_promise_alignment(app: Any) -> None:
    if getattr(app, "_bt38_fbm_db_delivery_promise_alignment", False):
        return

    # Keep the DB-only reader and the existing bounded Amazon persistence repair
    # on the same governed install path. This restores the missing registration;
    # the FBM render itself still performs no marketplace read.
    from services.governed_amazon_fbm_profile_event_alignment import (
        install_governed_amazon_fbm_profile_event_alignment,
    )
    install_governed_amazon_fbm_profile_event_alignment(app)

    @before_render_template.connect_via(app)
    def _inject_fbm_delivery_promises(sender, template, context, **extra):
        if getattr(template, "name", None) != "fbm.html":
            return

        items = context.get("orders") or []
        keys = {
            (
                int(getattr(item.get("order"), "store_id", 0) or 0),
                str(getattr(item.get("order"), "marketplace_order_id", "") or "").strip(),
            )
            for item in items
            if isinstance(item, dict) and item.get("order") is not None
        }
        keys = {key for key in keys if key[0] > 0 and key[1]}
        if not keys:
            return

        profile_promises = _profile_promises(keys)
        operational_promises = _operational_promises(keys)

        for item in items:
            if not isinstance(item, dict):
                continue
            order = item.get("order")
            key = (
                int(getattr(order, "store_id", 0) or 0),
                str(getattr(order, "marketplace_order_id", "") or "").strip(),
            )
            promise = _merge_promise(
                profile_promises.get(key),
                operational_promises.get(key),
            )
            item["delivery_promise"] = promise

            # Amazon packages expose the actual package shippingService, so that
            # exact persisted package fact may enrich the presentation-only
            # marketplace shipment. eBay's shippingService is buyer-selected
            # promise context and must never override the physical label/
            # fulfillment authority chosen by the shipment map.
            shipment = item.get("shipment")
            provider = str(getattr(shipment, "provider", "") or "").strip().lower()
            service = str((promise or {}).get("shipping_service") or "").strip()
            platform = str(
                item.get("platform")
                or getattr(getattr(order, "store", None), "platform", "")
                or ""
            ).strip().lower()
            if (
                shipment is not None
                and provider == "marketplace"
                and platform == "amazon"
                and service
            ):
                shipment.service = service

    app._bt38_fbm_db_delivery_promise_alignment = True
