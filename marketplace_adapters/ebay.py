"""BT38 governed eBay live adapter."""

from __future__ import annotations

import json
import requests
from typing import Any, Mapping

from marketplace_adapters.base import GovernedMarketplaceAdapter


class EbayAdapter(GovernedMarketplaceAdapter):
    marketplace = "ebay"
    adapter_name = "ebay"

    def execute(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        store = payload.get("_governed_store") or payload.get("store")
        listing = payload.get("_governed_listing") or payload.get("listing")

        if not store:
            return self.blocked_result(
                action=action,
                payload=payload,
                reason="Missing store for eBay execution.",
            )

        raw = getattr(store, "api_key", None)

        creds = None

        if isinstance(raw, str):
            try:
                creds = json.loads(raw)
            except Exception:
                creds = None
        elif isinstance(raw, dict):
            creds = raw

        if not creds:
            return self.blocked_result(
                action=action,
                payload=payload,
                reason="Missing eBay credentials.",
            )

        token = (
            creds.get("access_token")
            or creds.get("oauth_token")
            or creds.get("token")
        )

        def _token_expires_soon(value: Any) -> bool:
            if not value:
                return True
            try:
                from datetime import datetime, timedelta

                expires_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if expires_at.tzinfo is not None:
                    expires_at = expires_at.replace(tzinfo=None)
                return expires_at <= datetime.utcnow() + timedelta(minutes=10)
            except Exception:
                return True

        if _token_expires_soon(creds.get("access_token_expires_at")):
            import base64
            import os
            from datetime import datetime, timedelta

            from app import db

            refresh_token = creds.get("refresh_token")
            client_id = os.getenv("EBAY_CLIENT_ID") or creds.get("app_id")
            client_secret = os.getenv("EBAY_CLIENT_SECRET") or creds.get("cert_id")

            if not refresh_token or not client_id or not client_secret:
                return self.blocked_result(
                    action=action,
                    payload=payload,
                    reason="Missing eBay refresh credentials.",
                )

            basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            from services.governed_ebay_oauth_scopes import governed_ebay_refresh_scopes

            scopes = governed_ebay_refresh_scopes(creds)

            refresh_response = requests.post(
                "https://api.ebay.com/identity/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": scopes,
                },
                timeout=30,
            )

            try:
                refresh_payload = refresh_response.json()
            except Exception:
                refresh_payload = {"raw": refresh_response.text}

            if refresh_response.status_code >= 300 or not refresh_payload.get("access_token"):
                return {
                    "ok": False,
                    "success": False,
                    "marketplace": "ebay",
                    "action": action,
                    "status_code": refresh_response.status_code,
                    "reason": "eBay access token refresh failed before push.",
                    "refresh_response": refresh_payload,
                    "live_write": False,
                }

            now = datetime.utcnow()
            creds.update({
                "access_token": refresh_payload.get("access_token"),
                "token_type": refresh_payload.get("token_type"),
                "access_token_expires_at": (
                    now + timedelta(seconds=int(refresh_payload.get("expires_in", 7200)))
                ).isoformat(),
                "oauth_source": "governed_ebay_adapter_refresh_before_push",
                "oauth_requested_scope": scopes,
                "oauth_granted_scope": (
                    refresh_payload.get("scope")
                    or creds.get("oauth_granted_scope")
                ),
                "refreshed_at": now.isoformat(),
                "sandbox": False,
            })

            store.api_key = json.dumps(creds)
            store.is_active = True
            store.store_mode = "live"
            db.session.commit()

            token = creds.get("access_token")

        if not token:
            return self.blocked_result(
                action=action,
                payload=payload,
                reason="Missing eBay access token.",
            )

        item_id = (
            payload.get("external_listing_id")
            or getattr(listing, "external_listing_id", None)
        )

        if not item_id:
            return self.blocked_result(
                action=action,
                payload=payload,
                reason="Missing eBay item id.",
            )

        quantity = payload.get("quantity")

        if quantity is None and listing:
            stock = getattr(listing, "warehouse_stock", None)
            if stock:
                quantity = getattr(stock, "quantity", 0)

        sku = str(
            payload.get("sku")
            or getattr(listing, "external_sku", None)
            or ""
        ).strip()

        # Historical BT38 imports used the eBay ItemID as a fallback seller SKU
        # for single listings that do not actually have a seller-defined SKU.
        # eBay ReviseInventoryStatus must identify those ItemID-tracked listings
        # by ItemID alone. Variation children still require their true SKU.
        is_variation = bool(
            getattr(listing, "parent_item_id", None)
            or getattr(listing, "external_parent_id", None)
            or getattr(listing, "variation_sku_map", None)
        )
        itemid_tracked_single = bool(
            not is_variation
            and (
                not str(getattr(listing, "external_sku", None) or "").strip()
                or sku == str(item_id).strip()
            )
        )
        request_sku = "" if itemid_tracked_single else sku
        sku_xml = f"\n    <SKU>{request_sku}</SKU>" if request_sku else ""

        xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseInventoryStatusRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{token}</eBayAuthToken>
  </RequesterCredentials>
  <ErrorLanguage>en_GB</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
  <InventoryStatus>
    <ItemID>{item_id}</ItemID>{sku_xml}
    <Quantity>{int(quantity or 0)}</Quantity>
  </InventoryStatus>
</ReviseInventoryStatusRequest>"""

        url = "https://api.ebay.com/ws/api.dll"

        headers = {
            "Content-Type": "text/xml",
            "X-EBAY-API-CALL-NAME": "ReviseInventoryStatus",
            "X-EBAY-API-SITEID": str(creds.get("site_id") or creds.get("siteid") or "3"),
            "X-EBAY-API-COMPATIBILITY-LEVEL": str(creds.get("compatibility_level") or "1193"),
        }

        response = requests.post(
            url,
            headers=headers,
            data=xml_body.encode("utf-8"),
            timeout=30,
        )

        response_text = response.text or ""

        ack = "UNKNOWN"
        if "<Ack>Success</Ack>" in response_text:
            ack = "Success"
        elif "<Ack>Warning</Ack>" in response_text:
            ack = "Warning"
        elif "<Ack>Failure</Ack>" in response_text:
            ack = "Failure"

        short_error = None
        if "<ShortMessage>" in response_text:
            try:
                short_error = response_text.split("<ShortMessage>", 1)[1].split("</ShortMessage>", 1)[0].strip()
            except Exception:
                short_error = None

        long_error = None
        if "<LongMessage>" in response_text:
            try:
                long_error = response_text.split("<LongMessage>", 1)[1].split("</LongMessage>", 1)[0].strip()
            except Exception:
                long_error = None

        ack_success = ack in ("Success", "Warning")
        write_acknowledged = response.status_code < 300 and ack_success
        observed_quantity = None
        readback_verified = False
        readback_error = None

        if write_acknowledged:
            try:
                from datetime import datetime
                from app import db
                from services.governed_ebay_inventory_import import (
                    _get_item_detail,
                    _xml_text,
                )

                detail = _get_item_detail(creds, str(item_id))
                if detail is None:
                    readback_error = "eBay exact GetItem returned no Item."
                else:
                    variations = list(detail.findall(".//{*}Variations/{*}Variation"))
                    if variations:
                        matched = None
                        for variation in variations:
                            if _xml_text(variation, "{*}SKU") == str(request_sku):
                                matched = variation
                                break
                        if matched is None:
                            readback_error = "eBay exact GetItem did not return the pushed variation SKU."
                        else:
                            listed_quantity = int(_xml_text(matched, "{*}Quantity", "0") or 0)
                            sold_quantity = int(
                                _xml_text(
                                    matched,
                                    "{*}SellingStatus/{*}QuantitySold",
                                    "0",
                                )
                                or 0
                            )
                            observed_quantity = max(0, listed_quantity - sold_quantity)
                    else:
                        # GetItem returns Item.Quantity as lifetime total
                        # (available + sold), just like Variation.Quantity.
                        listed_quantity = int(
                            _xml_text(detail, "{*}Quantity", "0") or 0
                        )
                        sold_quantity = int(
                            _xml_text(
                                detail,
                                "{*}SellingStatus/{*}QuantitySold",
                                "0",
                            )
                            or 0
                        )
                        observed_quantity = max(
                            0,
                            listed_quantity - sold_quantity,
                        )

                    if observed_quantity is not None:
                        readback_verified = (
                            int(observed_quantity) == int(quantity or 0)
                        )
                        if listing is not None:
                            listing.last_marketplace_qty = int(observed_quantity)
                            listing.last_synced_at = datetime.utcnow()
                            db.session.commit()

                    if observed_quantity is not None and not readback_verified:
                        readback_error = (
                            "eBay exact read-back mismatch: "
                            f"observed={int(observed_quantity)} "
                            f"expected={int(quantity or 0)}"
                        )
            except Exception as exc:
                readback_error = f"eBay exact read-back failed: {exc}"

        ok = bool(write_acknowledged and readback_verified)

        response_summary = (
            f"Ack={ack}; ItemID={item_id}; SKU={request_sku or '[ItemID-tracked]'}; "
            f"Quantity={int(quantity or 0)}"
        )
        if observed_quantity is not None:
            response_summary += f"; ObservedQuantity={int(observed_quantity)}"
        if short_error:
            response_summary += f"; ShortError={short_error}"
        if long_error:
            response_summary += f"; LongError={long_error}"
        if readback_error:
            response_summary += f"; Readback={readback_error}"

        if not write_acknowledged:
            reason = short_error or long_error or "eBay inventory write was not acknowledged."
        elif not readback_verified:
            reason = readback_error or "eBay inventory write was acknowledged but exact read-back was not verified."
        else:
            reason = "eBay inventory write and exact read-back verified."

        return {
            "ok": ok,
            "success": ok,
            "marketplace": "ebay",
            "action": action,
            "status_code": response.status_code,
            "ack": ack,
            "short_error": short_error,
            "long_error": long_error,
            "reason": reason,
            "response_summary": response_summary,
            "response_text": response_text[:4000],
            "live_write": True,
            "write_acknowledged": write_acknowledged,
            "readback_verified": readback_verified,
            "observed_quantity": observed_quantity,
            "readback_error": readback_error,
            "ebay_call": "ReviseInventoryStatus",
            "readback_call": "GetItem" if write_acknowledged else None,
            "external_listing_id": item_id,
            "sku": request_sku,
            "itemid_tracked_single": itemid_tracked_single,
            "quantity": quantity,
        }