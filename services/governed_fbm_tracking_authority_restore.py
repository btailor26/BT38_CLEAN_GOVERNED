"""Restore the existing FBM tracking authority split.

Packlink-purchased tracking remains owned by BT38's existing live Packlink
journey. Marketplace-supplied tracking remains a direct marketplace link.
Presentation-only: no DB/provider/marketplace reads and no writes.
"""
from __future__ import annotations

from flask import request


_SCRIPT = r"""
<script data-bt38-tracking-authority-restore="1">
(function () {
  'use strict';
  function marketplace(row) {
    var logo = row && row.children[1] && row.children[1].querySelector('.fbm-marketplace-logo');
    return String(logo && (logo.getAttribute('alt') || logo.getAttribute('title')) || '').trim().toLowerCase();
  }
  function orderId(row) {
    var node = row && row.children[2] && row.children[2].querySelector('.fw-semibold');
    return String(node && node.textContent || '').trim();
  }
  function restore() {
    document.querySelectorAll('.fbm-order-row').forEach(function (row) {
      var platform = marketplace(row), id = orderId(row), cell = row.children[7];
      if (!cell || !id) return;
      cell.querySelectorAll('a.fbm-tracking-journey').forEach(function (link) {
        var href = '';
        if (platform.indexOf('ebay') !== -1) href = 'https://www.ebay.co.uk/mesh/ord/details?orderid=' + encodeURIComponent(id);
        if (platform.indexOf('amazon') !== -1) href = 'https://sellercentral.amazon.co.uk/orders-v3/order/' + encodeURIComponent(id);
        if (!href) return;
        link.href = href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.classList.remove('fbm-tracking-journey');
        link.removeAttribute('role');
        link.removeAttribute('tabindex');
        delete link.dataset.journeySource;
        delete link.dataset.shipmentId;
        delete link.dataset.platform;
      });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', restore, {once:true});
  else restore();
  new MutationObserver(restore).observe(document.documentElement, {childList:true, subtree:true, attributes:true, attributeFilter:['href','class']});
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
