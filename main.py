from app import app

from services.governed_ebay_notification_challenge import (
    install_ebay_notification_challenge_handler,
)

install_ebay_notification_challenge_handler(app)


# Webhook runtime alignment only.
#
# Keep the existing governed webhook route and execution path intact, but make
# durable capture the point where the exact notification becomes active work.
# Diagnostic logging remains evidence only and must never strand a commercial
# event before governed execution begins.
def _install_governed_webhook_runtime_alignment():
    from flask import g
    from types import SimpleNamespace

    import governed_routes
    import services.governed_webhook_capture as webhook_capture

    original_ebay_capture = webhook_capture.capture_ebay_notification
    original_amazon_capture = webhook_capture.capture_amazon_notification
    original_diagnostic = governed_routes._bt38_record_webhook_event

    def _capture_and_arm(platform, capture_function, request):
        notification_record_id = capture_function(request)

        # Durable capture has succeeded. From this point the exact event is
        # active governed work and must never remain silently in RECEIVED.
        webhook_capture.mark_notification_status(
            platform,
            notification_record_id,
            processing_status="PROCESSING",
            verification_status="PENDING",
            parsed=True,
        )

        g.bt38_notification_record_id = int(notification_record_id)
        g.bt38_notification_platform = platform
        return int(notification_record_id)

    def _capture_ebay_and_arm(request, *, commit=True):
        return _capture_and_arm(
            "ebay",
            lambda req: original_ebay_capture(req, commit=commit),
            request,
        )

    def _capture_amazon_and_arm(request, *, commit=True):
        return _capture_and_arm(
            "amazon",
            lambda req: original_amazon_capture(req, commit=commit),
            request,
        )

    def _record_diagnostic_without_blocking(**kwargs):
        try:
            return original_diagnostic(**kwargs)
        except Exception as exc:
            # SystemLog is diagnostic evidence, never execution authority.
            # Clear any failed logging transaction and allow the already
            # captured exact event to continue through the existing path.
            from extensions import db

            db.session.rollback()
            app.logger.exception(
                "Webhook diagnostic logging failed after durable capture; "
                "governed execution will continue"
            )
            return SimpleNamespace(id=None, error=str(exc))

    webhook_capture.capture_ebay_notification = _capture_ebay_and_arm
    webhook_capture.capture_amazon_notification = _capture_amazon_and_arm
    governed_routes._bt38_record_webhook_event = _record_diagnostic_without_blocking


_install_governed_webhook_runtime_alignment()


@app.teardown_request
def record_captured_webhook_failure(exception=None):
    """Record an uncaught post-capture failure on the exact raw notification.

    This observes Flask's existing request lifecycle only. It does not replace
    Flask error handling and does nothing for non-webhook requests.
    """
    if exception is None:
        return None

    from flask import g, request

    if request.method != "POST":
        return None

    if request.path.rstrip("/") not in {
        "/governed/webhooks/ebay",
        "/governed/webhooks/amazon",
    }:
        return None

    notification_record_id = getattr(
        g,
        "bt38_notification_record_id",
        None,
    )
    if notification_record_id is None:
        return None

    from extensions import db
    from services.governed_webhook_capture import mark_notification_status

    platform = str(
        getattr(g, "bt38_notification_platform", "") or ""
    ).strip().lower()

    db.session.rollback()
    try:
        mark_notification_status(
            platform,
            int(notification_record_id),
            processing_status="FAILED",
            last_error=str(exception)[:4000],
            completed=True,
        )
    except Exception:
        db.session.rollback()
        app.logger.exception(
            "Failed to persist captured webhook failure state"
        )

    return None


@app.after_request
def acknowledge_captured_ebay_webhook(response):
    """Acknowledge eBay once its notification is durably captured.

    The governed webhook route deliberately records the immutable raw eBay
    notification before any downstream order, Warehouse, group or runtime work.
    If that later governed processing fails, the captured notification remains
    available for audit/recovery and eBay must not be asked to redeliver the
    same commercial event merely because BT38's downstream processing failed.

    Capture failures are NOT acknowledged here: they remain non-2xx so eBay can
    retry because BT38 does not yet hold the immutable notification.
    """
    from flask import request

    if request.method != "POST":
        return response

    if request.path.rstrip("/") != "/governed/webhooks/ebay":
        return response

    if response.status_code < 500:
        return response

    payload = response.get_json(silent=True)
    if not isinstance(payload, dict):
        return response

    if payload.get("status") != "processing_failed":
        return response

    if payload.get("notification_record_id") is None:
        return response

    # The immutable notification is already stored. Keep the response body and
    # audit state unchanged, but return a provider-success HTTP status so eBay
    # does not redeliver an event because a later BT38 governed step failed.
    response.status_code = 200
    response.headers["X-BT38-Webhook-Capture"] = "stored"
    response.headers["X-BT38-Webhook-Processing"] = "failed-after-capture"
    return response


try:
    import services.governed_mcf_compat  # noqa: F401
    from governed_mcf_routes import governed_mcf_bp
    app.register_blueprint(governed_mcf_bp)
except Exception as exc:
    app.logger.error(f"Failed to register governed MCF routes: {exc}")


@app.teardown_appcontext
def close_sqlalchemy_session(exception=None):
    """Never leave pooled Neon connections idle inside a transaction."""
    from extensions import db

    try:
        # Read-only SELECTs also open a PostgreSQL transaction. Roll it back at
        # context teardown unless the route/service already committed its write.
        db.session.rollback()
    except Exception:
        app.logger.exception("SQLAlchemy rollback failed during app teardown")
    finally:
        db.session.remove()


if __name__ == '__main__':
    # Legacy background sync workers are intentionally not started here.
    # Future runtime execution must enter through the governed command path.
    app.run(host='0.0.0.0', port=5000, debug=True)
