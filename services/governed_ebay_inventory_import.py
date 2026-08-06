"""
BT38 GOVERNED EBAY INVENTORY IMPORT

Purpose:
- Use the existing MarketplaceListing variation-capable DB structure.
- One eBay parent ItemID can create many child rows by external_sku.
- No product-linking changes.
- No warehouse UI rewrite.
"""

from __future__ import annotations

import base64
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import requests

from app import db
from models import Store, MarketplaceListing, Warehouse, WarehouseStock, SyncLog, SystemConfig
from services.runtime_status_writer import set_store_runtime_status


EBAY_TRADING_URL = "https://api.ebay.com/ws/api.dll"
EBAY_COMPAT_LEVEL = "1193"
EBAY_SITE_ID = "3"


def _parse_creds(store: Store) -> dict[str, Any]:
    raw = store.api_key or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _token_expires_soon(value: Any) -> bool:
    if not value:
        return False
    try:
        expires_at = datetime.fromisoformat(str(value))
        return expires_at <= datetime.utcnow() + timedelta(minutes=10)
    except Exception:
        return False


def _refresh_access_token_if_needed(store: Store, creds: dict[str, Any]) -> dict[str, Any]:
    token = creds.get("access_token")
    if token and not _token_expires_soon(creds.get("access_token_expires_at")):
        return creds

    refresh_token = creds.get("refresh_token")
    client_id = os.getenv("EBAY_CLIENT_ID") or creds.get("app_id")
    client_secret = os.getenv("EBAY_CLIENT_SECRET") or creds.get("cert_id")

    if not refresh_token or not client_id or not client_secret:
        return creds

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    scopes = os.getenv("EBAY_SCOPES") or (
        "https://api.ebay.com/oauth/api_scope "
        "https://api.ebay.com/oauth/api_scope/sell.inventory "
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment "
        "https://api.ebay.com/oauth/api_scope/sell.account"
    )

    resp = requests.post(
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

    payload = resp.json() if resp.text else {}
    if resp.status_code >= 300 or not payload.get("access_token"):
        return creds

    creds.update({
        "access_token": payload.get("access_token"),
        "access_token_expires_at": (
            datetime.utcnow() + timedelta(seconds=int(payload.get("expires_in", 7200)))
        ).isoformat(),
        "oauth_source": "governed_ebay_inventory_import_refresh",
    })
    store.api_key = json.dumps(creds)
    db.session.add(store)
    db.session.flush()

    return creds


def _xml_text(node: ET.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    found = node.find(path)
    if found is None or found.text is None:
        return default
    return str(found.text).strip()


def _trading_headers(creds: dict[str, Any], call_name: str) -> dict[str, str]:
    return {
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": str(creds.get("site_id") or creds.get("siteid") or EBAY_SITE_ID),
        "X-EBAY-API-COMPATIBILITY-LEVEL": str(creds.get("compatibility_level") or EBAY_COMPAT_LEVEL),
        "X-EBAY-API-IAF-TOKEN": str(creds.get("access_token") or ""),
        "Content-Type": "text/xml",
    }


def _get_active_items(creds: dict[str, Any], page: int = 1, entries: int = 100) -> list[ET.Element]:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{creds.get("access_token") or ""}</eBayAuthToken>
  </RequesterCredentials>
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>{entries}</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </ActiveList>
  <DetailLevel>ReturnAll</DetailLevel>
</GetMyeBaySellingRequest>"""

    resp = requests.post(
        EBAY_TRADING_URL,
        headers=_trading_headers(creds, "GetMyeBaySelling"),
        data=body.encode("utf-8"),
        timeout=60,
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    return list(root.findall(".//{*}Item"))


def _get_item_detail(creds: dict[str, Any], item_id: str) -> ET.Element | None:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{creds.get("access_token") or ""}</eBayAuthToken>
  </RequesterCredentials>
  <ItemID>{item_id}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
  <IncludeItemSpecifics>true</IncludeItemSpecifics>
  <IncludeWatchCount>true</IncludeWatchCount>
</GetItemRequest>"""

    resp = requests.post(
        EBAY_TRADING_URL,
        headers=_trading_headers(creds, "GetItem"),
        data=body.encode("utf-8"),
        timeout=60,
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    return root.find(".//{*}Item")


def _variation_specifics_json(variation: ET.Element) -> str:
    pairs = {}
    for nvl in variation.findall(".//{*}VariationSpecifics/{*}NameValueList"):
        name = _xml_text(nvl, "{*}Name")
        values = [
            (v.text or "").strip()
            for v in nvl.findall("{*}Value")
            if v is not None and v.text
        ]
        if name:
            pairs[name] = values[0] if len(values) == 1 else values
    return json.dumps(pairs, ensure_ascii=False)


def _default_warehouse() -> Warehouse:
    return Warehouse.get_default()


def _find_or_create_stock(sku: str, title: str) -> WarehouseStock:
    warehouse = _default_warehouse()
    stock = (
        db.session.query(WarehouseStock)
        .filter(
            WarehouseStock.warehouse_id == warehouse.id,
            WarehouseStock.sku == sku,
            WarehouseStock.is_deleted == False,  # noqa: E712
        )
        .first()
    )

    if not stock:
        stock = WarehouseStock(
            warehouse_id=warehouse.id,
            sku=sku,
            product_name=title or sku,
            available_quantity=0,
            is_active=True,
            is_deleted=False,
        )
        db.session.add(stock)
        db.session.flush()

    if title and not stock.product_name:
        stock.product_name = title

    stock.last_sync_at = datetime.utcnow()
    return stock


def _upsert_listing(
    *,
    store: Store,
    stock: WarehouseStock,
    item_id: str,
    sku: str,
    title: str,
    qty: int,
    price: float,
    is_variation_child: bool,
    parent_item_id: str | None,
    variation_sku_map: str | None,
) -> MarketplaceListing:
    # Permanent marketplace identity contract:
    #
    # The eBay Item ID supplied by the API is the stable listing identity.
    # Seller SKU and title are mutable marketplace metadata.
    #
    # Resolve by store + Item ID first. A SKU fallback is permitted only for
    # legacy rows that do not yet contain a marketplace Item ID.
    listing = (
        db.session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.store_id == store.id,
            MarketplaceListing.external_listing_id == item_id,
        )
        .order_by(
            MarketplaceListing.is_active.desc(),
            MarketplaceListing.id.asc(),
        )
        .first()
    )

    if listing is None and sku:
        listing = (
            db.session.query(MarketplaceListing)
            .filter(
                MarketplaceListing.store_id == store.id,
                MarketplaceListing.external_sku == sku,
                db.or_(
                    MarketplaceListing.external_listing_id.is_(None),
                    MarketplaceListing.external_listing_id == "",
                ),
            )
            .order_by(
                MarketplaceListing.is_active.desc(),
                MarketplaceListing.id.asc(),
            )
            .first()
        )

    if not listing:
        listing = MarketplaceListing(
            store_id=store.id,
            warehouse_stock_id=stock.id,
            external_listing_id=item_id,
            external_sku=sku,
            title=title or sku,
            price=price or 0,
            currency="GBP",
            is_active=True,
        )
        db.session.add(listing)

    # Marketplace imports may create an initial relationship, but must never
    # replace a saved Product Linking relationship. Relinking is user-controlled.
    if listing.warehouse_stock_id is None:
        listing.warehouse_stock_id = stock.id

    listing.external_listing_id = item_id
    listing.external_sku = sku
    listing.title = title or listing.title or sku
    listing.price = price or listing.price or 0
    listing.currency = listing.currency or "GBP"
    listing.is_active = True
    listing.last_marketplace_qty = int(qty or 0)
    listing.last_synced_at = datetime.utcnow()

    if is_variation_child:
        listing.parent_item_id = parent_item_id or item_id
        listing.external_parent_id = parent_item_id or item_id
        listing.variation_sku_map = variation_sku_map
    else:
        listing.parent_item_id = None
        listing.external_parent_id = None
        listing.variation_sku_map = None

    return listing


def _import_item(store: Store, creds: dict[str, Any], item: ET.Element) -> dict[str, int]:
    item_id = _xml_text(item, "{*}ItemID")
    if not item_id:
        return {"items": 0, "variations": 0}

    detail = _get_item_detail(creds, item_id) or item

    title = _xml_text(detail, "{*}Title") or f"eBay Item {item_id}"
    parent_sku = _xml_text(detail, "{*}SKU") or item_id
    parent_qty = int(_xml_text(detail, "{*}QuantityAvailable", "0") or 0)
    parent_price = float(_xml_text(detail, "{*}SellingStatus/{*}CurrentPrice", "0") or 0)

    variations = list(detail.findall(".//{*}Variations/{*}Variation"))

    imported_items = 0
    imported_variations = 0

    if variations:
        for variation in variations:
            sku = _xml_text(variation, "{*}SKU")
            if not sku:
                continue

            qty = int(_xml_text(variation, "{*}Quantity", "0") or 0)
            sold = int(_xml_text(variation, "{*}SellingStatus/{*}QuantitySold", "0") or 0)
            available = max(0, qty - sold)
            price = float(_xml_text(variation, "{*}StartPrice", str(parent_price)) or parent_price or 0)

            stock = _find_or_create_stock(sku, title)
            _upsert_listing(
                store=store,
                stock=stock,
                item_id=item_id,
                sku=sku,
                title=title,
                qty=available,
                price=price,
                is_variation_child=True,
                parent_item_id=item_id,
                variation_sku_map=_variation_specifics_json(variation),
            )
            imported_variations += 1
            imported_items += 1
    else:
        stock = _find_or_create_stock(parent_sku, title)
        _upsert_listing(
            store=store,
            stock=stock,
            item_id=item_id,
            sku=parent_sku,
            title=title,
            qty=parent_qty,
            price=parent_price,
            is_variation_child=False,
            parent_item_id=None,
            variation_sku_map=None,
        )
        imported_items += 1

    return {"items": imported_items, "variations": imported_variations}


def recover_governed_ebay_listing_from_notification(
    *,
    store_id: int | None,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one bounded exact-store recovery through the existing importer.

    This function performs no MarketplaceListing write itself. The existing
    run_governed_ebay_inventory_import() -> _import_item() ->
    _upsert_listing() path remains the only eBay listing writer.
    """
    if store_id is None:
        return {
            "success": False,
            "governed": True,
            "applicable": True,
            "bounded_recovery": True,
            "reason": "ebay_listing_recovery_store_missing",
            "event_type": str(event_type or ""),
        }

    try:
        result = run_governed_ebay_inventory_import(
            store_id=int(store_id),
        )
    except Exception as exc:
        return {
            "success": False,
            "governed": True,
            "applicable": True,
            "bounded_recovery": True,
            "reason": "ebay_listing_recovery_exception",
            "error": str(exc),
            "store_id": int(store_id),
            "event_type": str(event_type or ""),
        }

    return {
        "success": bool((result or {}).get("success")),
        "governed": True,
        "applicable": True,
        "bounded_recovery": True,
        "store_id": int(store_id),
        "event_type": str(event_type or ""),
        "result": result,
    }


def run_governed_ebay_inventory_import(store_id=None) -> dict[str, Any]:
    query = db.session.query(Store).filter(Store.platform.ilike("%ebay%"))

    if store_id:
        query = query.filter(Store.id == store_id)
    else:
        query = query.filter(Store.is_active == True)  # noqa: E712

    stores = query.order_by(Store.id.asc()).all()

    results = []

    for store in stores:
        creds = _parse_creds(store)
        creds = _refresh_access_token_if_needed(store, creds)

        if not creds.get("access_token"):
            results.append({
                "store_id": store.id,
                "store": store.name,
                "success": False,
                "error": "missing_ebay_access_token",
            })
            continue

        imported = 0
        variations = 0
        pages = 0
        seen_item_ids = set()

        # eBay may return up to 100 items even when a smaller entries value is requested.
        # Keep each governed cycle bounded, but resume from the next page next time.
        progress_key = f"ebay_import_next_page_store_{store.id}"
        progress_row = SystemConfig.query.filter_by(key=progress_key).first()

        try:
            start_page = int(progress_row.value) if progress_row and progress_row.value else 1
        except Exception:
            start_page = 1

        if start_page < 1:
            start_page = 1

        max_pages_per_cycle = 2
        end_page = start_page + max_pages_per_cycle - 1
        next_page = start_page

        for page in range(start_page, end_page + 1):
            items = _get_active_items(creds, page=page, entries=100)

            if not items:
                next_page = 1
                break

            pages += 1
            next_page = page + 1

            for item in items:
                item_id = _xml_text(item, "{*}ItemID")

                if not item_id:
                    continue

                if item_id in seen_item_ids:
                    continue

                seen_item_ids.add(item_id)

                counts = _import_item(store, creds, item)

                imported += counts["items"]
                variations += counts["variations"]

                db.session.commit()

            # Final page reached. Reset next cycle back to page 1.
            if len(items) < 100:
                next_page = 1
                break

        if progress_row is None:
            progress_row = SystemConfig(key=progress_key, value=str(next_page))
            db.session.add(progress_row)
        else:
            progress_row.value = str(next_page)

        db.session.commit()

        set_store_runtime_status(store, "idle", last_sync=True)
        db.session.add(SyncLog(
            store_id=store.id,
            status="success",
            items_synced=imported,
            message=(
                f"governed_ebay_inventory_import "
                f"imported={imported} variations={variations} pages={pages}"
            ),
            created_at=datetime.utcnow(),
        ))

        results.append({
            "store_id": store.id,
            "store": store.name,
            "success": True,
            "imported": imported,
            "variations": variations,
            "pages": pages,
        })

    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "marketplace": "ebay",
        "results": results,
    }
