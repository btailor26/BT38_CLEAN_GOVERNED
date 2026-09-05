"""Shared Flask extensions to avoid circular imports."""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class BT38SQLAlchemy(SQLAlchemy):
    """Keep exact Amazon FBM profile persistence on the existing webhook path."""

    def init_app(self, app):
        super().init_app(app)
        # This is only an installer for the existing current-event alignment.
        # It adds no worker, poller, recovery scan or marketplace read. Amazon
        # Prime/program/promise facts are persisted from the webhook already
        # being processed by the governed runtime.
        from services.governed_amazon_fbm_profile_event_alignment import (
            install_governed_amazon_fbm_profile_event_alignment,
        )

        install_governed_amazon_fbm_profile_event_alignment(app)


# Create shared instances.
db = BT38SQLAlchemy(model_class=Base)
login_manager = LoginManager()
