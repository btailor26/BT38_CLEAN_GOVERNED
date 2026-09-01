"""Install the governed Warehouse inbound alignment through the existing services import path.

Importing this module is deliberately limited to route registration. The installed
Warehouse endpoints remain authenticated and read-only; inventory mutation stays
outside this alignment until the governed Goods In commit boundary is proven.
"""
from __future__ import annotations


def install(app) -> None:
    from services.governed_warehouse_inbound_alignment import (
        install_governed_warehouse_inbound_alignment,
    )

    install_governed_warehouse_inbound_alignment(app)
