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
from services.governed_product_linking_unlink_alignment import (
    install_product_linking_unlink_alignment,
)
from services.governed_ebay_native_shipping_alignment import (
    install_governed_ebay_native_shipping_alignment,
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

install_governed_notification_read_alignment(app)
install_product_linking_unlink_alignment(app)
install_governed_ebay_native_shipping_alignment(app)
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
