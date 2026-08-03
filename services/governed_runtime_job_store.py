"""
Durable exact-scope runtime jobs.

The database stores delayed marketplace work so jobs survive:
- Gunicorn worker boundaries
- Fly restarts
- deployments

No broad marketplace scanning is performed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from extensions import db


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS governed_runtime_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_key VARCHAR(128) NOT NULL UNIQUE,
    source VARCHAR(200) NOT NULL,
    event_type VARCHAR(100),
    marketplace VARCHAR(50),
    store_id INTEGER,
    seller_sku VARCHAR(200),
    listing_id VARCHAR(200),
    order_id VARCHAR(200),
    asin VARCHAR(40),
    fnsku VARCHAR(80),
    warehouse_stock_id INTEGER,
    group_id INTEGER,
    expected_quantity INTEGER,
    payload_json JSONB,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    verify_after TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    claimed_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
)
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_governed_runtime_jobs_due
ON governed_runtime_jobs (status, verify_after)
"""


def ensure_runtime_job_table() -> None:
    db.session.execute(text(_TABLE_SQL))
    db.session.execute(text(_INDEX_SQL))
    db.session.commit()


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _job_key(source: str, event: dict[str, Any]) -> str:
    identity = {
        "source": _clean(source),
        "event_type": _clean(event.get("event_type")),
        "marketplace": _clean(event.get("marketplace")),
        "store_id": event.get("store_id"),
        "seller_sku": _clean(event.get("seller_sku")),
        "listing_id": _clean(event.get("listing_id")),
        "order_id": _clean(event.get("order_id")),
        "asin": _clean(event.get("asin")),
        "fnsku": _clean(event.get("fnsku")),
        "warehouse_stock_id": event.get("warehouse_stock_id"),
        "group_id": event.get("group_id"),
    }

    encoded = json.dumps(
        identity,
        sort_keys=True,
        default=str,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def enqueue_runtime_job(
    *,
    source: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    verify_after = event.get("verify_after")
    if isinstance(verify_after, str):
        verify_after = datetime.fromisoformat(
            verify_after.replace("Z", "+00:00")
        ).replace(tzinfo=None)

    if not isinstance(verify_after, datetime):
        raise ValueError("verify_after must be a datetime")

    key = _job_key(source, event)

    row = db.session.execute(
        text(
            """
            INSERT INTO governed_runtime_jobs (
                job_key,
                source,
                event_type,
                marketplace,
                store_id,
                seller_sku,
                listing_id,
                order_id,
                asin,
                fnsku,
                warehouse_stock_id,
                group_id,
                expected_quantity,
                payload_json,
                status,
                verify_after,
                updated_at
            )
            VALUES (
                :job_key,
                :source,
                :event_type,
                :marketplace,
                :store_id,
                :seller_sku,
                :listing_id,
                :order_id,
                :asin,
                :fnsku,
                :warehouse_stock_id,
                :group_id,
                :expected_quantity,
                CAST(:payload_json AS JSONB),
                'PENDING',
                :verify_after,
                NOW()
            )
            ON CONFLICT (job_key)
            DO UPDATE SET
                verify_after = EXCLUDED.verify_after,
                payload_json = COALESCE(
                    EXCLUDED.payload_json,
                    governed_runtime_jobs.payload_json
                ),
                expected_quantity = EXCLUDED.expected_quantity,
                status = CASE
                    WHEN governed_runtime_jobs.status = 'COMPLETED'
                    THEN 'PENDING'
                    ELSE governed_runtime_jobs.status
                END,
                completed_at = NULL,
                last_error = NULL,
                updated_at = NOW()
            RETURNING id, status, verify_after
            """
        ),
        {
            "job_key": key,
            "source": source,
            "event_type": event.get("event_type"),
            "marketplace": event.get("marketplace"),
            "store_id": event.get("store_id"),
            "seller_sku": event.get("seller_sku"),
            "listing_id": event.get("listing_id"),
            "order_id": event.get("order_id"),
            "asin": event.get("asin"),
            "fnsku": event.get("fnsku"),
            "warehouse_stock_id": event.get("warehouse_stock_id"),
            "group_id": event.get("group_id"),
            "expected_quantity": event.get("expected_quantity"),
            "payload_json": json.dumps(
                event.get("payload"),
                default=str,
            )
            if event.get("payload") is not None
            else None,
            "verify_after": verify_after,
        },
    ).mappings().one()

    db.session.commit()

    return {
        "queued": True,
        "durable": True,
        "job_id": int(row["id"]),
        "status": row["status"],
        "verify_after": row["verify_after"].isoformat(),
    }


def load_pending_runtime_job_hints(
    limit: int = 250,
) -> list[dict[str, Any]]:
    """Load bounded pending job identities once at runtime startup.

    These rows are used only as process-memory wake-up hints. The database
    remains the durable authority for execution and completion.
    """
    rows = db.session.execute(
        text(
            """
            SELECT
                id,
                source,
                event_type,
                marketplace,
                store_id,
                seller_sku,
                listing_id,
                order_id,
                asin,
                fnsku,
                warehouse_stock_id,
                group_id,
                expected_quantity,
                payload_json,
                verify_after
            FROM governed_runtime_jobs
            WHERE status = 'PENDING'
            ORDER BY verify_after, id
            LIMIT :limit
            """
        ),
        {"limit": int(limit)},
    ).mappings().all()

    results = []

    for row in rows:
        item = dict(row)
        payload = item.pop("payload_json", None)

        item["payload"] = (
            payload if isinstance(payload, dict) else None
        )
        item["received_at"] = datetime.utcnow()
        item["scope_present"] = bool(
            item.get("store_id") is not None
            and any(
                (
                    item.get("seller_sku"),
                    item.get("listing_id"),
                    item.get("order_id"),
                    item.get("asin"),
                    item.get("fnsku"),
                    item.get("warehouse_stock_id"),
                )
            )
        )

        results.append(item)

    return results


def claim_due_runtime_jobs(limit: int = 50) -> list[dict[str, Any]]:
    rows = db.session.execute(
        text(
            """
            WITH due AS (
                SELECT id
                FROM governed_runtime_jobs
                WHERE status = 'PENDING'
                  AND verify_after <= NOW()
                ORDER BY verify_after, id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE governed_runtime_jobs job
            SET
                status = 'PROCESSING',
                attempts = attempts + 1,
                claimed_at = NOW(),
                updated_at = NOW()
            FROM due
            WHERE job.id = due.id
            RETURNING job.*
            """
        ),
        {"limit": int(limit)},
    ).mappings().all()

    db.session.commit()

    results = []

    for row in rows:
        item = dict(row)
        payload = item.pop("payload_json", None)

        item["payload"] = payload if isinstance(payload, dict) else None
        item["scope_present"] = bool(
            item.get("store_id") is not None
            and any(
                (
                    item.get("seller_sku"),
                    item.get("listing_id"),
                    item.get("order_id"),
                    item.get("asin"),
                    item.get("fnsku"),
                    item.get("warehouse_stock_id"),
                )
            )
        )

        results.append(item)

    return results


def complete_runtime_job(
    job_id: int,
    *,
    success: bool,
    error: str | None = None,
) -> None:
    db.session.execute(
        text(
            """
            UPDATE governed_runtime_jobs
            SET
                status = CASE
                    WHEN :success THEN 'COMPLETED'
                    ELSE 'FAILED'
                END,
                completed_at = CASE
                    WHEN :success THEN NOW()
                    ELSE completed_at
                END,
                last_error = :error,
                updated_at = NOW()
            WHERE id = :job_id
            """
        ),
        {
            "job_id": int(job_id),
            "success": bool(success),
            "error": error,
        },
    )
    db.session.commit()
