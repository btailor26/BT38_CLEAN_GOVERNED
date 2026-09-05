"""Restore the existing FBM tracking authority split without marketplace redirects.

Tracking-number clicks stay inside the existing BT38 shipment-journey modal.
Persisted purchased-provider shipments remain the stronger physical-shipment
authority; marketplace tracking is only the fallback journey source when no
purchased provider shipment exists. Presentation-only: no DB/provider/marketplace
reads and no writes are introduced here.
"""
from __future__ import annotations

from flask import request


_SCRIPT = r"""
<script data-bt38-tracking-authority-restore="1">
(function () {
  'use strict';

  function marketplace(row) {
    var logo = row && row.children[1] && row.children[1].querySelector('.fbm-marketplace-logo');
    return String(logo && (logo.getAttribute('alt') || logo.getAttribute('title')) || '').trim();
  }

  function carrier(row) {
    var node = row && row.children[7] && row.children[7].querySelector('strong');
    return String(node && node.textContent || marketplace(row) || 'Marketplace').trim();
  }

  function alignRowTracking() {
    document.querySelectorAll('.fbm-order-row').forEach(function (row) {
      var platform = marketplace(row);
      var shipmentCell = row.children[7];
      if (!shipmentCell) return;

      shipmentCell.querySelectorAll(
        'a[href*="ebay.co.uk/mesh/ord/details"], a[href*="sellercentral.amazon.co.uk/orders-v3/order/"]'
      ).forEach(function (link) {
        var tracking = String(link.textContent || '').trim();
        link.removeAttribute('href');
        link.removeAttribute('target');
        link.removeAttribute('rel');
        link.setAttribute('role', 'button');
        link.setAttribute('tabindex', '0');
        link.classList.add('fbm-tracking-journey');
        link.dataset.trackingNumber = tracking;
        link.dataset.carrier = carrier(row);
        if (!link.dataset.journeySource) link.dataset.journeySource = 'marketplace';
        if (!link.dataset.platform) link.dataset.platform = platform;
      });
    });
  }

  function removeMarketplaceRedirectsFromJourney() {
    var modal = document.getElementById('fbmTrackingJourneyModal');
    if (!modal) return;
    modal.querySelectorAll(
      'a[href*="ebay.co.uk/mesh/ord/details"], a[href*="sellercentral.amazon.co.uk/orders-v3/order/"]'
    ).forEach(function (link) {
      link.remove();
    });
  }

  function align() {
    alignRowTracking();
    removeMarketplaceRedirectsFromJourney();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', align, {once:true});
  } else {
    align();
  }

  new MutationObserver(align).observe(document.documentElement, {
    childList:true,
    subtree:true,
    attributes:true,
    attributeFilter:['href','class']
  });
})();
</script>
"""


def install_governed_fbm_tracking_authority_restore(app) -> None:
    if getattr(app, "_bt38_fbm_tracking_authority_restore", False):
        return

    @app.after_request
    def _restore_tracking_authority(response):
        if request.method != "GET" or request.path.rstrip("/") != "/fbm":
            return response
        if response.status_code != 200 or not response.is_sequence:
            return response
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type:
            return response
        html = response.get_data(as_text=True)
        if 'data-bt38-tracking-authority-restore="1"' in html or "</body>" not in html:
            return response
        response.set_data(html.replace("</body>", _SCRIPT + "</body>", 1))
        response.headers.pop("Content-Length", None)
        return response

    app._bt38_fbm_tracking_authority_restore = True
