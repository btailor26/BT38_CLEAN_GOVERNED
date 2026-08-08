from app import app

from services.governed_ebay_notification_challenge import (
    install_ebay_notification_challenge_handler,
)

install_ebay_notification_challenge_handler(app)


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
