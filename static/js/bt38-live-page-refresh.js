// Shared BT38 event-driven page refresh controller.
// One server SSE connection is owned by base.html; this file never opens one.
// No polling, no intervals, no marketplace reads, and no full-page reloads.
(function () {
  'use strict';

  if (window.bt38LivePageRefreshInstalled) return;
  window.bt38LivePageRefreshInstalled = true;

  let pendingWhileHidden = false;
  let lastSequence = '';
  let productLinkingRefreshRunning = false;

  function sequenceOf(event) {
    return String(event?.detail?.sequence || '').trim();
  }

  function currentProductLinkingSearch() {
    if (!document.querySelector('[data-bt38-page="productLinking"]')) return '';
    const form = document.getElementById('bt38ProductLinkingFilterForm');
    return String(form?.querySelector('[name="search"]')?.value || '').trim();
  }

  async function refreshProductLinkingSilently() {
    const search = currentProductLinkingSearch();
    if (!search || productLinkingRefreshRunning) return false;
    if (typeof window.bt38RefreshProductLinkingRecord !== 'function') return false;

    productLinkingRefreshRunning = true;
    try {
      // Product Linking already owns the exact targeted DB-backed refresh.
      // Reuse the user's current search identity rather than hydrate the full
      // working set. One event -> one targeted read; no polling or broad scan.
      await window.bt38RefreshProductLinkingRecord({
        listingSku: search,
        warehouseSku: search
      });
      return true;
    } catch (error) {
      console.warn('[BT38 UI] Product Linking silent refresh failed', error);
      return false;
    } finally {
      productLinkingRefreshRunning = false;
    }
  }

  function pageOwnsCommittedRefresh() {
    // Orders / MCF already performs one narrow DB-only table refresh from the
    // same shared event. Do not add a second current-page request there.
    return Boolean(document.getElementById('mcf-orders-body'));
  }

  async function refreshCurrentPage() {
    if (pageOwnsCommittedRefresh()) return;

    if (document.visibilityState === 'hidden') {
      pendingWhileHidden = true;
      return;
    }

    if (document.querySelector('[data-bt38-page="productLinking"]')) {
      await refreshProductLinkingSilently();
      return;
    }

    // Pages without a targeted live updater must remain visually stable.
    // Never reload or rerender the whole page on a marketplace event because
    // that can interrupt active edits, searches, modals, or quantity changes.
  }

  window.addEventListener('bt38-marketplace-event', function (event) {
    const sequence = sequenceOf(event);
    if (sequence && sequence === lastSequence) return;
    if (sequence) lastSequence = sequence;
    void refreshCurrentPage();
  });

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible' || !pendingWhileHidden) return;
    pendingWhileHidden = false;
    void refreshCurrentPage();
  });

  function installFbmShippingConnectionsNav() {
    if (document.getElementById('bt38FbmConnectionsMenu')) return;

    const fbmLink = Array.from(document.querySelectorAll('#bt38SideNav a')).find(function (link) {
      return String(link.getAttribute('href') || '') === '/fbm';
    });
    if (!fbmLink) return;

    const wrapper = document.createElement('div');
    wrapper.id = 'bt38FbmConnectionsMenu';
    wrapper.className = 'bg-dark border-bottom border-secondary';
    wrapper.innerHTML = `
      <button
        class="list-group-item list-group-item-action bg-dark text-light border-0 w-100 text-start d-flex align-items-center"
        type="button"
        data-bs-toggle="collapse"
        data-bs-target="#bt38FbmConnectionsCollapse"
        aria-expanded="false"
        aria-controls="bt38FbmConnectionsCollapse"
      >
        <span class="ms-4 d-flex align-items-center flex-grow-1">
          <i data-feather="link" class="me-2"></i>Shipping connections
        </span>
        <i data-feather="chevron-down"></i>
      </button>
      <div class="collapse" id="bt38FbmConnectionsCollapse">
        <div class="pb-2">
          <button class="btn btn-dark text-start w-100 ps-5 py-2 bt38-shipping-connection" type="button" data-provider="royal_mail">
            <i data-feather="mail" class="me-2"></i>Royal Mail
          </button>
          <button class="btn btn-dark text-start w-100 ps-5 py-2 bt38-shipping-connection" type="button" data-provider="packlink">
            <i data-feather="truck" class="me-2"></i>Packlink PRO
          </button>
        </div>
      </div>`;

    fbmLink.insertAdjacentElement('afterend', wrapper);

    const modalHost = document.createElement('div');
    modalHost.innerHTML = `
      <div class="modal fade" id="bt38ShippingConnectionModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
          <div class="modal-content">
            <div class="modal-header">
              <div>
                <h5 class="modal-title" id="bt38ShippingConnectionTitle">Connect shipping provider</h5>
                <div class="small text-muted" id="bt38ShippingConnectionSubtitle"></div>
              </div>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body" id="bt38ShippingConnectionBody"></div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(modalHost.firstElementChild);

    document.querySelectorAll('.bt38-shipping-connection').forEach(function (button) {
      button.addEventListener('click', function () {
        const provider = button.dataset.provider;
        const title = document.getElementById('bt38ShippingConnectionTitle');
        const subtitle = document.getElementById('bt38ShippingConnectionSubtitle');
        const body = document.getElementById('bt38ShippingConnectionBody');

        if (provider === 'royal_mail') {
          title.textContent = 'Royal Mail';
          subtitle.textContent = 'Connect your own Royal Mail business account';
          body.innerHTML = `
            <div class="alert alert-info border">
              <strong>Royal Mail API access pending approval.</strong>
              <div class="small mt-1">BT38 is prepared for a merchant-owned Royal Mail connection. Each BT38 customer will connect their own Royal Mail account; BT38 will not share one postage account across customers.</div>
            </div>
            <div class="mb-3">
              <label class="form-label">Connection type</label>
              <input class="form-control" value="Royal Mail API" disabled>
              <div class="form-text">The approved Royal Mail API product and credential fields will be enabled here when portal access is activated.</div>
            </div>
            <div class="mb-3">
              <label class="form-label">Account status</label>
              <div><span class="badge bg-warning text-dark">API approval pending</span></div>
            </div>
            <button class="btn btn-primary" type="button" disabled>Connect Royal Mail account</button>
            <div class="small text-muted mt-3">Future flow: merchant connects account → BT38 validates credentials → services/labels/tracking use that merchant's Royal Mail account.</div>`;
        } else {
          title.textContent = 'Packlink PRO';
          subtitle.textContent = 'Connect or check your Packlink shipping account';
          body.innerHTML = `
            <div class="alert alert-light border">
              <strong>Packlink PRO connection.</strong>
              <div class="small text-muted mt-1">BT38 already supports Packlink API authentication and can test the currently configured account.</div>
            </div>
            <button id="bt38PacklinkConnectionTestNav" class="btn btn-primary" type="button">Test Packlink connection</button>
            <div id="bt38PacklinkConnectionResultNav" class="small text-muted mt-3"></div>`;

          const testButton = document.getElementById('bt38PacklinkConnectionTestNav');
          const result = document.getElementById('bt38PacklinkConnectionResultNav');
          testButton.addEventListener('click', async function () {
            testButton.disabled = true;
            result.className = 'small text-muted mt-3';
            result.textContent = 'Testing connection…';
            try {
              const response = await fetch('/fbm/packlink/connection', {
                method: 'GET',
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {'Accept': 'application/json'}
              });
              const payload = await response.json().catch(function () { return {}; });
              if (!response.ok || payload.success !== true) {
                throw new Error(payload.message || `HTTP ${response.status}`);
              }
              result.className = 'small text-success mt-3';
              result.textContent = payload.account_email
                ? `Connected · ${payload.account_email}`
                : 'Packlink PRO connected successfully.';
            } catch (error) {
              result.className = 'small text-danger mt-3';
              result.textContent = error instanceof Error ? error.message : 'Unable to test Packlink connection.';
            } finally {
              testButton.disabled = false;
            }
          });
        }

        const sideNav = document.getElementById('bt38SideNav');
        const offcanvas = sideNav ? bootstrap.Offcanvas.getInstance(sideNav) : null;
        if (offcanvas) offcanvas.hide();
        bootstrap.Modal.getOrCreateInstance(document.getElementById('bt38ShippingConnectionModal')).show();
      });
    });

    if (window.feather) feather.replace();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installFbmShippingConnectionsNav, {once: true});
  } else {
    installFbmShippingConnectionsNav();
  }
})();
