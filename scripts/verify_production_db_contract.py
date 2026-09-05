"""Read-only production DB compatibility check for the governed Fly release.

This intentionally checks only critical structures the current production image
actively depends on. Extra tables/columns do not fail the release. Historical
architecture drift is reported as a warning, not used as a schema blocker.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, inspect, text


REQUIRED_COLUMNS = {
    "stores": {
        "id", "platform", "is_active", "fbm_sync_enabled", "fba_import_enabled",
        "auto_push_enabled", "immediate_push", "store_mode",
    },
    "marketplace_orders": {
        "id", "store_id", "marketplace_order_id", "sku", "warehouse_stock_id",
        "quantity", "fulfillment_type", "carrier", "tracking_number", "shipped_at",
        "status", "idempotency_key", "created_at", "updated_at",
    },
    "marketplace_listings": {
        "id", "store_id", "external_sku", "warehouse_stock_id",
        "master_product_group_id",
    },
    "warehouse_stock": {
        "id", "sku", "available_quantity",
    },
    "fbm_shipments": {
        "id", "store_id", "marketplace_order_id", "provider", "provider_shipment_id",
        "carrier", "service", "tracking_number", "status", "label_purchased_at",
        "carrier_accepted_at", "first_movement_at", "delivered_at",
        "marketplace_confirmation_status", "purchase_key", "purchase_status",
        "created_at", "updated_at",
    },
    "fbm_order_profiles": {
        "id", "store_id", "marketplace_order_id", "platform", "is_prime",
        "fulfillment_channel", "shipment_service_level", "latest_ship_at",
        "checked_at", "updated_at",
    },
    # Current candidate code still contains a compatibility write to this table.
    # Keep it required only while that code path exists; removing that writer
    # should remove this table from the release contract in the same commit.
    "fbm_order_operational_state": {
        "store_id", "marketplace_order_id", "platform", "shipping_service",
        "ship_by_at", "earliest_delivery_at", "latest_delivery_at",
        "marketplace_checked_at", "created_at", "updated_at",
    },
}


def _database_url() -> str:
    value = str(os.environ.get("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("DB_CONTRACT_BLOCKED: DATABASE_URL is not available in the production runtime")
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    return value


def main() -> int:
    engine = create_engine(_database_url(), pool_pre_ping=True)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names(schema="public"))

    failures: list[str] = []
    for table, required in REQUIRED_COLUMNS.items():
        if table not in existing_tables:
            failures.append(f"missing table public.{table}")
            continue
        actual = {column["name"] for column in inspector.get_columns(table, schema="public")}
        missing = sorted(required - actual)
        if missing:
            failures.append(f"public.{table} missing columns: {', '.join(missing)}")

    if failures:
        print("DB_CONTRACT_BLOCKED")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("DB_CONTRACT_OK: critical production tables/columns required by this image are present")

    # Informational only. Existing historical drift must not make the structural
    # compatibility gate unusable; these warnings drive follow-up audits.
    with engine.connect() as connection:
        marketplace_proxy_count = connection.execute(text("""
            SELECT count(*)
            FROM fbm_shipments
            WHERE lower(coalesce(provider, '')) = 'marketplace'
        """)).scalar_one()
        if marketplace_proxy_count:
            print(
                "DB_CONTRACT_WARNING: "
                f"{marketplace_proxy_count} persisted marketplace FBM shipment rows exist; "
                "authority cleanup remains required"
            )

        operational_count = connection.execute(text("""
            SELECT count(*) FROM fbm_order_operational_state
        """)).scalar_one()
        if operational_count:
            print(
                "DB_CONTRACT_WARNING: "
                f"{operational_count} fbm_order_operational_state rows exist; "
                "compatibility persistence remains active until its writer is retired"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
