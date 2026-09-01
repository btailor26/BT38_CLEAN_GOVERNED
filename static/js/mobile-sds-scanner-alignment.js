/* Extend the existing BT38 MobileScanner with SDS QR support.
 * Product/SKU/carton scanning remains on the original MobileScanner path.
 */
(() => {
  if (typeof MobileScanner === 'undefined') return;

  const originalHandleBarcodeScan = MobileScanner.prototype.handleBarcodeScan;

  MobileScanner.prototype.startBarcodeScanning = function () {
    if (!('BarcodeDetector' in window)) {
      this.showToast('Barcode API not supported. Use manual entry.', 'warning');
      return;
    }

    const detector = new BarcodeDetector({
      formats: ['ean_13', 'ean_8', 'code_128', 'code_39', 'upc_a', 'upc_e', 'qr_code']
    });
    const video = document.getElementById('camera-feed');

    const scan = async () => {
      if (video && video.readyState === video.HAVE_ENOUGH_DATA) {
        try {
          const barcodes = await detector.detect(video);
          if (barcodes.length > 0) {
            this.handleBarcodeScan(barcodes[0].rawValue);
          }
        } catch (err) {
          console.error('Barcode detection error:', err);
        }
      }
      requestAnimationFrame(scan);
    };
    scan();
  };

  MobileScanner.prototype.handleBarcodeScan = async function (barcode) {
    const value = String(barcode || '').trim().toUpperCase();
    if (/^SDS-\d{10}$/.test(value)) {
      await this.handleSdsScan(value);
      return;
    }
    return originalHandleBarcodeScan.call(this, barcode);
  };

  MobileScanner.prototype.handleSdsScan = async function (reference) {
    if (this._sdsScanBusy || this._lastSdsReference === reference) return;
    this._sdsScanBusy = true;
    try {
      const lookupResponse = await fetch(`/api/mobile/sds/${encodeURIComponent(reference)}`);
      const parcel = await lookupResponse.json();
      if (!lookupResponse.ok || !parcel.success) {
        this.showToast(parcel.message || 'SDS parcel not found', 'error');
        return;
      }

      if (!parcel.can_advance || !parcel.next_event) {
        this._lastSdsReference = reference;
        this.showToast(parcel.delivered ? 'SDS parcel already delivered' : `SDS parcel status: ${parcel.status}`, 'warning');
        return;
      }

      const label = parcel.next_event === 'handover'
        ? 'Handover'
        : parcel.next_event === 'in_transit'
          ? 'In transit'
          : 'Delivered';
      if (!window.confirm(`${reference}\nRecord SDS event: ${label}?`)) {
        this.showToast('SDS scan cancelled', 'warning');
        return;
      }

      const response = await fetch(`/fbm/shipments/${parcel.shipment_id}/sds/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sds_reference: reference,
          event_type: parcel.next_event,
          confirm_scan: `SCAN_${parcel.next_event.toUpperCase()}`
        })
      });
      const result = await response.json();
      if (!response.ok || !result.success) {
        this.showToast(result.message || 'SDS scan failed', 'error');
        return;
      }

      this._lastSdsReference = reference;
      this.showToast(`${reference}: ${label} recorded`, 'success');
      setTimeout(() => {
        if (this._lastSdsReference === reference) this._lastSdsReference = null;
      }, 2500);
    } catch (err) {
      console.error('SDS scan failed:', err);
      this.showToast('SDS scan failed', 'error');
    } finally {
      this._sdsScanBusy = false;
    }
  };
})();
