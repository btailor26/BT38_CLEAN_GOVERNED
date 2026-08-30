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

// BT38 assistant presentation layer.
// Dashboard Actions remains the single source of truth. This block performs
// presentation-only reads and reactions; it never changes business actions.
(function () {
  'use strict';

  if (window.bt38AssistantInstalled) return;
  window.bt38AssistantInstalled = true;

  const cacheKey = 'bt38.assistant.dashboardActionCount';
  const dashboardPath = '/dashboard';
  const anchors = ['bt38-assistant-left', 'bt38-assistant-centre', 'bt38-assistant-right'];
  const activePoses = [
    '/static/img/bt38-guide-active.svg',
    '/static/img/bt38-guide-progress.svg'
  ];
  const closePoses = [
    '/static/img/bt38-guide-progress.svg',
    '/static/img/bt38-guide-nearly-done.svg'
  ];
  let host = null;
  let image = null;
  let message = null;
  let currentCount = null;
  let refreshRunning = false;
  let pagePose = null;
  let temporaryMessageTimer = null;

  function injectAssistantStyles() {
    if (document.getElementById('bt38AssistantStyles')) return;
    const style = document.createElement('style');
    style.id = 'bt38AssistantStyles';
    style.textContent = `
      body.bt38-assistant-space{padding-bottom:112px!important}
      #bt38Assistant{position:fixed;bottom:10px;z-index:1035;display:flex;align-items:flex-end;gap:8px;max-width:min(360px,calc(100vw - 22px));pointer-events:none;transition:left .28s ease,right .28s ease,transform .28s ease,opacity .2s ease;opacity:0}
      #bt38Assistant.bt38-ready{opacity:1}
      #bt38Assistant.bt38-assistant-left{left:12px;right:auto;transform:none}
      #bt38Assistant.bt38-assistant-right{right:12px;left:auto;transform:none;flex-direction:row-reverse}
      #bt38Assistant.bt38-assistant-centre{left:50%;right:auto;transform:translateX(-50%)}
      #bt38AssistantRobot{width:72px;height:82px;object-fit:contain;flex:0 0 auto;filter:drop-shadow(0 5px 6px rgba(35,46,72,.16))}
      #bt38AssistantBubble{background:rgba(255,255,255,.97);border:1px solid #e3e7ef;border-radius:12px;padding:9px 12px;box-shadow:0 5px 18px rgba(22,31,52,.10);font-size:12px;line-height:1.35;font-weight:650;color:#243047;max-width:238px}
      #bt38AssistantBubble strong{color:#3159d8}
      .bt38-assistant-rocket{position:fixed;z-index:1036;font-size:24px;pointer-events:none;animation:bt38RocketLaunch .9s cubic-bezier(.2,.7,.2,1) forwards;filter:drop-shadow(0 3px 3px rgba(31,41,55,.18))}
      @keyframes bt38RocketLaunch{0%{opacity:0;transform:translate(0,8px) rotate(-18deg) scale(.72)}18%{opacity:1}100%{opacity:0;transform:translate(78px,-118px) rotate(-8deg) scale(1.08)}}
      @media(max-width:700px){body.bt38-assistant-space{padding-bottom:94px!important}#bt38Assistant{bottom:7px;left:8px!important;right:8px!important;transform:none!important;max-width:none;justify-content:center;flex-direction:row!important}#bt38AssistantRobot{width:58px;height:66px}#bt38AssistantBubble{max-width:calc(100vw - 92px);font-size:11px;padding:8px 10px}}
      @media(prefers-reduced-motion:reduce){#bt38Assistant{transition:none}.bt38-assistant-rocket{animation:none;opacity:1}}
    `;
    document.head.appendChild(style);
  }

  function installAssistantHost() {
    if (document.getElementById('bt38Assistant')) return;
    injectAssistantStyles();
    host = document.createElement('aside');
    host.id = 'bt38Assistant';
    host.setAttribute('aria-live', 'polite');
    host.innerHTML = '<img id="bt38AssistantRobot" alt="BT38 assistant"><div id="bt38AssistantBubble"></div>';
    document.body.appendChild(host);
    document.body.classList.add('bt38-assistant-space');
    image = document.getElementById('bt38AssistantRobot');
    message = document.getElementById('bt38AssistantBubble');
  }

  function safeRandomAnchor() {
    if (!host) return;
    host.classList.remove(...anchors);
    if (window.matchMedia('(max-width:700px)').matches) {
      host.classList.add('bt38-assistant-centre');
      return;
    }
    const shuffled = anchors.slice().sort(function () { return Math.random() - 0.5; });
    const collisionSelector = 'button,a,input,select,textarea,table,.modal.show,.offcanvas.show,.dropdown-menu.show,[role="dialog"]';

    for (const candidate of shuffled) {
      host.classList.remove(...anchors);
      host.classList.add(candidate);
      const rect = host.getBoundingClientRect();
      const points = [
        [rect.left + 8, rect.top + 8],
        [rect.right - 8, rect.top + 8],
        [rect.left + rect.width / 2, rect.top + rect.height / 2],
        [rect.right - 8, rect.bottom - 8]
      ];
      const blocked = points.some(function (point) {
        return document.elementsFromPoint(point[0], point[1]).some(function (el) {
          return el !== host && !host.contains(el) && Boolean(el.closest(collisionSelector));
        });
      });
      if (!blocked) return;
    }

    host.classList.remove(...anchors);
    host.classList.add('bt38-assistant-right');
  }

  function randomFrom(values) {
    return values[Math.floor(Math.random() * values.length)];
  }

  function assetFor(count, forceFresh) {
    if (count === 0) return '/static/img/bt38-guide-complete.svg';
    if (!forceFresh && pagePose) return pagePose;
    if (count === 1) pagePose = randomFrom(closePoses);
    else if (count <= 3) pagePose = randomFrom(closePoses.concat(['/static/img/bt38-guide-active.svg']));
    else pagePose = randomFrom(activePoses);
    return pagePose;
  }

  function normalMessage(count) {
    if (count === 0) return '☕ <strong>All done.</strong> I’ll keep watch.';
    if (count === 1) return '💪 <strong>Nearly there.</strong> Just 1 action left.';
    if (count <= 3) return `👍 <strong>Great progress.</strong> ${count} actions left.`;
    return `🤖 <strong>${count} actions to sort.</strong> I’ll help you through them.`;
  }

  function launchRocket() {
    if (!host || !image || window.matchMedia('(prefers-reduced-motion:reduce)').matches) return;
    const rect = image.getBoundingClientRect();
    const rocket = document.createElement('span');
    rocket.className = 'bt38-assistant-rocket';
    rocket.textContent = '🚀';
    rocket.setAttribute('aria-hidden', 'true');
    rocket.style.left = `${Math.max(8, rect.left + rect.width * .58)}px`;
    rocket.style.top = `${Math.max(8, rect.top + rect.height * .16)}px`;
    document.body.appendChild(rocket);
    window.setTimeout(function () { rocket.remove(); }, 1000);
  }

  function controlInfo(target) {
    const control = target && target.closest ? target.closest('button,a,[role="button"],input[type="submit"]') : null;
    if (!control) return null;
    if (control.closest('#bt38SideNav,.navbar,#bt38NotificationPanel,.breadcrumb,.pagination')) return null;

    const href = String(control.getAttribute('href') || '').toLowerCase();
    const action = String(control.getAttribute('data-action') || '').toLowerCase();
    const text = String(control.textContent || control.value || '').trim().toLowerCase();

    if (control.tagName === 'A' && href && !action && !control.hasAttribute('role')) return null;
    return {control, text, href, action};
  }

  function actionKind(target) {
    const info = controlInfo(target);
    if (!info) return '';
    const haystack = `${info.text} ${info.href} ${info.action}`;
    if (/(^|\s)push(\s|$)|\/push\b/.test(haystack)) return 'push';
    if (/dispatch|ship|fulfil|fulfill/.test(haystack)) return 'dispatch';
    if (/unlink|ungroup|remove link/.test(haystack)) return 'unlink';
    if (/link|group/.test(haystack)) return 'link';
    if (/buy label|purchase label|label/.test(haystack)) return 'label';
    if (/print/.test(haystack)) return 'print';
    if (/scan/.test(haystack)) return 'scan';
    if (/import/.test(haystack)) return 'import';
    if (/sync|refresh/.test(haystack)) return 'sync';
    if (/confirm|approve/.test(haystack)) return 'confirm';
    if (/save|update|apply/.test(haystack)) return 'save';
    if (/connect|authori[sz]e|reconnect/.test(haystack)) return 'connect';
    if (/retry|try again/.test(haystack)) return 'retry';
    return '';
  }

  function showTemporary(html, pose, milliseconds) {
    if (!host || !image || !message) return;
    if (temporaryMessageTimer) window.clearTimeout(temporaryMessageTimer);
    if (pose) image.src = pose;
    message.innerHTML = html;
    safeRandomAnchor();
    host.classList.add('bt38-ready');
    temporaryMessageTimer = window.setTimeout(function () {
      temporaryMessageTimer = null;
      if (Number.isFinite(currentCount)) renderCount(currentCount, null, false);
    }, milliseconds || 2600);
  }

  function workingReaction(kind) {
    const poseActive = '/static/img/bt38-guide-active.svg';
    const poseProgress = '/static/img/bt38-guide-progress.svg';
    const reactions = {
      dispatch: ['📦 <strong>On it.</strong> Let’s get this order moving.', poseActive, 2200],
      link: ['🔗 <strong>Good move.</strong> I’m checking that link.', poseProgress, 2200],
      unlink: ['🧩 <strong>Updating the link.</strong> I’ll keep the stock relationship clear.', poseProgress, 2400],
      label: ['🏷️ <strong>Getting the label ready.</strong> Nearly there.', poseActive, 2200],
      print: ['🖨️ <strong>Ready to print.</strong> Keep it moving.', poseProgress, 1900],
      scan: ['📷 <strong>Scanning.</strong> I’m with you.', poseActive, 1800],
      import: ['✨ <strong>Bringing that in.</strong> I’ll watch the result.', poseActive, 2200],
      sync: ['🔄 <strong>Checking for updates.</strong> I’ll keep it tidy.', poseProgress, 2200],
      confirm: ['👍 <strong>Approved.</strong> Moving this one forward.', poseProgress, 2000],
      connect: ['🔌 <strong>Connecting.</strong> I’ll tell you how it goes.', poseActive, 2200],
      retry: ['💪 <strong>Trying again.</strong> Let’s get this one cleared.', poseActive, 2200],
      save: ['👍 <strong>Saving that.</strong> One step closer.', poseProgress, 1900]
    };

    if (kind === 'push') {
      launchRocket();
      showTemporary('🚀 <strong>Launching that update.</strong> I’ll keep an eye on it.', poseActive, 2200);
      return;
    }
    const reaction = reactions[kind];
    if (reaction) showTemporary(reaction[0], reaction[1], reaction[2]);
  }

  function renderCount(count, previousCount, freshPose) {
    if (!host || !image || !message || !Number.isFinite(count)) return;
    const completed = Number.isFinite(previousCount) && previousCount > count
      ? previousCount - count
      : 0;
    if (freshPose) pagePose = null;
    image.src = assetFor(count, Boolean(freshPose));
    image.alt = count === 0 ? 'BT38 assistant taking a well-earned break' : 'BT38 assistant helping with today’s actions';
    if (completed > 0) {
      message.innerHTML = count === 0
        ? `🎉 <strong>Well done!</strong> ${completed} completed · you’re all caught up.`
        : `👍 <strong>Well done!</strong> ${completed} completed · ${count} to go.`;
    } else {
      message.innerHTML = normalMessage(count);
    }
    safeRandomAnchor();
    host.classList.add('bt38-ready');
  }

  function parseDashboardCount(doc) {
    const titles = Array.from(doc.querySelectorAll('.bt38-panel-title'));
    const actionTitle = titles.find(function (node) {
      return /^Actions\b/i.test(String(node.textContent || '').trim());
    });
    if (!actionTitle) return null;
    const match = String(actionTitle.textContent || '').match(/Actions\s*\((\d+)\)/i);
    if (!match) return null;
    const value = Number(match[1]);
    return Number.isFinite(value) ? value : null;
  }

  function currentPageDashboardCount() {
    return parseDashboardCount(document);
  }

  async function readDashboardActionCount() {
    const local = currentPageDashboardCount();
    if (Number.isFinite(local)) return local;

    const response = await fetch(dashboardPath, {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {'Accept': 'text/html'}
    });
    if (!response.ok) return null;
    const html = await response.text();
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return parseDashboardCount(doc);
  }

  async function refreshAssistant(options) {
    if (refreshRunning || document.visibilityState === 'hidden') return;
    refreshRunning = true;
    try {
      const count = await readDashboardActionCount();
      if (!Number.isFinite(count)) return;
      const previous = Number.isFinite(currentCount)
        ? currentCount
        : Number(window.sessionStorage.getItem(cacheKey));
      const safePrevious = Number.isFinite(previous) ? previous : null;
      currentCount = count;
      window.sessionStorage.setItem(cacheKey, String(count));
      if (!temporaryMessageTimer) renderCount(count, safePrevious, Boolean(options && options.freshPose));
    } catch (error) {
      console.debug('[BT38 assistant] Dashboard action read unavailable', error);
    } finally {
      refreshRunning = false;
    }
  }

  function friendlyBellTitle(raw) {
    const text = String(raw || '').trim();
    if (/marketplace quantity push succeeded/i.test(text)) return '👍 Stock updated';
    if (/marketplace quantity push failed/i.test(text)) return '⚠️ Stock update needs attention';
    if (/marketplace quantity push skipped/i.test(text)) return '👌 Stock already matched';
    if (/product linking updated/i.test(text)) return '🔗 Product link updated';
    if (/product linking.*removed|unlink/i.test(text)) return '🧩 Product link removed';
    if (/listing.*imported|listing.*recovered|new listing/i.test(text)) return '✨ Listing added';
    if (/marketplace sale|order.*received|sale received/i.test(text)) return '🛍️ Sale received';
    if (/dispatch.*succeeded|shipment.*confirmed/i.test(text)) return '👍 Order dispatched';
    if (/dispatch.*failed|shipment.*failed/i.test(text)) return '⚠️ Dispatch needs attention';
    if (/tracking.*updated|tracking.*received/i.test(text)) return '📍 Tracking updated';
    if (/label.*purchased|label.*created/i.test(text)) return '🏷️ Label ready';
    if (/connection.*succeeded|connected successfully/i.test(text)) return '🔌 Store connected';
    if (/connection.*failed|auth.*failed|token.*failed/i.test(text)) return '⚠️ Store connection needs attention';
    return text.replace(/\bgoverned\b/gi, '').replace(/\bruntime\b/gi, '').replace(/\s{2,}/g, ' ').trim();
  }

  function friendlyMeta(raw) {
    return String(raw || '')
      .replace(/^Group\s+(\d+)$/i, 'Product group $1')
      .replace(/\bgoverned\b/gi, '')
      .replace(/\bruntime\b/gi, '')
      .replace(/\breconcile\b/gi, 'check')
      .replace(/\bpropagation\b/gi, 'update')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  function alignBellLanguage(root) {
    const list = root && root.querySelectorAll ? root : document;
    list.querySelectorAll('#bt38NotificationList .list-group-item').forEach(function (item) {
      const title = item.querySelector('.mt-1.fw-semibold');
      if (title) title.textContent = friendlyBellTitle(title.textContent);
      item.querySelectorAll('.small.text-muted.mt-1').forEach(function (meta) {
        meta.textContent = friendlyMeta(meta.textContent);
      });
      item.dataset.bt38AssistantLanguageAligned = '1';
    });
  }

  function successMessageFromText(raw) {
    const text = String(raw || '').replace(/\s+/g, ' ').trim();
    const pushed = text.match(/(\d+)\s+(?:listing(?:s)?\s+)?pushed/i);
    const skipped = text.match(/(\d+)\s+(?:listing(?:s)?\s+)?skipped/i);
    const updated = text.match(/(\d+)\s+(?:listing(?:s)?\s+)?updated/i);
    const linked = text.match(/(\d+)\s+(?:listing(?:s)?\s+)?linked/i);

    const parts = [];
    if (pushed) parts.push(`${pushed[1]} pushed`);
    if (updated) parts.push(`${updated[1]} updated`);
    if (linked) parts.push(`${linked[1]} linked`);
    if (skipped) parts.push(`${skipped[1]} already correct`);

    if (parts.length) return `👍 <strong>Well done!</strong> ${parts.join(' · ')}.`;
    if (/dispatch|shipped|shipment/i.test(text)) return '📦 <strong>Well done!</strong> That order is moving.';
    if (/label/i.test(text)) return '🏷️ <strong>Nice!</strong> Your label is ready.';
    if (/link/i.test(text)) return '🔗 <strong>Nice work!</strong> That product link is updated.';
    if (/connect/i.test(text)) return '🔌 <strong>Connected!</strong> That store is ready.';
    return '👍 <strong>Nice work.</strong> That update completed.';
  }

  function alertReaction(node) {
    if (!node || !node.classList || !node.classList.contains('alert')) return;
    if (node.dataset.bt38AssistantReacted === '1') return;
    node.dataset.bt38AssistantReacted = '1';
    const raw = String(node.textContent || '').replace(/\s+/g, ' ').trim();
    if (!raw) return;

    if (node.classList.contains('alert-success')) {
      showTemporary(successMessageFromText(raw), '/static/img/bt38-guide-progress.svg', 3000);
      void refreshAssistant();
    } else if (node.classList.contains('alert-danger')) {
      showTemporary('⚠️ <strong>I hit a problem.</strong> The details are above — we can sort it.', '/static/img/bt38-guide-active.svg', 3200);
    } else if (node.classList.contains('alert-warning')) {
      showTemporary('👀 <strong>Almost there.</strong> This one needs a quick check.', '/static/img/bt38-guide-nearly-done.svg', 2800);
    }
  }

  function installPresentationObservers() {
    alignBellLanguage(document);
    document.querySelectorAll('.alert').forEach(alertReaction);

    const observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (!(node instanceof Element)) return;
          if (node.matches('.alert')) alertReaction(node);
          node.querySelectorAll && node.querySelectorAll('.alert').forEach(alertReaction);
          alignBellLanguage(node);
        });
      });
    });
    observer.observe(document.body, {childList:true, subtree:true});
  }

  function installAssistant() {
    installAssistantHost();
    const cached = Number(window.sessionStorage.getItem(cacheKey));
    if (Number.isFinite(cached)) {
      currentCount = cached;
      renderCount(cached, null, true);
    }
    installPresentationObservers();
    void refreshAssistant({freshPose:true});
  }

  document.addEventListener('click', function (event) {
    const kind = actionKind(event.target);
    if (!kind) return;
    window.setTimeout(function () { workingReaction(kind); }, 80);
  }, true);

  window.addEventListener('bt38-marketplace-event', function () {
    void refreshAssistant();
  });
  window.addEventListener('pageshow', function () {
    pagePose = null;
    void refreshAssistant({freshPose:true});
  });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') void refreshAssistant();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installAssistant, {once:true});
  } else {
    installAssistant();
  }
})();

// Shared commercial wording polish. This changes presentation text only and
// deliberately leaves System Activity/diagnostic pages untouched.
(function () {
  'use strict';

  if (window.bt38FriendlyWordingInstalled) return;
  window.bt38FriendlyWordingInstalled = true;

  function isDiagnosticPage() {
    return /^\/admin\/system-activity\/?$/i.test(window.location.pathname || '');
  }

  const exactPhrases = new Map([
    ['Marketplace events', 'Your activity'],
    ['No marketplace events found.', 'You’re all caught up.'],
    ['View sales dashboard', 'Back to dashboard'],
    ['Preview only. No marketplace sync was executed.', 'Preview only — nothing was changed.'],
    ['Store active state updated through governed path.', '👍 Store status updated.'],
    ['This old action is disabled. Use the governed warehouse, store, listing, or group path.', 'This action has moved. Use the current BT38 action instead.'],
    ['Store deletion is disabled in governed mode until delete rules are approved.', 'Store deletion is not available yet.'],
    ['Amazon setup preview only. Live credential setup is not wired through old routes.', 'Amazon setup preview only — nothing has been connected yet.']
  ]);

  function friendlyText(value) {
    const original = String(value || '');
    const trimmed = original.trim();
    if (!trimmed) return original;
    if (exactPhrases.has(trimmed)) return original.replace(trimmed, exactPhrases.get(trimmed));

    let next = trimmed
      .replace(/\bgoverned mode\b/gi, 'BT38')
      .replace(/\bgoverned path\b/gi, 'BT38')
      .replace(/\bgoverned route\b/gi, 'current BT38 action')
      .replace(/\bruntime reconcile\b/gi, 'system check')
      .replace(/\breconciliation\b/gi, 'check')
      .replace(/\breconcile\b/gi, 'check')
      .replace(/\bpropagation\b/gi, 'update')
      .replace(/\bmarketplace quantity push\b/gi, 'stock update')
      .replace(/\bmarketplace write\b/gi, 'marketplace update')
      .replace(/\s{2,}/g, ' ')
      .trim();

    return original.replace(trimmed, next);
  }

  function alignNode(root) {
    if (isDiagnosticPage()) return;
    const host = root instanceof Element ? root : document.body;
    if (!host) return;

    const selector = '#bt38NotificationPanel small,#bt38NotificationPanel .text-muted,#bt38NotificationPanel a,.alert,.form-text,.small.text-muted,.badge';
    const elements = [];
    if (host.matches && host.matches(selector)) elements.push(host);
    host.querySelectorAll && host.querySelectorAll(selector).forEach(function (node) { elements.push(node); });

    elements.forEach(function (node) {
      if (node.closest('code,pre,#bt38Assistant,#bt38SideNav,.navbar')) return;
      if (node.children.length !== 0) return;
      const before = node.textContent;
      const after = friendlyText(before);
      if (after !== before) node.textContent = after;
    });
  }

  function installFriendlyWording() {
    if (isDiagnosticPage()) return;
    alignNode(document.body);
    const observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node instanceof Element) alignNode(node);
        });
      });
    });
    observer.observe(document.body, {childList:true, subtree:true});
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installFriendlyWording, {once:true});
  } else {
    installFriendlyWording();
  }
})();

// Shared FBM-style bounded list bar for the main operational list pages.
// This is presentation/paging alignment only. It never performs marketplace,
// provider, shipment-status or background reads. Warehouse keeps its existing
// server per_page path; other pages reveal only rows already present in the DOM.
(function () {
  'use strict';

  if (window.bt38SharedListBarInstalled) return;
  window.bt38SharedListBarInstalled = true;

  const step = 15;
  const localHiddenClass = 'bt38-shared-list-hidden';
  const supportedPaths = new Set([
    '/warehouse',
    '/amazon-fba-stock',
    '/product-linking',
    '/stores',
    '/listings',
    '/groups',
    '/admin/system-activity'
  ]);

  function injectStyles() {
    if (document.getElementById('bt38SharedListBarStyles')) return;
    const style = document.createElement('style');
    style.id = 'bt38SharedListBarStyles';
    style.textContent = `
      .bt38-shared-list-bar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 14px;background:#fff;border:1px solid #e5e7eb;border-radius:0 0 10px 10px;box-shadow:0 4px 10px rgba(15,23,42,.04);font-size:12px;color:#6b7280}
      .bt38-shared-list-actions{display:flex;align-items:center;gap:8px;margin-left:auto}
      .bt38-shared-list-bar .btn{white-space:nowrap}
      .${localHiddenClass}{display:none!important}
      @media(max-width:700px){.bt38-shared-list-bar{align-items:flex-start}.bt38-shared-list-actions{width:100%;justify-content:flex-end}}
    `;
    document.head.appendChild(style);
  }

  function cleanPath() {
    const path = String(window.location.pathname || '').replace(/\/$/, '');
    return path || '/';
  }

  function pageLabel() {
    const path = cleanPath();
    if (document.getElementById('mcf-orders-body')) return 'MCF orders';
    if (path === '/warehouse') return 'warehouse items';
    if (path === '/amazon-fba-stock') return 'FBA records';
    if (path === '/product-linking') return 'product groups';
    if (path === '/stores') return 'stores';
    if (path === '/listings') return 'listings';
    if (path === '/groups') return 'groups';
    if (path === '/admin/system-activity') return 'activity records';
    return 'records';
  }

  function directRows(table) {
    if (!table || !table.tBodies || !table.tBodies.length) return [];
    return Array.from(table.tBodies[0].rows).filter(function (row) {
      if (row.id === 'mcf-empty-row' || row.id === 'mcf-no-match-row') return false;
      if (row.querySelector('td[colspan]') && row.cells.length === 1) return false;
      return true;
    });
  }

  function primaryTable() {
    if (document.querySelector('.fbm-order-row')) return null; // FBM owns its canonical bar.
    const mcf = document.getElementById('mcf-orders-body');
    if (mcf) return mcf.closest('table');
    const warehouse = document.querySelector('[data-bt38-page="warehouse"] .bt38-stock-table');
    if (warehouse) return warehouse;
    const productLinking = document.querySelector('[data-bt38-page="productLinking"] #warehouseDataContainer table');
    if (productLinking) return productLinking;
    if (!supportedPaths.has(cleanPath())) return null;
    const candidates = Array.from(document.querySelectorAll('main table')).filter(function (table) {
      return !table.closest('.modal,.offcanvas') && directRows(table).length > 0;
    });
    candidates.sort(function (a, b) { return directRows(b).length - directRows(a).length; });
    return candidates[0] || null;
  }

  function barHost(table) {
    if (!table) return null;
    const shell = table.closest('.bt38-table-shell,.card');
    if (shell) return shell;
    return table.closest('.table-responsive')?.parentElement || table.parentElement;
  }

  function stateKey() {
    return `bt38:listbar:${cleanPath()}:limit`;
  }

  function readLocalLimit() {
    try {
      const value = Number.parseInt(window.sessionStorage.getItem(stateKey()) || String(step), 10);
      return Math.max(step, Number.isFinite(value) ? value : step);
    } catch (error) {
      return step;
    }
  }

  function saveLocalLimit(value) {
    try { window.sessionStorage.setItem(stateKey(), String(value)); } catch (error) {}
  }

  function createBar(host) {
    let bar = host.querySelector(':scope > .bt38-shared-list-bar');
    if (bar) return bar;
    bar = document.createElement('div');
    bar.className = 'bt38-shared-list-bar';
    bar.innerHTML = '<span class="bt38-shared-list-copy"></span><div class="bt38-shared-list-actions"><button class="btn btn-sm btn-outline-secondary bt38-list-latest" type="button">Show latest 15</button><button class="btn btn-sm btn-outline-primary bt38-list-more" type="button">Show 15 more</button></div>';
    host.appendChild(bar);
    return bar;
  }

  function renderCopy(bar, shown, total, label, hasMore) {
    const copy = bar.querySelector('.bt38-shared-list-copy');
    copy.textContent = hasMore
      ? `Showing the latest ${shown} ${label}. Older ${label} show only when expanded.`
      : `Showing ${shown} ${label}.`;
    const more = bar.querySelector('.bt38-list-more');
    more.disabled = !hasMore;
  }

  function installWarehouseBar(table, host, bar) {
    if (bar.dataset.bt38Mode === 'warehouse') return;
    bar.dataset.bt38Mode = 'warehouse';
    const rows = directRows(table);
    const params = new URLSearchParams(window.location.search);
    const current = Math.max(step, Number.parseInt(params.get('per_page') || String(rows.length || step), 10) || step);
    const countText = String(document.querySelector('.bt38-table-count')?.textContent || '');
    const totalMatch = countText.match(/(\d[\d,]*)/);
    const total = totalMatch ? Number.parseInt(totalMatch[1].replace(/,/g, ''), 10) : rows.length;
    const shown = Math.min(current, total || rows.length);
    renderCopy(bar, shown, total, pageLabel(), total > shown);

    function go(limit) {
      const next = new URLSearchParams(window.location.search);
      next.set('per_page', String(limit));
      next.set('page', '1');
      window.location.assign(`${window.location.pathname}?${next.toString()}#bt38SharedListBar`);
    }

    bar.id = 'bt38SharedListBar';
    bar.querySelector('.bt38-list-latest').addEventListener('click', function () { go(step); });
    bar.querySelector('.bt38-list-more').addEventListener('click', function () { go(Math.min(total || current + step, current + step)); });
  }

  function installMcfBar(table, host, bar) {
    if (bar.dataset.bt38Mode === 'mcf') return;
    bar.dataset.bt38Mode = 'mcf';
    const nativeSize = document.getElementById('mcf-page-size');
    const nativeFooter = nativeSize?.closest('.card-footer');
    if (nativeFooter) nativeFooter.classList.add('d-none');
    if (nativeSize) {
      nativeSize.value = '100';
      nativeSize.dispatchEvent(new Event('input', {bubbles:true}));
    }

    function apply() {
      const rows = directRows(table).filter(function (row) { return !row.classList.contains('d-none'); });
      const limit = readLocalLimit();
      rows.forEach(function (row, index) { row.classList.toggle(localHiddenClass, index >= limit); });
      renderCopy(bar, Math.min(limit, rows.length), rows.length, pageLabel(), rows.length > limit);
    }

    bar.querySelector('.bt38-list-latest').addEventListener('click', function () { saveLocalLimit(step); apply(); });
    bar.querySelector('.bt38-list-more').addEventListener('click', function () { saveLocalLimit(readLocalLimit() + step); apply(); });
    ['mcf-status-filter', 'mcf-search'].forEach(function (id) {
      const control = document.getElementById(id);
      if (control) control.addEventListener('input', function () { window.setTimeout(apply, 0); });
    });
    apply();
  }

  function installLocalBar(table, host, bar) {
    if (bar.dataset.bt38Mode === 'local') return;
    bar.dataset.bt38Mode = 'local';

    function apply() {
      const rows = directRows(table);
      const limit = readLocalLimit();
      rows.forEach(function (row, index) { row.classList.toggle(localHiddenClass, index >= limit); });
      renderCopy(bar, Math.min(limit, rows.length), rows.length, pageLabel(), rows.length > limit);
    }

    bar.querySelector('.bt38-list-latest').addEventListener('click', function () { saveLocalLimit(step); apply(); });
    bar.querySelector('.bt38-list-more').addEventListener('click', function () { saveLocalLimit(readLocalLimit() + step); apply(); });
    apply();
  }

  function install() {
    const table = primaryTable();
    if (!table || table.dataset.bt38SharedListBar === '1') return false;
    const host = barHost(table);
    if (!host) return false;
    injectStyles();
    table.dataset.bt38SharedListBar = '1';
    const bar = createBar(host);
    if (cleanPath() === '/warehouse') installWarehouseBar(table, host, bar);
    else if (document.getElementById('mcf-orders-body')) installMcfBar(table, host, bar);
    else installLocalBar(table, host, bar);
    return true;
  }

  function installWithAsyncSupport() {
    if (install()) return;
    const observer = new MutationObserver(function () {
      if (install()) observer.disconnect();
    });
    observer.observe(document.body, {childList:true, subtree:true});
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installWithAsyncSupport, {once:true});
  else installWithAsyncSupport();
})();