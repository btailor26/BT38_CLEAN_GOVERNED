from app import app

from governed_mcf_routes import governed_mcf_bp

if "governed_mcf" not in app.blueprints:
    app.register_blueprint(governed_mcf_bp)

import services.governed_mcf_compat  # noqa: F401
import services.governed_ui_event_signal  # noqa: F401
import services.governed_webhook_rejection_recovery  # noqa: F401
import services.public_early_access  # noqa: F401
from services.governed_notification_read_alignment import install_governed_notification_read_alignment
from services.governed_fbm_page_alignment import install_governed_fbm_page_alignment
from services.governed_fbm_global_search_alignment import install_governed_fbm_global_search_alignment
from services.governed_fbm_all_orders_health_alignment import install_governed_fbm_all_orders_health_alignment
from services.governed_fbm_dispatch_queue_alignment import install_governed_fbm_dispatch_queue_alignment
from services.governed_product_linking_unlink_alignment import install_product_linking_unlink_alignment
from services.governed_ebay_native_shipping_alignment import install_governed_ebay_native_shipping_alignment
from services.governed_ebay_packlink_confirmation_alignment import install_governed_ebay_packlink_confirmation_alignment
from services.governed_fbm_db_authority_alignment import install_governed_fbm_db_authority_alignment
from services.governed_shipping_spend_alignment import install_governed_shipping_spend_alignment
from services.governed_shipping_spend_reporting import install_governed_shipping_spend_reporting
from services.governed_seller_delivery_config import install_governed_seller_delivery_config
from services.governed_sds_fbm_read_alignment import install_governed_sds_fbm_read_alignment
from services.governed_sds_dispatch_alignment import install_governed_sds_dispatch_alignment
from services.governed_sds_label_alignment import install_governed_sds_label_alignment
from services.governed_sds_scan_alignment import install_governed_sds_scan_alignment
from services.governed_sds_scanner_lookup_alignment import install_governed_sds_scanner_lookup_alignment
from services.governed_warehouse_inbound_installer import install as install_governed_warehouse_inbound
from services.governed_ebay_return_intake_alignment import install_governed_ebay_return_intake_alignment
from services.governed_fbm_small_alignment import install_governed_fbm_small_alignment
from services.governed_fbm_ready_landing_alignment import install_governed_fbm_ready_landing_alignment
from services.governed_exact_record_event_alignment import install_governed_exact_record_event_alignment
from services.governed_bell_event_projection_alignment import install_governed_bell_event_projection_alignment
from services.governed_amazon_fbm_profile_event_alignment import install_governed_amazon_fbm_profile_event_alignment

install_governed_notification_read_alignment(app)
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
install_governed_ebay_return_intake_alignment()
install_governed_fbm_small_alignment(app)
install_governed_fbm_ready_landing_alignment(app)
# Amazon ORDER_CHANGE already carries Prime/program and, when supplied, promise
# truth. Persist that exact event into the existing FBM profile/operational rows
# once; the FBM page remains DB-only and does not poll Amazon per row.
install_governed_amazon_fbm_profile_event_alignment(app)
install_governed_exact_record_event_alignment(app)
install_governed_bell_event_projection_alignment(app)

from services.governed_ebay_notification_challenge import install_ebay_notification_challenge_handler

install_ebay_notification_challenge_handler(app)


@app.after_request
def acknowledge_captured_ebay_webhook(response):
    """Acknowledge eBay once its notification is durably captured."""
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
