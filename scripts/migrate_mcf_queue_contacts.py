"""One-time idempotent schema migration for MCF queue/contact fields."""

from sqlalchemy import text

from app import app
from extensions import db


STATEMENTS = (
    """
    ALTER TABLE marketplace_orders
    ADD COLUMN IF NOT EXISTS ship_to_email VARCHAR(320)
    """,
    """
    ALTER TABLE marketplace_orders
    ADD COLUMN IF NOT EXISTS ship_to_phone VARCHAR(50)
    """,
    """
    ALTER TABLE marketplace_orders
    ADD COLUMN IF NOT EXISTS mcf_queue_hidden BOOLEAN
    NOT NULL DEFAULT FALSE
    """,
)


with app.app_context():
    for statement in STATEMENTS:
        db.session.execute(text(statement))

    db.session.commit()

    print(
        "PASS: marketplace_orders MCF contact and queue columns exist"
    )
