"""
Services package - Business logic and external integrations
"""

# Global Amazon FBA settlement rule. Importing the services package installs a
# narrow wrapper around the existing exact-event runtime so any delayed Amazon
# FBA Seller-SKU change committed to Neon wakes the same targeted UI channel.
# It does not create a second inventory authority, worker, scan, or push path.
import services.governed_fba_settlement_ui_alignment  # noqa: F401,E402

# MCF startup tracking alignment. Initial source-marketplace dispatch is not the
# terminal state: Amazon may publish one or many tracking numbers later. Extend
# the existing bounded startup recovery so a Fly sleep/restart refreshes only
# exact recently-dispatched MCF orders whose tracking enrichment is unfinished.
import services.governed_mcf_tracking_startup_alignment  # noqa: F401,E402
