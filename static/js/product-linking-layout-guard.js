// Product Linking layout guard.
// Long listing titles must remain inside Linked Listings and must never cover
// the original right-hand Actions controls.
(function () {
  "use strict";

  const root = document.querySelector('[data-bt38-page="productLinking"]');
  if (!root) return;

  const STYLE_ID = "bt38-product-linking-layout-guard";

  function installStyle() {
    if (document.getElementById(STYLE_ID)) return;

    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      [data-bt38-page="productLinking"] #warehouseDataContainer table {
        width: 100% !important;
        table-layout: fixed !important;
      }

      [data-bt38-page="productLinking"] #warehouseDataContainer tr > th:nth-child(5),
      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(5) {
        width: 45% !important;
        min-width: 0 !important;
        max-width: 45% !important;
        overflow: hidden !important;
        box-sizing: border-box !important;
      }

      [data-bt38-page="productLinking"] #warehouseDataContainer tr > th:nth-child(6),
      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(6) {
        width: 12% !important;
        min-width: 110px !important;
        white-space: nowrap !important;
        overflow: visible !important;
        position: relative !important;
        z-index: 3 !important;
        box-sizing: border-box !important;
      }

      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(5) > *,
      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(5) .d-block,
      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(5) .d-flex,
      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(5) .flex-grow-1 {
        min-width: 0 !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
      }

      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(5) .text-truncate,
      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(5) strong,
      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(5) a {
        min-width: 0 !important;
        max-width: 100% !important;
        white-space: normal !important;
        overflow-wrap: anywhere !important;
        word-break: break-word !important;
        overflow: hidden !important;
        text-overflow: clip !important;
        box-sizing: border-box !important;
      }

      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(6) .btn-group,
      [data-bt38-page="productLinking"] #warehouseDataContainer tr > td:nth-child(6) .d-flex {
        position: relative !important;
        z-index: 4 !important;
        white-space: nowrap !important;
      }
    `;
    document.head.appendChild(style);
  }

  installStyle();

  window.BT38 = window.BT38 || {};
  window.BT38.productLinkingLayoutGuard = {
    titleContained: true,
    actionsProtected: true,
    actionsColumnMinWidth: 110
  };
}());
