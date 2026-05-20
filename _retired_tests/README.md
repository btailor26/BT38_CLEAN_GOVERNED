# Retired tests

This folder contains old standalone scripts/tests that are intentionally excluded
from pytest collection.

These files are retained for audit history only.

`amazon_live_feed_encryption_retired.py` was an old Amazon live feed encryption test that attempted
to call the retired `AmazonService.push_quantity_update(...)` path. The governed
system now routes Amazon FBM inventory changes through the governed execution
layer and `AmazonAPIService.update_fbm_inventory_quantity_governed(...)`.

Do not re-enable retired tests without a governed audit and explicit approval.
