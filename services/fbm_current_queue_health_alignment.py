"""Align FBM health cards with the current persisted FBM queue.

The selected date/month remains useful for lifecycle-event context, but operational
health must not show zero merely because an unresolved order was created on an
earlier date. This overlay reuses the existing MarketplaceOrder / FBMOrderProfile /
FBMShipment DB authorities and never calls a marketplace or provider.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import MarketplaceOrder


_TERMINAL_STATUSES = {
    "cancelled",
    "canceled",
    "delivered",
    "returned",
    "refunded",
}


def install_fbm_current_queue_health_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_current_queue_health_alignment_installed", False):
        return

    from services import governed_fbm_page_alignment as page_alignment

    original_health_summary = page_alignment._health_summary
    original_health_html = page_alignment._health_html

    def current_queue_health_summary() -> dict:
        # Keep the existing period-backed lifecycle facts (returns/replacements/
        # refunds) and overlay the operational cards from the current queue.
        health = dict(original_health_summary())

        eligible = (
            func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
            ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
        )
        rows = (
            db.session.query(MarketplaceOrder)
            .filter(*eligible)
            .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
            .order_by(MarketplaceOrder.id.desc())
            .limit(page_alignment._FBM_HEALTH_MAX_ROWS)
            .all()
        )
        latest = page_alignment._dedupe_latest(rows)
        profiles = page_alignment._profile_map([
            row for row in latest
            if page_alignment._platform(row).strip().lower() == "amazon"
        ])

        current_rows = []
        for row in latest:
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key) if page_alignment._platform(row).strip().lower() == "amazon" else None
            if not page_alignment._workspace_fbm_eligible(row, profile):
                continue
            status = str(getattr(row, "status", "") or "").strip().lower()
            if status in _TERMINAL_STATUSES:
                continue
            current_rows.append(row)

        shipments = page_alignment._shipment_map(current_rows)
        ready = dispatched = awaiting = overdue = 0
        platform_counts: dict[str, int] = {}
        for row in current_rows:
            platform = page_alignment._platform(row).strip() or "Other"
            platform_counts[platform] = platform_counts.get(platform, 0) + 1
            route_state = page_alignment._route_state(row)
            if route_state == "Ready for FBM routing":
                ready += 1
            if route_state in {"Dispatched", "Tracking recorded"}:
                dispatched += 1

            shipment = shipments.get((int(row.store_id), str(row.marketplace_order_id)))
            if shipment is not None:
                state = page_alignment.shipment_confirmation_state(shipment)
                if state == "awaiting_carrier_acceptance":
                    awaiting += 1
                elif state == "acceptance_overdue":
                    overdue += 1

        health.update({
            "total": len(current_rows),
            "ready": ready,
            "dispatched": dispatched,
            "awaiting_acceptance": awaiting,
            "overdue": overdue,
            "platform_counts": dict(sorted(platform_counts.items(), key=lambda item: (-item[1], item[0].lower()))),
            # Mapping review remains available on individual shipment actions but
            # is intentionally no longer a top-level health card/action metric.
            "mapping_review": 0,
            # Buyer-message ingestion is not yet persisted in BT38. Never invent
            # a count; expose a truthful zero until the marketplace -> DB path is wired.
            "buyer_messages": 0,
            "truncated": len(rows) >= page_alignment._FBM_HEALTH_MAX_ROWS,
        })

        risk_actions = int(health["overdue"]) + int(health["returns"]) + int(health["replacements"]) + int(health["refund_issues"])
        health_base = max(1, int(health["total"]) + int(health["returns"]) + int(health["replacements"]) + int(health["refund_issues"]))
        health["risk_actions"] = risk_actions
        health["health_score"] = max(0, min(100, round(100 * (health_base - risk_actions) / health_base)))
        health["shipping_actions"] = int(health["ready"]) + int(health["overdue"])
        return health

    def current_queue_health_html(health: dict) -> str:
        html = original_health_html(health)
        old = page_alignment._metric_card(
            "Mapping review",
            int(health.get("mapping_review") or 0),
            [f"{int(health.get('mapping_review') or 0)} carrier mappings need review"],
        )
        new = page_alignment._metric_card(
            "Buyer messages",
            int(health.get("buyer_messages") or 0),
            ["Buyer-message count will come from persisted marketplace message events once that governed ingestion path is enabled."],
        )
        return html.replace(old, new, 1)

    page_alignment._health_summary = current_queue_health_summary
    page_alignment._health_html = current_queue_health_html
    app._bt38_fbm_current_queue_health_alignment_installed = True
    app.logger.info(
        "BT38 FBM health aligned: operational cards use current persisted queue; period lifecycle facts remain DB-backed; Buyer messages stays truthful until ingestion exists"
    )
