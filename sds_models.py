"""Persisted Seller Delivery Service (SDS) scan events."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class SDSScanEvent(db.Model):
    __tablename__ = "sds_scan_events"

    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(
        db.Integer,
        db.ForeignKey("fbm_shipments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = db.Column(db.String(50), nullable=False, index=True)
    event_key = db.Column(db.String(200), nullable=False, unique=True, index=True)
    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    source = db.Column(db.String(50), nullable=False, default="seller_scan")
    created_by = db.Column(db.String(150), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    shipment = db.relationship(
        "FBMShipment",
        backref=db.backref("sds_scan_events", lazy=True, cascade="all, delete-orphan"),
    )
