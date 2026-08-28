(function () {
  'use strict';

  const PACKLINK_PRO_URL = 'https://pro.packlink.com/';

  function labelHref(label) {
    if (!label || typeof label !== 'object') return '';
    if (label.url) return String(label.url);
    const base64 = label.base64 || label.data;
    if (!base64) return '';
    const format = String(label.format || 'PDF').toUpperCase();
    const mime = format === 'PNG' ? 'image/png' : (format === 'JPG' || format === 'JPEG' ? 'image/jpeg' : 'application/pdf');
    return `data:${mime};base64,${String(base64)}`;
  }

  function ensureDownload(box) {
    if (!box || !box.dataset || !box.dataset.label) return;
    let label;
    try { label = JSON.parse(box.dataset.label); }
    catch (_) { return; }
    const href = labelHref(label);
    if (!href) return;

    let link = box.querySelector('.label-download');
    if (!link) {
      link = document.createElement('a');
      link.className = 'btn btn-sm btn-outline-primary label-download mt-2 me-2';
      link.textContent = 'Download label';
      box.appendChild(link);
    }
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener';
    link.setAttribute('download', `bt38-label.${String(label.format || 'pdf').toLowerCase()}`);
  }

  function alignPacklinkPayment(box) {
    if (!box) return;
    const status = box.querySelector('.packlink-status');
    if (!status) return;
    const links = Array.from(box.querySelectorAll('a[href^="https://pro.packlink.com"]'));
    let primary = links[0];
    if (!primary) {
      primary = document.createElement('a');
      primary.href = PACKLINK_PRO_URL;
      primary.target = '_blank';
      primary.rel = 'noopener';
      primary.className = 'btn btn-sm btn-success me-2 packlink-pay-link';
      status.parentNode.insertBefore(primary, status);
    }
    primary.classList.add('packlink-pay-link');
    primary.textContent = 'Open Packlink · Ready for payment';
    links.slice(1).forEach(link => link.remove());
  }

  function process(root) {
    const scope = root && root.querySelectorAll ? root : document;
    if (root && root.matches && root.matches('.rate-results')) {
      ensureDownload(root);
      alignPacklinkPayment(root);
    }
    scope.querySelectorAll('.rate-results').forEach(box => {
      ensureDownload(box);
      alignPacklinkPayment(box);
    });
  }

  function start() {
    process(document);
    const root = document.getElementById('fbmShippingOrders');
    if (!root || !window.MutationObserver) return;
    const observer = new MutationObserver(mutations => {
      mutations.forEach(mutation => {
        const target = mutation.target && mutation.target.nodeType === 1 ? mutation.target : null;
        if (target) process(target.closest('.rate-results') || target);
        mutation.addedNodes.forEach(node => {
          if (node && node.nodeType === 1) process(node);
        });
      });
    });
    observer.observe(root, {subtree:true, childList:true, attributes:true, attributeFilter:['data-label']});
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
  else start();
})();
