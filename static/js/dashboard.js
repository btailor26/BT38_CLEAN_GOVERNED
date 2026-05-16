/**
 * Dashboard JavaScript functionality
 * Handles real-time updates, UI interactions, and API calls
 */

/**
 * Smart fetch() helper with auto-detection
 * - Auto-stringifies plain objects and sets Content-Type: application/json
 * - Leaves FormData alone (browser sets multipart boundary)
 * - Handles 204 No Content and empty-body responses
 * - Prevents JSON parse explosions and 415 Unsupported Media Type
 * 
 * Usage:
 *   // JSON - auto-stringified
 *   const data = await api('/api/endpoint', { method: 'POST', body: {sku: 'ABC', qty: 5} });
 * 
 *   // FormData - auto-detected
 *   const fd = new FormData(); fd.append('file', file);
 *   const data = await api('/api/upload', { method: 'POST', body: fd });
 */
async function api(url, options = {}) {
    const init = { method: 'GET', credentials: 'same-origin', ...options };
    
    // Auto-detect: plain object → JSON, FormData → leave alone
    const isPlainObj = init.body && typeof init.body === 'object' && !(init.body instanceof FormData);
    
    if (isPlainObj) {
        // Plain object: auto-stringify and set JSON headers
        init.body = JSON.stringify(init.body);
        init.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            ...(init.headers || {})
        };
    } else {
        // FormData or no body: only set Accept header
        init.headers = {
            'Accept': 'application/json',
            ...(init.headers || {})
        };
        // Do NOT set Content-Type for FormData - browser handles it
    }

    const res = await fetch(url, init);
    const ct = (res.headers.get('content-type') || '').toLowerCase();

    // 204 No Content → return null cleanly
    if (res.status === 204 || res.headers.get('content-length') === '0') {
        if (!res.ok) throw new Error(`HTTP ${res.status} (empty)`);
        return null;
    }

    // JSON body
    if (ct.includes('application/json')) {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            const msg = data?.error || data?.detail || JSON.stringify(data).slice(0, 400);
            throw new Error(`HTTP ${res.status}: ${msg || 'Unknown error'}`);
        }
        return data;
    }

    // Fallback: text (HTML, plain, etc.)
    const text = await res.text();
    if (!res.ok) throw new Error(`HTTP ${res.status} (non-JSON): ${text.slice(0, 400)}`);
    return text;
}

/**
 * Show user-friendly error notification
 */
function showError(message, error) {
    console.error(message, error);
    const errorText = error?.message || error?.toString() || 'Unknown error';
    
    if (window.inventoryDashboard) {
        window.inventoryDashboard.showNotification(
            `${message}: ${errorText}`,
            'danger',
            5000
        );
    } else {
        alert(`${message}\n\n${errorText}`);
    }
}

class InventoryDashboard {
    constructor() {
        this.syncStatusInterval = null;
        this.lastUpdateTime = null;
        
        this.init();
    }
    
    init() {
        this.initEventListeners();
        this.startSyncStatusUpdates();
        this.initTooltips();
    }
    
    initEventListeners() {
        // Auto-refresh buttons
        const refreshButtons = document.querySelectorAll('[data-refresh]');
        refreshButtons.forEach(button => {
            button.addEventListener('click', () => {
                this.refreshData(button.dataset.refresh);
            });
        });
        
        // Form enhancements
        this.enhanceForms();
        
        // Keyboard shortcuts
        this.initKeyboardShortcuts();
    }
    
    initKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            // Ctrl+N for new item
            if (e.ctrlKey && e.key === 'n') {
                e.preventDefault();
                const addItemLink = document.querySelector('a[href*="add"]');
                if (addItemLink) {
                    window.location.href = addItemLink.href;
                }
            }
            
            // Escape to go back
            if (e.key === 'Escape') {
                const backButton = document.querySelector('.btn[href*="inventory"], .btn[href*="stores"]');
                if (backButton) {
                    window.location.href = backButton.href;
                }
            }
        });
    }
    
    startSyncStatusUpdates() {
        // Initial update
        this.updateSyncStatus();
        
        // Set up interval for periodic updates
        this.syncStatusInterval = setInterval(() => {
            this.updateSyncStatus();
        }, 30000); // Update every 30 seconds
    }
    
    async updateSyncStatus() {
        try {
            const stores = await api('/api/sync-status');
            this.displaySyncStatus(stores);
            this.updateLastRefreshTime();
            
        } catch (error) {
            console.error('Error updating sync status:', error);
            this.showSyncError();
        }
    }
    
    displaySyncStatus(stores) {
        const statusElement = document.getElementById('sync-status');
        if (!statusElement || !stores || stores.length === 0) {
            return;
        }
        
        const syncingStores = stores.filter(store => store.sync_status === 'syncing');
        const errorStores = stores.filter(store => store.sync_status === 'error');
        const successStores = stores.filter(store => store.sync_status === 'success');
        
        let statusText = '';
        let badgeClass = '';
        let indicatorClass = '';
        
        if (syncingStores.length > 0) {
            statusText = `${syncingStores.length} Syncing`;
            badgeClass = 'bg-warning';
            indicatorClass = 'syncing';
        } else if (errorStores.length > 0) {
            statusText = `${errorStores.length} Error(s)`;
            badgeClass = 'bg-danger';
            indicatorClass = 'error';
        } else if (successStores.length > 0) {
            statusText = 'All Synced';
            badgeClass = 'bg-success';
            indicatorClass = 'success';
        } else {
            statusText = 'No Active Stores';
            badgeClass = 'bg-secondary';
            indicatorClass = 'pending';
        }
        
        statusElement.innerHTML = `
            Sync Status: 
            <span class="sync-indicator ${indicatorClass}"></span>
            <span class="badge ${badgeClass}">${statusText}</span>
        `;
    }
    
    showSyncError() {
        const statusElement = document.getElementById('sync-status');
        if (statusElement) {
            statusElement.innerHTML = `
                Sync Status: 
                <span class="sync-indicator error"></span>
                <span class="badge bg-danger">Connection Error</span>
            `;
        }
    }
    
    updateLastRefreshTime() {
        const lastUpdateElement = document.getElementById('last-update');
        if (lastUpdateElement) {
            this.lastUpdateTime = new Date();
            lastUpdateElement.textContent = this.lastUpdateTime.toLocaleString();
        }
    }
    
    async refreshData(type) {
        const button = document.querySelector(`[data-refresh="${type}"]`);
        if (button) {
            const originalContent = button.innerHTML;
            button.innerHTML = '<i data-feather="loader" class="me-1"></i>Refreshing...';
            button.disabled = true;
            
            // Replace feather icons
            feather.replace();
            
            try {
                if (type === 'sync') {
                    await this.updateSyncStatus();
                } else if (type === 'page') {
                    window.location.reload();
                }
                
                // Show success feedback
                button.innerHTML = '<i data-feather="check" class="me-1"></i>Updated';
                feather.replace();
                
                setTimeout(() => {
                    button.innerHTML = originalContent;
                    button.disabled = false;
                    feather.replace();
                }, 2000);
                
            } catch (error) {
                console.error('Error refreshing data:', error);
                
                button.innerHTML = '<i data-feather="x" class="me-1"></i>Error';
                feather.replace();
                
                setTimeout(() => {
                    button.innerHTML = originalContent;
                    button.disabled = false;
                    feather.replace();
                }, 2000);
            }
        }
    }
    
    enhanceForms() {
        // Add loading states to form submissions
        const forms = document.querySelectorAll('form');
        forms.forEach(form => {
            form.addEventListener('submit', (e) => {
                const submitButton = form.querySelector('button[type="submit"]');
                if (submitButton && !form.dataset.noLoading) {
                    const originalContent = submitButton.innerHTML;
                    submitButton.innerHTML = '<i data-feather="loader" class="me-1"></i>Saving...';
                    submitButton.disabled = true;
                    feather.replace();
                }
            });
        });
        
        // Auto-save form data to localStorage (for recovery)
        const inputsToSave = document.querySelectorAll('input[type="text"], input[type="email"], textarea');
        inputsToSave.forEach(input => {
            const key = `form_${window.location.pathname}_${input.name}`;
            
            // Load saved data
            const saved = localStorage.getItem(key);
            if (saved && !input.value) {
                input.value = saved;
            }
            
            // Save data on change
            input.addEventListener('input', () => {
                if (input.value) {
                    localStorage.setItem(key, input.value);
                } else {
                    localStorage.removeItem(key);
                }
            });
        });
        
        // Clear saved data on successful form submission
        forms.forEach(form => {
            form.addEventListener('submit', () => {
                const inputs = form.querySelectorAll('input[type="text"], input[type="email"], textarea');
                inputs.forEach(input => {
                    const key = `form_${window.location.pathname}_${input.name}`;
                    localStorage.removeItem(key);
                });
            });
        });
    }
    
    initTooltips() {
        // Initialize Bootstrap tooltips if available
        if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
            const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
            tooltipTriggerList.map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl));
        }
    }
    
    // Utility method for showing temporary notifications
    showNotification(message, type = 'info', duration = 3000) {
        const notification = document.createElement('div');
        notification.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
        notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
        notification.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        document.body.appendChild(notification);
        
        // Auto-remove after duration
        setTimeout(() => {
            if (notification.parentNode) {
                notification.remove();
            }
        }, duration);
    }
    
    // Method to trigger manual sync for a store
    async triggerStoreSync(storeId, storeName) {
          this.showNotification('Direct store sync is retired. Use governed queue actions from Admin Product Linking / Command Center.', 'warning', 5000);
          return;
}
    
    // Clean up intervals when leaving the page
    destroy() {
        if (this.syncStatusInterval) {
            clearInterval(this.syncStatusInterval);
        }
    }
}

// Initialize dashboard when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.inventoryDashboard = new InventoryDashboard();
});

// Clean up when leaving the page
window.addEventListener('beforeunload', () => {
    if (window.inventoryDashboard) {
        window.inventoryDashboard.destroy();
    }
});

// Global utility functions
window.refreshSyncStatus = () => {
    if (window.inventoryDashboard) {
        window.inventoryDashboard.updateSyncStatus();
    }
};

// Global function for triggering sync (used by templates)
window.triggerSync = (storeId, storeName) => {
    if (window.inventoryDashboard) {
        window.inventoryDashboard.triggerStoreSync(storeId, storeName);
    }
};

// Auto-format currency inputs
document.addEventListener('DOMContentLoaded', () => {
    const priceInputs = document.querySelectorAll('input[name="price"]');
    priceInputs.forEach(input => {
        input.addEventListener('blur', (e) => {
            const value = parseFloat(e.target.value);
            if (!isNaN(value)) {
                e.target.value = value.toFixed(2);
            }
        });
    });
});

// Auto-uppercase SKU inputs
document.addEventListener('DOMContentLoaded', () => {
    const skuInputs = document.querySelectorAll('input[name="sku"]');
    skuInputs.forEach(input => {
        input.addEventListener('input', (e) => {
            e.target.value = e.target.value.toUpperCase();
        });
    });
});

// =================== PENDING CHANGES TRACKING SYSTEM ===================

/**
 * Global state for tracking pending stock changes
 */
class PendingChangesManager {
    constructor() {
        this.pendingChanges = new Map(); // item_id -> {original_quantity, new_quantity}
        this.originalQuantities = new Map(); // item_id -> original_quantity
        this.pendingBar = null;
        this.isSaving = false;
        
        this.initializePendingBar();
        this.initializeNavigationGuard();
        this.loadOriginalQuantities();
    }
    
    /**
     * Load original quantities for all items on the page
     */
    loadOriginalQuantities() {
        const qtyInputs = document.querySelectorAll('.qty-input');
        qtyInputs.forEach(input => {
            const itemId = input.id.replace('qty-', '');
            // Read from data attribute for reliable original value (not affected by DOM hydration timing)
            const dataQty = input.dataset.originalQuantity;
            const originalQty = dataQty !== undefined ? parseInt(dataQty) : parseInt(input.value);
            // Store the value (including 0 which is valid)
            this.originalQuantities.set(itemId, isNaN(originalQty) ? 0 : originalQty);
        });
    }
    
    /**
     * Track a quantity change
     */
    addPendingChange(itemId, newQuantity) {
        const originalQty = this.originalQuantities.get(itemId) || 0;
        
        if (newQuantity === originalQty) {
            // No change from original, remove from pending
            this.pendingChanges.delete(itemId);
            this.removePendingIndicator(itemId);
        } else {
            // Track the change
            this.pendingChanges.set(itemId, {
                original_quantity: originalQty,
                new_quantity: newQuantity
            });
            this.addPendingIndicator(itemId);
        }
        
        this.updatePendingBar();
        this.updateGroupTotalsWithPending();
    }
    
    /**
     * Get all pending changes in format for batch API
     */
    getPendingChangesForAPI() {
        const updates = [];
        for (const [itemId, change] of this.pendingChanges) {
            updates.push({
                item_id: parseInt(itemId),
                quantity: change.new_quantity
            });
        }
        return updates;
    }
    
    /**
     * Clear all pending changes
     */
    clearPendingChanges() {
        // Revert all quantities to original values
        for (const [itemId, change] of this.pendingChanges) {
            const qtyInput = document.getElementById(`qty-${itemId}`);
            if (qtyInput) {
                qtyInput.value = change.original_quantity;
                this.updateQuantityBadgeLocal(itemId, change.original_quantity);
            }
            this.removePendingIndicator(itemId);
        }
        
        this.pendingChanges.clear();
        this.updatePendingBar();
        this.updateGroupTotalsWithPending();
    }
    
    /**
     * Apply saved changes to the page (after successful save)
     */
    applyChanges(savedItems) {
        for (const item of savedItems) {
            const itemId = item.item_id.toString();
            
            // Update original quantity to the new saved value
            this.originalQuantities.set(itemId, item.new_quantity);
            
            // Update UI badges
            this.updateQuantityBadgeLocal(itemId, item.new_quantity, item.badge_class);
            
            // Update low stock indicators
            this.updateLowStockIndicatorsLocal(itemId, item.needs_reorder);
            
            // Remove pending indicator
            this.removePendingIndicator(itemId);
        }
        
        this.pendingChanges.clear();
        this.updatePendingBar();
        this.updateGroupTotalsWithPending();
    }
    
    /**
     * Create and manage the sticky pending changes bar
     */
    initializePendingBar() {
        // Create pending bar HTML
        const pendingBarHTML = `
            <div id="pending-changes-bar" class="pending-changes-bar d-none">
                <div class="container-fluid">
                    <div class="row align-items-center">
                        <div class="col-auto">
                            <i data-feather="edit-2" class="me-2"></i>
                            <span id="pending-count">0</span> changes pending
                        </div>
                        <div class="col-auto ms-auto">
                            <button type="button" class="btn btn-sm btn-outline-light me-2" id="discard-changes-btn">
                                <i data-feather="x" class="me-1"></i>
                                Discard
                            </button>
                            <button type="button" class="btn btn-sm btn-light" id="save-changes-btn">
                                <i data-feather="check" class="me-1"></i>
                                Save Changes
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Add CSS for the pending bar
        const pendingBarCSS = `
            <style>
                .pending-changes-bar {
                    position: fixed;
                    bottom: 0;
                    left: 0;
                    right: 0;
                    background-color: #dc3545;
                    color: white;
                    padding: 12px 0;
                    z-index: 1050;
                    box-shadow: 0 -2px 10px rgba(0,0,0,0.2);
                    border-top: 3px solid #b02a37;
                }
                
                .pending-indicator {
                    border: 2px solid #dc3545 !important;
                    background-color: #fff5f5 !important;
                    position: relative;
                }
                
                .pending-indicator::after {
                    content: "●";
                    color: #dc3545;
                    position: absolute;
                    top: -8px;
                    right: -8px;
                    font-size: 16px;
                    background: white;
                    border-radius: 50%;
                    width: 16px;
                    height: 16px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 12px;
                }
                
                .table-row-pending {
                    background-color: #fff5f5 !important;
                }
            </style>
        `;
        
        // Insert CSS and HTML
        document.head.insertAdjacentHTML('beforeend', pendingBarCSS);
        document.body.insertAdjacentHTML('beforeend', pendingBarHTML);
        
        this.pendingBar = document.getElementById('pending-changes-bar');
        
        // Bind event handlers
        document.getElementById('save-changes-btn').addEventListener('click', () => this.saveAllChanges());
        document.getElementById('discard-changes-btn').addEventListener('click', () => this.clearPendingChanges());
    }
    
    /**
     * Update the pending bar visibility and count
     */
    updatePendingBar() {
        const count = this.pendingChanges.size;
        const countElement = document.getElementById('pending-count');
        
        if (count > 0) {
            this.pendingBar.classList.remove('d-none');
            countElement.textContent = count;
            
            // Add margin to body to account for fixed bar
            document.body.style.marginBottom = '60px';
        } else {
            this.pendingBar.classList.add('d-none');
            document.body.style.marginBottom = '0';
        }
        
        // Update feather icons
        if (typeof feather !== 'undefined') {
            feather.replace();
        }
    }
    
    /**
     * Add visual indicator to show item has pending changes
     */
    addPendingIndicator(itemId) {
        const qtyInput = document.getElementById(`qty-${itemId}`);
        const itemRow = document.querySelector(`input[value="${itemId}"]`)?.closest('tr');
        
        if (qtyInput) {
            qtyInput.classList.add('pending-indicator');
        }
        if (itemRow) {
            itemRow.classList.add('table-row-pending');
        }
    }
    
    /**
     * Remove visual indicator
     */
    removePendingIndicator(itemId) {
        const qtyInput = document.getElementById(`qty-${itemId}`);
        const itemRow = document.querySelector(`input[value="${itemId}"]`)?.closest('tr');
        
        if (qtyInput) {
            qtyInput.classList.remove('pending-indicator');
        }
        if (itemRow) {
            itemRow.classList.remove('table-row-pending');
        }
    }
    
    /**
     * Update group totals considering pending changes
     */
    updateGroupTotalsWithPending() {
        // Find all group total elements
        const groupTotalElements = document.querySelectorAll('[data-group-total]');
        
        groupTotalElements.forEach(totalElement => {
            const groupCard = totalElement.closest('.card');
            if (!groupCard) return;
            
            const groupItems = groupCard.querySelectorAll('.qty-input');
            let total = 0;
            
            groupItems.forEach(input => {
                const itemId = input.id.replace('qty-', '');
                let quantity = parseInt(input.value) || 0;
                
                // Use pending quantity if available
                if (this.pendingChanges.has(itemId)) {
                    quantity = this.pendingChanges.get(itemId).new_quantity;
                }
                
                total += quantity;
            });
            
            totalElement.textContent = total;
        });
    }
    
    /**
     * Local update of quantity badge without server call
     */
    updateQuantityBadgeLocal(itemId, newQuantity, badgeClass = null) {
        const badge = document.querySelector(`.qty-badge-${itemId}`);
        if (badge) {
            // Determine badge class if not provided
            if (!badgeClass) {
                if (newQuantity < 10) {
                    badgeClass = 'bg-warning text-dark';
                } else if (newQuantity > 50) {
                    badgeClass = 'bg-success';
                } else {
                    badgeClass = 'bg-secondary';
                }
            }
            
            badge.className = `badge qty-badge-${itemId} ${badgeClass}`;
            badge.style.fontSize = '0.7em';
            
            let badgeText = newQuantity;
            if (newQuantity < 10) {
                badgeText += ' - Low';
            }
            badge.textContent = badgeText;
        }
    }
    
    /**
     * Local update of low stock indicators
     */
    updateLowStockIndicatorsLocal(itemId, needsReorder) {
        const itemRow = document.querySelector(`input[value="${itemId}"]`)?.closest('tr');
        if (!itemRow) return;
        
        // Update row background color
        if (needsReorder) {
            itemRow.classList.add('table-warning');
        } else {
            itemRow.classList.remove('table-warning');
        }
        
        // Update low stock badges in the name column
        const nameCell = itemRow.querySelector('td:nth-child(3)'); // Adjust index as needed
        if (nameCell) {
            const existingBadge = nameCell.querySelector('.badge.bg-warning');
            if (needsReorder && !existingBadge) {
                // Add low stock badge
                const badge = document.createElement('span');
                badge.className = 'badge bg-warning text-dark ms-1';
                badge.textContent = 'Low';
                nameCell.appendChild(badge);
            } else if (!needsReorder && existingBadge) {
                // Remove low stock badge
                existingBadge.remove();
            }
        }
    }
    
    /**
     * Save all pending changes via batch API
     */
    async saveAllChanges() {
        if (this.isSaving || this.pendingChanges.size === 0) return;
        
        this.isSaving = true;
        const saveBtn = document.getElementById('save-changes-btn');
        const originalContent = saveBtn.innerHTML;
        
        try {
            saveBtn.innerHTML = '<i data-feather="loader" class="me-1"></i>Saving...';
            saveBtn.disabled = true;
            
            const csrfToken = getCSRFToken();
            if (!csrfToken) {
                throw new Error('CSRF token not found');
            }
            
            const updates = this.getPendingChangesForAPI();
            
            // Auto-stringified by api() helper
            const data = await api('/batch_update_stock', {
                method: 'POST',
                headers: {
                    'X-CSRF-Token': csrfToken
                },
                body: {
                    updates: updates,
                    csrf_token: csrfToken
                }
            });
            
            // Apply changes to UI
            this.applyChanges(data.updated_items);
            
            // Show success message
            let message = data.message;
            if (data.warning) {
                message += ` (${data.warning})`;
            }
            
            if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
                window.inventoryDashboard.showNotification(message, 'success', 3000);
            } else {
                alert(message);
            }
            
        } catch (error) {
            showError('Failed to save changes', error);
        } finally {
            this.isSaving = false;
            saveBtn.innerHTML = originalContent;
            saveBtn.disabled = false;
            
            // Update feather icons
            if (typeof feather !== 'undefined') {
                feather.replace();
            }
        }
    }
    
    /**
     * Set up navigation guard to warn about unsaved changes
     */
    initializeNavigationGuard() {
        // Warn before page unload
        window.addEventListener('beforeunload', (e) => {
            if (this.pendingChanges.size > 0) {
                const message = `You have ${this.pendingChanges.size} unsaved changes. Are you sure you want to leave?`;
                e.preventDefault();
                e.returnValue = message;
                return message;
            }
        });
        
        // Warn before navigation clicks (optional enhancement)
        document.addEventListener('click', (e) => {
            const link = e.target.closest('a');
            if (link && link.href && this.pendingChanges.size > 0) {
                // Check if it's an external navigation link (not a #hash or javascript:void)
                if (!link.href.includes('#') && !link.href.includes('javascript:')) {
                    const confirmed = confirm(`You have ${this.pendingChanges.size} unsaved changes. Are you sure you want to leave this page?`);
                    if (!confirmed) {
                        e.preventDefault();
                        return false;
                    }
                }
            }
        });
    }
}

// Global instance
let pendingChangesManager;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Initialize pending changes manager after a brief delay to ensure DOM is fully loaded
    setTimeout(() => {
        pendingChangesManager = new PendingChangesManager();
    }, 100);
});

// =================== INLINE STOCK ADJUSTMENT FUNCTIONS ===================

/**
 * Get CSRF token for secure requests
 */
function getCSRFToken() {
    const metaToken = document.querySelector('meta[name="csrf-token"]');
    if (metaToken) {
        return metaToken.getAttribute('content');
    }
    
    // Fallback: look for it in a global variable or form
    if (window.csrf_token) {
        return window.csrf_token;
    }
    
    const csrfInput = document.querySelector('input[name="csrf_token"]');
    if (csrfInput) {
        return csrfInput.value;
    }
    
    return null;
}

/**
 * Adjust quantity by a specific amount (+1 or -1)
 */
function adjustQuantity(itemId, change) {
    const qtyInput = document.getElementById(`qty-${itemId}`);
    if (!qtyInput) {
        console.error(`Quantity input not found for item ${itemId}`);
        return;
    }
    
    const currentQty = parseInt(qtyInput.value) || 0;
    const newQty = Math.max(0, currentQty + change); // Ensure not negative
    
    qtyInput.value = newQty;
    updateQuantityLocal(itemId);
}

/**
 * Update quantity locally (no server call - uses pending changes system)
 */
function updateQuantityLocal(itemId) {
    const qtyInput = document.getElementById(`qty-${itemId}`);
    if (!qtyInput) {
        console.error(`Quantity input not found for item ${itemId}`);
        return;
    }
    
    const newQuantity = parseInt(qtyInput.value);
    if (isNaN(newQuantity) || newQuantity < 0) {
        // Show error feedback and revert to original or last valid value
        if (pendingChangesManager) {
            const originalQty = pendingChangesManager.originalQuantities.get(itemId) || 0;
            qtyInput.value = originalQty;
            if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
                window.inventoryDashboard.showNotification('Quantity must be a valid number', 'danger', 2000);
            }
        }
        return;
    }
    
    // Use pending changes manager to track the change
    if (pendingChangesManager) {
        pendingChangesManager.addPendingChange(itemId, newQuantity);
        
        // Update local UI elements immediately
        updateQuantityBadgeDisplay(itemId, newQuantity);
        
        // Update low stock indicators locally
        const needsReorder = newQuantity < 10; // Simple threshold, could be made dynamic
        updateLowStockIndicators(itemId, needsReorder);
    } else {
        // Fallback if pendingChangesManager not available
        console.log('PendingChangesManager not available, updating display directly');
        updateQuantityBadgeDisplay(itemId, newQuantity);
    }
}

/**
 * Legacy function for backward compatibility - redirects to local update
 */
async function updateQuantity(itemId) {
    updateQuantityLocal(itemId);
}

/**
 * Handle Enter key press in quantity input
 */
function handleQuantityKeypress(event, itemId) {
    if (event.key === 'Enter') {
        event.preventDefault();
        updateQuantityLocal(itemId);
    }
}

/**
 * Update quantity badge display with enhanced colors
 */
function updateQuantityBadgeDisplay(itemId, newQuantity) {
    const badge = document.querySelector(`.qty-badge-${itemId}`);
    if (badge) {
        // Update badge text
        badge.textContent = newQuantity + (newQuantity < 10 ? ' - Low' : '');
        
        // Enhanced color coding with more vibrant and distinct colors
        if (newQuantity === 0) {
            badge.className = `badge qty-badge-${itemId} bg-danger text-white`;
            badge.style.backgroundColor = '#dc3545';
        } else if (newQuantity < 5) {
            badge.className = `badge qty-badge-${itemId} bg-warning text-dark`;
            badge.style.backgroundColor = '#ffc107';
            badge.style.color = '#000';
        } else if (newQuantity < 10) {
            badge.className = `badge qty-badge-${itemId} bg-warning text-dark`;
            badge.style.backgroundColor = '#fd7e14';
            badge.style.color = '#000';
        } else if (newQuantity < 20) {
            badge.className = `badge qty-badge-${itemId} bg-info text-white`;
            badge.style.backgroundColor = '#17a2b8';
        } else if (newQuantity < 50) {
            badge.className = `badge qty-badge-${itemId} bg-primary text-white`;
            badge.style.backgroundColor = '#007bff';
        } else {
            badge.className = `badge qty-badge-${itemId} bg-success text-white`;
            badge.style.backgroundColor = '#28a745';
        }
        
        badge.style.fontSize = '0.85em';
        badge.style.fontWeight = 'bold';
        badge.style.padding = '0.35em 0.6em';
        badge.style.borderRadius = '0.375rem';
        badge.style.textShadow = '0 1px 2px rgba(0,0,0,0.1)';
        
        // Add a pulse animation to show the change
        badge.style.transform = 'scale(1.15)';
        badge.style.transition = 'all 0.2s ease';
        setTimeout(() => {
            badge.style.transform = 'scale(1)';
        }, 250);
    }
}

/**
 * Update low stock indicators
 */
function updateLowStockIndicators(itemId, needsReorder) {
    const row = document.getElementById(`item-row-${itemId}`);
    if (row) {
        if (needsReorder) {
            row.style.border = '3px solid #ff6b6b';
            row.style.borderRadius = '10px';
            row.style.backgroundColor = '#fff8e1';
            row.style.transition = 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
            row.style.boxShadow = '0 4px 12px rgba(255, 107, 107, 0.4)';
        } else {
            row.style.border = '';
            row.style.borderRadius = '';
            row.style.backgroundColor = '';
            row.style.boxShadow = '';
        }
    }
}

/**
 * Handle manual input changes in quantity fields
 */
function handleQuantityInputChange(event, itemId) {
    // Small delay to allow input to complete, then update locally
    clearTimeout(window.quantityInputTimeout);
    window.quantityInputTimeout = setTimeout(() => {
        updateQuantityLocal(itemId);
    }, 300); // 300ms delay to avoid rapid updates while typing
}

/**
 * Update the quantity badge display
 */
function updateQuantityBadge(itemId, newQuantity, badgeClass) {
    const badge = document.querySelector(`.qty-badge-${itemId}`);
    if (badge) {
        badge.className = `badge qty-badge-${itemId} ${badgeClass}`;
        badge.style.fontSize = '0.7em';
        
        let badgeText = newQuantity;
        if (newQuantity < 10) {
            badgeText += ' - Low';
        }
        badge.textContent = badgeText;
    }
}

/**
 * Show temporary feedback for stock updates
 */
function showStockUpdateFeedback(itemId, message, type) {
    // Try to use the existing notification system if available
    if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
        const alertType = type === 'error' ? 'danger' : type === 'success' ? 'success' : 'info';
        window.inventoryDashboard.showNotification(message, alertType, 2000);
        return;
    }
    
    // Fallback: create inline feedback
    const qtyInput = document.getElementById(`qty-${itemId}`);
    if (!qtyInput) return;
    
    // Remove any existing feedback
    const existingFeedback = qtyInput.parentNode.querySelector('.qty-feedback');
    if (existingFeedback) {
        existingFeedback.remove();
    }
    
    // Create new feedback element
    const feedback = document.createElement('div');
    feedback.className = `qty-feedback text-${type === 'error' ? 'danger' : type === 'success' ? 'success' : 'info'}`;
    feedback.style.fontSize = '0.7em';
    feedback.style.position = 'absolute';
    feedback.style.top = '100%';
    feedback.style.left = '0';
    feedback.style.zIndex = '10';
    feedback.style.background = 'white';
    feedback.style.padding = '2px 4px';
    feedback.style.border = '1px solid #ddd';
    feedback.style.borderRadius = '3px';
    feedback.style.boxShadow = '0 2px 4px rgba(0,0,0,0.1)';
    feedback.textContent = message;
    
    // Make parent relative for positioning
    qtyInput.parentNode.style.position = 'relative';
    qtyInput.parentNode.appendChild(feedback);
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
        if (feedback.parentNode) {
            feedback.remove();
        }
    }, 3000);
}

/**
 * Update group totals in grouped view
 */
function updateGroupTotals(itemId) {
    // Find which group this item belongs to and update totals
    const itemRow = document.querySelector(`input[value="${itemId}"]`)?.closest('tr');
    if (!itemRow) return;
    
    // Find the group container
    const groupCard = itemRow.closest('.card');
    if (!groupCard) return;
    
    // Find the group total element
    const totalQtyElement = groupCard.querySelector('[data-group-total]');
    if (!totalQtyElement) return;
    
    // Calculate new total from all items in this group
    const groupItems = groupCard.querySelectorAll('.qty-input');
    let total = 0;
    groupItems.forEach(input => {
        total += parseInt(input.value) || 0;
    });
    
    // Update the display
    totalQtyElement.textContent = total;
}

/**
 * Update low stock indicators
 */
function updateLowStockIndicators(itemId, needsReorder) {
    const itemRow = document.querySelector(`input[value="${itemId}"]`)?.closest('tr');
    if (!itemRow) return;
    
    // Update row background color
    if (needsReorder) {
        itemRow.classList.add('table-warning');
    } else {
        itemRow.classList.remove('table-warning');
    }
    
    // Update low stock badges in the name column
    const nameCell = itemRow.querySelector('td:nth-child(3)'); // Adjust index as needed
    if (nameCell) {
        const existingBadge = nameCell.querySelector('.badge.bg-warning');
        if (needsReorder && !existingBadge) {
            // Add low stock badge
            const badge = document.createElement('span');
            badge.className = 'badge bg-warning text-dark ms-1';
            badge.textContent = 'Low';
            nameCell.appendChild(badge);
        } else if (!needsReorder && existingBadge) {
            // Remove low stock badge
            existingBadge.remove();
        }
    }
}

// =================== MISSING TEMPLATE FUNCTIONS ===================

/**
 * Push selected items to connected stores
 */
async function pushSelectedItems() {
    const selectedCheckboxes = document.querySelectorAll('.item-checkbox:checked');
    if (selectedCheckboxes.length === 0) {
        if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
            window.inventoryDashboard.showNotification('No items selected', 'warning', 3000);
        } else {
            alert('No items selected');
        }
        return;
    }
    
    const selectedIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.value));
    
    const pushBtn = document.getElementById('pushSelectedBtn');
    const originalContent = pushBtn.innerHTML;
    
    try {
        pushBtn.innerHTML = '<i data-feather="loader" class="me-1"></i>Pushing...';
        pushBtn.disabled = true;
        
        const csrfToken = getCSRFToken();
        // Call the correct bulk push endpoint
        const data = await api('/push_stock_bulk', {
            method: 'POST',
            headers: {
                'X-CSRF-Token': csrfToken
            },
            body: {
                item_ids: selectedIds,
                csrf_token: csrfToken
            }
        });
        
        if (data.success) {
            if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
                window.inventoryDashboard.showNotification(data.message || 'Selected items pushed successfully', 'success', 4000);
            } else {
                alert(data.message || 'Selected items pushed successfully');
            }
            // Clear selections
            selectedCheckboxes.forEach(cb => cb.checked = false);
            updateBulkButtons();
        } else {
            throw new Error(data.error || 'Failed to push items');
        }
        
    } catch (error) {
        console.error('Error pushing selected items:', error);
        const errorMsg = `Error pushing items: ${error.message}`;
        if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
            window.inventoryDashboard.showNotification(errorMsg, 'danger', 5000);
        } else {
            alert(errorMsg);
        }
    } finally {
        pushBtn.innerHTML = originalContent;
        pushBtn.disabled = false;
        
        if (typeof feather !== 'undefined') {
            feather.replace();
        }
    }
}

/**
 * Push all items to connected stores
 */
async function pushAllItems() {
    if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
        window.inventoryDashboard.showNotification(
            'Legacy Push All is retired. Use governed bulk queue actions from Admin Product Linking / Jobs.',
            'warning',
            5000
        );
    } else {
        alert('Legacy Push All is retired. Use governed bulk queue actions from Admin Product Linking / Jobs.');
    }
    return;
}

/**
 * Push individual item to connected stores
 */
async function pushIndividualItem(itemId, itemSku) {
    const button = event.target.closest('button');
    const originalContent = button.innerHTML;
    
    try {
        button.innerHTML = '<i data-feather="loader"></i>';
        button.disabled = true;
        
        const csrfToken = getCSRFToken();
        // Auto-stringified and Content-Type set by api() helper
        const data = await api('/api/push-individual', {
            method: 'POST',
            headers: {
                'X-CSRF-Token': csrfToken
            },
            body: {
                item_id: itemId,
                csrf_token: csrfToken
            }
        });
        
        if (data.success) {
            if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
                window.inventoryDashboard.showNotification(`${itemSku}: ${data.message}`, 'success', 3000);
            }
            
            // Temporary success indicator
            button.innerHTML = '<i data-feather="check"></i>';
            button.classList.remove('btn-outline-success');
            button.classList.add('btn-success');
            
            setTimeout(() => {
                button.innerHTML = originalContent;
                button.classList.remove('btn-success');
                button.classList.add('btn-outline-success');
                button.disabled = false;
                if (typeof feather !== 'undefined') {
                    feather.replace();
                }
            }, 2000);
        } else {
            throw new Error(data.error || 'Failed to push item');
        }
        
    } catch (error) {
        console.error('Error pushing individual item:', error);
        const errorMsg = `Error pushing ${itemSku}: ${error.message}`;
        if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
            window.inventoryDashboard.showNotification(errorMsg, 'danger', 4000);
        }
        
        // Temporary error indicator
        button.innerHTML = '<i data-feather="x"></i>';
        button.classList.remove('btn-outline-success');
        button.classList.add('btn-danger');
        
        setTimeout(() => {
            button.innerHTML = originalContent;
            button.classList.remove('btn-danger');
            button.classList.add('btn-outline-success');
            button.disabled = false;
            if (typeof feather !== 'undefined') {
                feather.replace();
            }
        }, 2000);
    }
}

/**
 * Confirm and delete an item
 */
function confirmDelete(itemId, itemName) {
    const confirmed = confirm(`Are you sure you want to delete "${itemName}"? This action cannot be undone.`);
    if (!confirmed) return;
    
    // Redirect to delete endpoint (assuming it exists)
    window.location.href = `/delete-item/${itemId}`;
}

/**
 * Release item from group
 */
async function releaseFromGroup(button, itemId, itemSku) {
    const confirmed = confirm(`Remove "${itemSku}" from its current group?`);
    if (!confirmed) return;
    
    const originalContent = button.innerHTML;
    
    try {
        button.innerHTML = '<i data-feather="loader"></i>';
        button.disabled = true;
        
        const csrfToken = getCSRFToken();
        // Call with item_id in the URL path (not body)
        const data = await api(`/release_from_group/${itemId}`, {
            method: 'POST',
            headers: {
                'X-CSRF-Token': csrfToken
            },
            body: {
                csrf_token: csrfToken
            }
        });
        
        if (data.success) {
            if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
                window.inventoryDashboard.showNotification(`${itemSku} released from group`, 'success', 3000);
            }
            
            // Reload page to show updated grouping
            setTimeout(() => {
                window.location.reload();
            }, 1000);
        } else {
            throw new Error(data.error || 'Failed to release item from group');
        }
        
    } catch (error) {
        console.error('Error releasing from group:', error);
        const errorMsg = `Error releasing ${itemSku}: ${error.message}`;
        if (window.inventoryDashboard && window.inventoryDashboard.showNotification) {
            window.inventoryDashboard.showNotification(errorMsg, 'danger', 4000);
        } else {
            alert(errorMsg);
        }
        
        button.innerHTML = originalContent;
        button.disabled = false;
        if (typeof feather !== 'undefined') {
            feather.replace();
        }
    }
}

/**
 * Filter items by group
 */
function filterByGroup(groupId) {
    const currentUrl = new URL(window.location.href);
    if (groupId) {
        currentUrl.searchParams.set('group', groupId);
    } else {
        currentUrl.searchParams.delete('group');
    }
    window.location.href = currentUrl.toString();
}

/**
 * Perform search
 */
function performSearch() {
    const searchInput = document.getElementById('searchInput');
    const searchTerm = searchInput.value.trim();
    
    const currentUrl = new URL(window.location.href);
    if (searchTerm) {
        currentUrl.searchParams.set('search', searchTerm);
    } else {
        currentUrl.searchParams.delete('search');
    }
    window.location.href = currentUrl.toString();
}

/**
 * Clear search
 */
function clearSearch() {
    const currentUrl = new URL(window.location.href);
    currentUrl.searchParams.delete('search');
    window.location.href = currentUrl.toString();
}

/**
 * Handle search keyup (Enter key)
 */
function handleSearchKeyup(event) {
    if (event.key === 'Enter') {
        performSearch();
    }
}

/**
 * Toggle select all checkboxes for a section
 */
function toggleSelectAll(checkbox, type) {
    const isChecked = checkbox.checked;
    let targetCheckboxes;
    
    if (type === 'flat') {
        targetCheckboxes = document.querySelectorAll('.flat-checkbox');
    } else if (type === 'ungrouped') {
        targetCheckboxes = document.querySelectorAll('.ungrouped-checkbox');
    } else if (type.startsWith('group')) {
        const groupClass = `.${type}-checkbox`;
        targetCheckboxes = document.querySelectorAll(groupClass);
    }
    
    if (targetCheckboxes) {
        targetCheckboxes.forEach(cb => {
            cb.checked = isChecked;
        });
    }
    
    updateBulkButtons();
}

/**
 * Update bulk action buttons based on selections
 */
function updateBulkButtons() {
    const selectedCheckboxes = document.querySelectorAll('.item-checkbox:checked');
    const pushSelectedBtn = document.getElementById('pushSelectedBtn');
    
    if (pushSelectedBtn) {
        if (selectedCheckboxes.length > 0) {
            pushSelectedBtn.disabled = false;
            pushSelectedBtn.innerHTML = `<i data-feather="upload" class="me-1"></i>Push Selected (${selectedCheckboxes.length})`;
        } else {
            pushSelectedBtn.disabled = true;
            pushSelectedBtn.innerHTML = '<i data-feather="upload" class="me-1"></i>Push Selected';
        }
        
        if (typeof feather !== 'undefined') {
            feather.replace();
        }
    }
}
