"""Public early-access application workflow for BT38.

Scope:
- public application intake only
- store application evidence in existing SystemLog
- no marketplace calls
- no stock/sync/push/import execution
- no automatic user creation
- approved applicants are handed to existing governed user management
"""
from __future__ import annotations

from datetime import datetime
import json
from urllib.parse import quote

from flask import redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from app import app
from extensions import db
from models import SystemLog, User


def _clean(value: str, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _application_payload() -> dict:
    marketplaces = [
        name
        for name in ("Amazon", "eBay", "Other")
        if request.form.get(f"marketplace_{name.lower()}") == "on"
    ]
    return {
        "status": "pending",
        "full_name": _clean(request.form.get("full_name"), 120),
        "business_name": _clean(request.form.get("business_name"), 160),
        "email": _clean(request.form.get("email"), 160).lower(),
        "marketplaces": marketplaces,
        "monthly_orders": _clean(request.form.get("monthly_orders"), 80),
        "goals": _clean(request.form.get("goals"), 2000),
        "submitted_at": datetime.utcnow().isoformat() + "Z",
        "source": "bt38_public_early_access",
    }


def _load_details(row: SystemLog) -> dict:
    try:
        data = json.loads(row.details or "{}")
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@app.route("/apply", methods=["GET", "POST"])
def bt38_early_access_apply():
    if current_user.is_authenticated:
        return redirect(url_for("governed.governed_dashboard_page"))

    if request.method == "GET":
        return render_template("early_access_apply.html")

    payload = _application_payload()
    required = (
        payload["full_name"],
        payload["business_name"],
        payload["email"],
        payload["goals"],
    )
    if not all(required) or "@" not in payload["email"]:
        return render_template(
            "early_access_apply.html",
            error="Please complete your name, business name, valid email and what you want to manage with BT38.",
            form=request.form,
        ), 400

    existing_user = User.query.filter(User.email.ilike(payload["email"])).first()
    if existing_user:
        flash("An approved BT38 account already exists for this email. Please sign in.", "info")
        return redirect(url_for("governed.login"))

    recent = (
        SystemLog.query
        .filter(SystemLog.log_type == "early_access_application")
        .order_by(SystemLog.created_at.desc())
        .limit(250)
        .all()
    )
    for row in recent:
        details = _load_details(row)
        if str(details.get("email") or "").lower() == payload["email"] and details.get("status") in {"pending", "approved"}:
            return render_template(
                "early_access_received.html",
                application_id=row.id,
                existing=True,
            )

    row = SystemLog(
        log_type="early_access_application",
        message=f"Early access application: {payload['business_name']}",
        details=json.dumps(payload, ensure_ascii=False),
    )
    db.session.add(row)
    db.session.commit()

    return render_template(
        "early_access_received.html",
        application_id=row.id,
        existing=False,
    )


@app.get("/admin/early-access-applications")
@login_required
def bt38_early_access_applications_admin():
    if getattr(current_user, "role", "") != "admin":
        flash("You do not have permission to review early-access applications.", "danger")
        return redirect(url_for("governed.governed_dashboard_page"))

    rows = (
        SystemLog.query
        .filter(SystemLog.log_type == "early_access_application")
        .order_by(SystemLog.created_at.desc(), SystemLog.id.desc())
        .limit(500)
        .all()
    )
    applications = []
    for row in rows:
        details = _load_details(row)
        applications.append({
            "id": row.id,
            "created_at": row.created_at,
            **details,
        })

    return render_template(
        "admin/early_access_applications.html",
        applications=applications,
    )


@app.post("/admin/early-access-applications/<int:application_id>/<decision>")
@login_required
def bt38_early_access_application_decision(application_id: int, decision: str):
    if getattr(current_user, "role", "") != "admin":
        flash("You do not have permission to review early-access applications.", "danger")
        return redirect(url_for("governed.governed_dashboard_page"))

    decision = str(decision or "").strip().lower()
    if decision not in {"approved", "rejected", "pending"}:
        flash("Invalid application decision.", "danger")
        return redirect(url_for("bt38_early_access_applications_admin"))

    row = SystemLog.query.filter_by(
        id=application_id,
        log_type="early_access_application",
    ).first_or_404()

    details = _load_details(row)
    details["status"] = decision
    details["reviewed_at"] = datetime.utcnow().isoformat() + "Z"
    details["reviewed_by_user_id"] = getattr(current_user, "id", None)
    row.details = json.dumps(details, ensure_ascii=False)

    audit = SystemLog(
        log_type="early_access_application_review",
        message=f"Early access application {application_id}: {decision}",
        details=json.dumps({
            "application_id": application_id,
            "decision": decision,
            "email": details.get("email"),
            "reviewed_by_user_id": getattr(current_user, "id", None),
            "reviewed_at": details["reviewed_at"],
        }),
    )
    db.session.add(audit)
    db.session.commit()

    if decision == "approved":
        email = quote(str(details.get("email") or ""))
        username = quote(str(details.get("full_name") or ""))
        flash("Application approved. Create the BT38 account through the existing governed user workflow.", "success")
        return redirect(f"/users/create?email={email}&username={username}")

    flash(f"Application marked {decision}.", "success")
    return redirect(url_for("bt38_early_access_applications_admin"))
