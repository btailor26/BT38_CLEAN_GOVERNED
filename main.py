from app import app

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
from services.governed_warehouse_inbound_installer import install as install_governed_warehouse_inbound

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
