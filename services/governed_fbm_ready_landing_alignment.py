"""Restore Ready to dispatch as the first FBM landing view.

This is presentation-only alignment over the existing browser-session workflow.
It does not add a query, marketplace/provider call, DB write, worker, poller or
second FBM workflow. The existing rendered canonical snapshot remains the data
source; this only restores the intended initial queue selection and tab order.
"""
from __future__ import annotations

from flask import make_response
from flask_login import login_required


def _align_ready_landing_html(html: str) -> str:
    html = html.replace(
        "var sessionDefaults={tab:'pending',search:'',dirty:false};",
        "var sessionDefaults={tab:'ready_dispatch',search:'',dirty:false};",
    )
    html = html.replace(
        "(saved.tab&&labels[saved.tab]?saved.tab:'pending')",
        "((saved.tab&&labels[saved.tab]&&saved.tab!=='pending')?saved.tab:'ready_dispatch')",
    )
    html = html.replace(
        "addWorkflowButton(tabBar,'pending','Pending');\n  addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');",
        "addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');\n  addWorkflowButton(tabBar,'pending','Pending');",
    )
    return html


def install_governed_fbm_ready_landing_alignment(app) -> None:
    endpoint = "governed_fbm.fbm_page"
    current = app.view_functions.get(endpoint)
    if current is None or getattr(current, "_bt38_ready_landing_alignment", False):
        return

    @login_required
    def ready_landing_page():
        response = make_response(current())
        if response.status_code != 200 or "text/html" not in str(response.content_type or "").lower():
            return response
        response.set_data(_align_ready_landing_html(response.get_data(as_text=True)))
        return response

    ready_landing_page._bt38_ready_landing_alignment = True
    app.view_functions[endpoint] = ready_landing_page
    app.logger.info(
        "BT38 FBM landing aligned: Ready to dispatch first; existing DB-backed snapshot and browser-local workflow retained"
    )
