from app import app

# Load the existing MCF compatibility binding before any startup recovery can
# attempt Amazon submission. This keeps MCFService UI/fee compatibility while
# all live Amazon execution remains on services.governed_mcf_execution.
import services.governed_mcf_compat  # noqa: F401
import services.governed_ui_event_signal  # noqa: F401
import services.governed_webhook_rejection_recovery  # noqa: F401

from services.governed_ebay_notification_challenge import (
    install_ebay_notification_challenge_handler,
)
from services.product_linking_recent_table_alignment import (
    install_product_linking_recent_table_alignment,
)

install_ebay_notification_challenge_handler(app)
install_product_linking_recent_table_alignment(app)


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
    import services.governed_mcf_execution as mcf_execution
    import services.governed_webhook_capture as webhook_capture
    import services.governed_webhook_execution as webhook_execution

    original_ebay_capture = webhook_capture.capture_ebay_notification
    original_amazon_capture = webhook_capture.capture_amazon_notification
    original_diagnostic = governed_routes._bt38_record_webhook_event
    original_webhook_execution = (
        webhook_execution.process_marketplace_notification
    )
    original_mcf_status_refresh = mcf_execution.refresh_mcf_status

    def _capture_and_arm(platform, capture_function, request):
        notification_record_id = int(capture_function(request))

        # Durable capture is already committed by the existing capture helper.
        # Store the exact identity on the current request before any later work
        # so an uncaught post-capture failure can always be written back to the
        # same raw notification.
        g.bt38_notification_record_id = notification_record_id
        g.bt38_notification_platform = platform

        # Arming is state only, not parsing. parsed_at remains untouched until
        # the existing governed route actually parses/resolves the event.
        try:
            webhook_capture.mark_notification_status(
                platform,
                notification_record_id,
                processing_status="PROCESSING",
                verification_status="PENDING",
            )
        except Exception:
            from extensions import db

            db.session.rollback()
            app.logger.exception(
                "Webhook was captured but immediate PROCESSING status could "
                "not be recorded; governed execution will continue"
            )

        return notification_record_id

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

    def _refresh_mcf_status_preserving_acceptance_clock(mcf_order):
        """Keep the first Amazon acceptance time stable across later refreshes."""
        accepted_at_before_refresh = mcf_order.amazon_status_updated_at
        success, result = original_mcf_status_refresh(mcf_order)

        if accepted_at_before_refresh is not None:
            from extensions import db

            mcf_order.amazon_status_updated_at = accepted_at_before_refresh
            db.session.commit()

        return success, result

    def _execute_with_mcf_signal_alignment(
        marketplace,
        payload,
        actor="webhook",
        notification_record_id=None,
    ):
        platform = str(marketplace or "").strip().lower()

        # eBay variation notifications may identify only listingId + line item.
        # When more than one active SKU shares that listing ID, resolve the
        # exact order line before the existing executor chooses Warehouse stock.
        if platform == "ebay":
            from services.governed_ebay_variation_signal import (
                enrich_ambiguous_ebay_order_signal,
            )

            payload = enrich_ambiguous_ebay_order_signal(payload or {})

        result = original_webhook_execution(
            marketplace=marketplace,
            payload=payload,
            actor=actor,
            notification_record_id=notification_record_id,
        )

        if platform != "amazon":
            return result

        from services.governed_mcf_execution import (
            refresh_mcf_from_amazon_signal,
        )

        mcf_result = refresh_mcf_from_amazon_signal(payload or {})
        result["mcf_signal_refresh"] = mcf_result

        # An exact MCF signal must not be acknowledged as fully processed when
        # Amazon status/tracking refresh or the governed marketplace enrichment
        # failed. SQS can then retain/retry the same durable commercial event.
        if (
            not mcf_result.get("success", False)
            and not mcf_result.get("skipped", False)
        ):
            raise RuntimeError(
                "amazon_mcf_signal_continuation_failed:"
                f"{mcf_result.get('reason')}:"
                f"{mcf_result.get('error') or ''}"
            )

        return result

    webhook_capture.capture_ebay_notification = _capture_ebay_and_arm
    webhook_capture.capture_amazon_notification = _capture_amazon_and_arm
    governed_routes._bt38_record_webhook_event = _record_diagnostic_without_blocking
    mcf_execution.refresh_mcf_status = (
        _refresh_mcf_status_preserving_acceptance_clock
    )
    webhook_execution.process_marketplace_notification = (
        _execute_with_mcf_signal_alignment
    )


_install_governed_webhook_runtime_alignment()


try:
    from services.governed_recovery_alignment import (
        run_bounded_startup_recovery_alignment,
    )
    from services.governed_failed_mcf_retry import retry_failed_linked_mcf

    recovery_result = run_bounded_startup_recovery_alignment(app)
    app.logger.info(
        "BT38 bounded recovery alignment: %s",
        recovery_result,
    )

    with app.app_context():
        failed_mcf_retry_result = retry_failed_linked_mcf()
    app.logger.info(
        "BT38 failed linked MCF retry alignment: %s",
        failed_mcf_retry_result,
    )
except Exception:
    from extensions import db

    db.session.rollback()
    app.logger.exception("BT38 bounded recovery alignment failed")


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
