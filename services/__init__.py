"""
Services package - Business logic and external integrations
"""

# Global Amazon FBA settlement rule. Importing the services package installs a
# narrow wrapper around the existing exact-event runtime so any delayed Amazon
# FBA Seller-SKU change committed to Neon wakes the same targeted UI channel.
# It does not create a second inventory authority, worker, scan, or push path.
import services.governed_fba_settlement_ui_alignment  # noqa: F401,E402

# MCF tracking recovery must be installed before the governed runtime performs
# its bounded startup recovery. The recovery keeps already-dispatched external
# marketplace MCF orders alive until Amazon tracking enrichment is complete,
# including multi-package tracking that arrives after a Fly sleep/restart.
# This reuses the existing MCF refresh and marketplace enrichment path.
import services.governed_mcf_tracking_startup_alignment  # noqa: F401,E402

# Exact eBay shipment hydration also checks the exact order's eBay Finances
# SHIPPING_LABEL truth when shipment fulfilment exists. This remains read-only,
# zero-polling, and persists only into the existing shipping spend ledger.
import services.governed_ebay_shipping_label_finance_alignment  # noqa: F401,E402

# Existing Amazon FBM profile hydration also recovers Seller Central-purchased
# Buy Shipping label authority from exact Amazon Finances + Merchant Fulfillment
# reads. Tracking is optional; the validated Amazon ShipmentId is the durable
# shipment identity. No worker, poller, second order path, or marketplace write.
import services.governed_amazon_shipping_label_readback_alignment  # noqa: F401,E402

# Successful shipment lifecycle events finish only that exact existing FBM
# order's marketplace shipment authority. This reuses the established Amazon
# and eBay exact readbacks and never introduces polling, startup recovery or a
# broad marketplace/database scan.
import services.governed_fbm_shipment_event_alignment  # noqa: F401,E402

# The bounded FBM workspace must still bootstrap exact Amazon classification for
# visible/selected rows when no complete FBMOrderProfile exists. This restores
# the existing profile authority (Prime/SFP, fulfilment, promise and shipped-order
# readbacks) without making the health aggregation a marketplace-read surface.
import services.governed_fbm_amazon_profile_alignment  # noqa: F401,E402
