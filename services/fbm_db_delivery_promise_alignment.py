"""Read-only FBM delivery-promise alignment for the server-rendered desk.

Delivery promises are marketplace-owned facts already persisted in
fbm_order_operational_state when the original order was hydrated.  The FBM page
must display those stored facts without performing a marketplace read while the
page renders.
"""
from __future__ import annotations

from typing import Any

from flask import before_render_template
from sqlalchemy import bindparam, text

from extensions import db


def install_fbm_db_delivery_promise_alignment(app: Any) -> None:
    if getattr(app, "_bt38_fbm_db_delivery_promise_alignment", False):
        return

    @before_render_template.connect_via(app)
    def _inject_fbm_delivery_promises(sender, template, context, **extra):
        if getattr(template, "name", None) != "fbm.html":
            return

        items = context.get("orders") or []
        keys = {
            (
                getattr(item.get("order"), "store_id", None),
                str(getattr(item.get("order"), "marketplace_order_id", "") or "").strip(),
            )
            for item in items
            if isinstance(item, dict) and item.get("order") is not None
        }
        keys = {key for key in keys if key[0] is not None and key[1]}
        if not keys:
            return

        store_ids = sorted({key[0] for key in keys})
        order_ids = sorted({key[1] for key in keys})
        statement = text(
            """
            SELECT store_id,
                   marketplace_order_id,
                   shipping_service,
                   ship_by_at,
                   earliest_delivery_at,
                   latest_delivery_at
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
            # The FBM desk must remain available even if an older environment
            # has not yet received the additive operational-state table.
            db.session.rollback()
            app.logger.exception("FBM stored delivery-promise read failed")
            rows = []

        promises = {
            (row["store_id"], str(row["marketplace_order_id"])): {
                "shipping_service": row["shipping_service"],
                "ship_by_at": row["ship_by_at"],
                "earliest_delivery_at": row["earliest_delivery_at"],
                "latest_delivery_at": row["latest_delivery_at"],
                "source": "fbm_order_operational_state",
            }
            for row in rows
            if (row["store_id"], str(row["marketplace_order_id"])) in keys
        }

        for item in items:
            if not isinstance(item, dict):
                continue
            order = item.get("order")
            key = (
                getattr(order, "store_id", None),
                str(getattr(order, "marketplace_order_id", "") or "").strip(),
            )
            item["delivery_promise"] = promises.get(key)

    app._bt38_fbm_db_delivery_promise_alignment = True
