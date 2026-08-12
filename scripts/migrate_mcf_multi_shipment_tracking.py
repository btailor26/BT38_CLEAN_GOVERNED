"""One-time idempotent schema migration for multi-shipment MCF tracking."""

from sqlalchemy import text

from app import app
from extensions import db


STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS mcf_order_shipments (
        id SERIAL PRIMARY KEY,
        mcf_order_id INTEGER NOT NULL REFERENCES mcf_orders(id) ON DELETE CASCADE,
        tracking_number VARCHAR(255) NOT NULL,
        carrier VARCHAR(100),
        ship_date TIMESTAMP WITHOUT TIME ZONE,
        estimated_arrival_date TIMESTAMP WITHOUT TIME ZONE,
        shipment_status VARCHAR(50),
        marketplace_forwarded_at TIMESTAMP WITHOUT TIME ZONE,
        created_at TIMESTAMP WITHOUT TIME ZONE,
        updated_at TIMESTAMP WITHOUT TIME ZONE,
        CONSTRAINT uq_mcf_order_shipments_tracking
            UNIQUE (mcf_order_id, tracking_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_mcf_order_shipments_mcf_order_id
    ON mcf_order_shipments (mcf_order_id)
    """,
)


with app.app_context():
    for statement in STATEMENTS:
        db.session.execute(text(statement))

    db.session.commit()

    print("PASS: MCF multi-shipment tracking storage exists")
