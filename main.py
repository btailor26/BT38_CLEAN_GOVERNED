from app import app

# Register the existing governed MCF blueprint before templates render. The
# shared base navigation links to governed_mcf.orders_mcf_page, so the endpoint
# must exist in the live Flask URL map even when the current request is for a
# different page such as Warehouse.
from governed_mcf_routes import governed_mcf_bp

if "governed_mcf" not in app.blueprints:
    app.register_blueprint(governed_mcf_bp)

# Load the existing MCF compatibility binding. This keeps MCFService UI/fee
# compatibility while all live Amazon execution remains on
# services.governed_mcf_execution.
import services.governed_mcf_compat  # noqa: F401
import services.governed_ui_event_signal  # noqa: F401
import services.governed_webhook_rejection_recovery  # noqa: F401
import services.public_early_access  # noqa: F401
from services.governed_notification_read_alignment import (
    install_governed_notification_read_alignment,
)
from services.governed_fbm_page_alignment import (
    install_governed_fbm_page_alignment,
)
from services.governed_fbm_global_search_alignment import (
    install_governed_fbm_global_search_alignment,
)
from services.governed_fbm_all_orders_health_alignment import (
    install_governed_fbm_all_orders_health_alignment,
)
from services.governed_fbm_dispatch_queue_alignment import (
    install_governed_fbm_dispatch_queue_alignment,
)
from services.governed_product_linking_unlink_alignment import (
    install_product_linking_unlink_alignment,
)
from services.governed_ebay_native_shipping_alignment import (
    install_governed_ebay_native_shipping_alignment,
)
from services.governed_ebay_packlink_confirmation_alignment import (
    install_governed_ebay_packlink_confirmation_alignment,
)
from services.governed_fbm_db_authority_alignment import (
    install_governed_fbm_db_authority_alignment,
)
from services.governed_shipping_spend_alignment import (
    install_governed_shipping_spend_alignment,
)
from services.governed_shipping_spend_reporting import (
    install_governed_shipping_spend_reporting,
)
from services.governed_seller_delivery_config import (
    install_governed_seller_delivery_config,
)
from services.governed_sds_fbm_read_alignment import (
    install_governed_sds_fbm_read_alignment,
)
from services.governed_sds_dispatch_alignment import (
    install_governed_sds_dispatch_alignment,
)
from services.governed_sds_label_alignment import (
    install_governed_sds_label_alignment,
)
from services.governed_sds_scan_alignment import (
    install_governed_sds_scan_alignment,
)
from services.governed_sds_scanner_lookup_alignment import (
    install_governed_sds_scanner_lookup_alignment,
)
from services.governed_warehouse_inbound_installer import (
    install as install_governed_warehouse_inbound,
)
from services.governed_ebay_return_intake_alignment import (
    install_governed_ebay_return_intake_alignment,
)
from services.governed_fbm_small_alignment import (
    install_governed_fbm_small_alignment,
)
from services.governed_fbm_ready_landing_alignment import (
    install_governed_fbm_ready_landing_alignment,
)
from services.governed_exact_record_event_alignment import (
    install_governed_exact_record_event_alignment,
)

install_governed_notification_read_alignment(app)
# FBM is one existing workspace. Install its DB-backed read surface first, then
# persisted search/workflow scopes and operational health, and only then Cofi's
# presentation tabs. This keeps the DOM out of the authority chain.
install_governed_fbm_page_alignment(app)
install_governed_fbm_global_search_alignment(app)
install_governed_fbm_all_orders_health_alignment(app)
install_governed_fbm_dispatch_queue_alignment(app)
install_product_linking_unlink_alignment(app)
install_governed_ebay_native_shipping_alignment(app)
install_governed_ebay_packlink_confirmation_alignment()
install_governed_fbm_db_authority_alignment()
install_governed_shipping_spend_alignment(app)
install_governed_shipping_spend_reporting(app)
install_governed_seller_delivery_config(app)
install_governed_sds_fbm_read_alignment()
install_governed_sds_dispatch_alignment(app)
install_governed_sds_label_alignment(app)
install_governed_sds_scan_alignment(app)
install_governed_sds_scanner_lookup_alignment(app)
install_governed_warehouse_inbound(app)
# Lifecycle alignment already owns MarketplaceOrder return/refund application.
# Add only the modern eBay nested ORDER_RETURN_ACTIVITY intake adapter before
# the final FBM/bell presentation overlay.
install_governed_ebay_return_intake_alignment()
# Final small alignment runs after the already-built FBM/bell installers so it
# can repair their handoff ordering without creating a parallel workflow.
install_governed_fbm_small_alignment(app)
# Ready to dispatch is the first FBM work area. Keep this final presentation
# correction after the small overlay so previously persisted Pending browser
# state cannot leave the landing table blank.
install_governed_fbm_ready_landing_alignment(app)
# Final event/session guard: replace the old generic commit wake with exact
# affected-record identities and keep every page session-driven. This must run
# after the FBM/bell presentation overlays so no later alignment can restore a
# broad event refresh or a bell DB read.
install_governed_exact_record_event_alignment(app)

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

    response.status_code = 200
    response.headers["X-BT38-Webhook-Capture"] = "stored"
    response.headers["X-BT38-Webhook-Processing"] = "failed-after-capture"
    return response
