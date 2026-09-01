"""Install the governed Warehouse inbound alignment through the existing services import path.

The Warehouse inbound endpoints and their existing-page presentation remain
read-only. Inventory mutation stays outside this alignment until the governed
Goods In commit boundary is proven.
"""
from __future__ import annotations


def install(app) -> None:
    from flask import request
    from services.governed_warehouse_inbound_alignment import (
        install_governed_warehouse_inbound_alignment,
    )

    install_governed_warehouse_inbound_alignment(app)

    endpoint = "governed_warehouse_inbound_frontend"
    if endpoint in app.view_functions:
        return

    @app.after_request
    def governed_warehouse_inbound_frontend(response):
        """Load the read-only Goods In controller only on the existing Warehouse page."""
        if request.method != "GET" or request.path.rstrip("/") != "/warehouse":
            return response
        if response.status_code != 200 or not response.is_sequence:
            return response
        content_type = str(response.headers.get("Content-Type") or "")
        if "text/html" not in content_type:
            return response

        html = response.get_data(as_text=True)
        marker = "warehouse-inbound-readonly.js"
        if marker in html or "</body>" not in html:
            return response

        script = '<script src="/static/js/warehouse-inbound-readonly.js"></script>'
        response.set_data(html.replace("</body>", script + "</body>", 1))
        response.headers["Content-Length"] = str(len(response.get_data()))
        return response

    # after_request handlers are not view functions; record a private install marker
    # so repeated installer calls cannot register the same presentation twice.
    app.view_functions[endpoint] = lambda: None