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

# Preserve the existing FBM journey path for historical/current Packlink rows.
# A persisted Packlink provider shipment id plus tracking is provider identity
# proof even when an older row predates the deterministic purchase-key field.
# This changes source selection only; it creates no provider call, poller,
# shipment record, marketplace write, or second journey renderer.
import services.governed_fbm_packlink_journey_authority  # noqa: F401,E402
