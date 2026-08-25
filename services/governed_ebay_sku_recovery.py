"""Governed recovery for active eBay listings/variations that have no seller SKU.

This module is eBay-only. It never replaces a non-empty seller SKU. Recovery is
bounded to one ItemID and, for variations, one exact VariationSpecifics identity.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from typing import Any

import requests

EBAY_TRADING_URL = "https://api.ebay.com/ws/api.dll"
EBAY_COMPAT_LEVEL = "1193"
EBAY_SITE_ID = "3"


def _text(node: ET.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    found = node.find(path)
    if found is None or found.text is None:
        return default
    return str(found.text).strip()


def _headers(creds: dict[str, Any], call_name: str) -> dict[str, str]:
    return {
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": str(creds.get("site_id") or creds.get("siteid") or EBAY_SITE_ID),
        "X-EBAY-API-COMPATIBILITY-LEVEL": str(creds.get("compatibility_level") or EBAY_COMPAT_LEVEL),
        "X-EBAY-API-IAF-TOKEN": str(creds.get("access_token") or ""),
        "Content-Type": "text/xml",
    }


def variation_identity(variation: ET.Element) -> str:
    pairs: list[tuple[str, tuple[str, ...]]] = []
    for nvl in variation.findall(".//{*}VariationSpecifics/{*}NameValueList"):
        name = _text(nvl, "{*}Name")
        values = tuple(
            (value.text or "").strip()
            for value in nvl.findall("{*}Value")
            if value is not None and value.text
        )
        if name:
            pairs.append((name, values))
    pairs.sort(key=lambda row: row[0].lower())
    return json.dumps(pairs, ensure_ascii=True, separators=(",", ":"))


def generated_sku(item_id: str, variation: ET.Element | None = None) -> str:
    """Return a stable <=50-character BT38 recovery SKU."""
    if variation is None:
        return f"BT38-EB-{item_id}"[:50]
    digest = hashlib.sha1(variation_identity(variation).encode("utf-8")).hexdigest()[:10].upper()
    return f"BT38-EB-{item_id}-{digest}"[:50]


def _append_variation_specifics(parent: ET.Element, variation: ET.Element) -> None:
    specifics = ET.SubElement(parent, "VariationSpecifics")
    for source in variation.findall(".//{*}VariationSpecifics/{*}NameValueList"):
        target = ET.SubElement(specifics, "NameValueList")
        ET.SubElement(target, "Name").text = _text(source, "{*}Name")
        for source_value in source.findall("{*}Value"):
            if source_value.text:
                ET.SubElement(target, "Value").text = source_value.text.strip()


def _revise_missing_sku(
    creds: dict[str, Any],
    item_id: str,
    sku: str,
    variation: ET.Element | None,
) -> None:
    request = ET.Element("ReviseFixedPriceItemRequest", xmlns="urn:ebay:apis:eBLBaseComponents")
    credentials = ET.SubElement(request, "RequesterCredentials")
    ET.SubElement(credentials, "eBayAuthToken").text = str(creds.get("access_token") or "")
    item = ET.SubElement(request, "Item")
    ET.SubElement(item, "ItemID").text = item_id

    if variation is None:
        ET.SubElement(item, "SKU").text = sku
    else:
        variations = ET.SubElement(item, "Variations")
        target = ET.SubElement(variations, "Variation")
        ET.SubElement(target, "SKU").text = sku
        quantity = _text(variation, "{*}Quantity")
        start_price = _text(variation, "{*}StartPrice")
        if quantity:
            ET.SubElement(target, "Quantity").text = quantity
        if start_price:
            ET.SubElement(target, "StartPrice").text = start_price
        _append_variation_specifics(target, variation)

    response = requests.post(
        EBAY_TRADING_URL,
        headers=_headers(creds, "ReviseFixedPriceItem"),
        data=ET.tostring(request, encoding="utf-8", xml_declaration=True),
        timeout=60,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    ack = _text(root, ".//{*}Ack").upper()
    if ack not in {"SUCCESS", "WARNING"}:
        messages = [
            _text(error, "{*}LongMessage") or _text(error, "{*}ShortMessage")
            for error in root.findall(".//{*}Errors")
        ]
        raise RuntimeError("ebay_missing_sku_revise_failed: " + "; ".join(filter(None, messages)))


def _get_item(creds: dict[str, Any], item_id: str) -> ET.Element:
    request = ET.Element("GetItemRequest", xmlns="urn:ebay:apis:eBLBaseComponents")
    credentials = ET.SubElement(request, "RequesterCredentials")
    ET.SubElement(credentials, "eBayAuthToken").text = str(creds.get("access_token") or "")
    ET.SubElement(request, "ItemID").text = item_id
    ET.SubElement(request, "DetailLevel").text = "ReturnAll"
    response = requests.post(
        EBAY_TRADING_URL,
        headers=_headers(creds, "GetItem"),
        data=ET.tostring(request, encoding="utf-8", xml_declaration=True),
        timeout=60,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    item = root.find(".//{*}Item")
    if item is None:
        raise RuntimeError("ebay_missing_sku_verify_item_not_found")
    return item


def ensure_single_listing_sku(creds: dict[str, Any], item_id: str, detail: ET.Element) -> tuple[str, ET.Element, bool]:
    existing = _text(detail, "{*}SKU")
    if existing:
        return existing, detail, False

    sku = generated_sku(item_id)
    _revise_missing_sku(creds, item_id, sku, None)
    verified = _get_item(creds, item_id)
    if _text(verified, "{*}SKU") != sku:
        raise RuntimeError("ebay_missing_sku_verification_failed")
    return sku, verified, True


def ensure_variation_sku(
    creds: dict[str, Any],
    item_id: str,
    variation: ET.Element,
) -> tuple[str, ET.Element, ET.Element, bool]:
    existing = _text(variation, "{*}SKU")
    if existing:
        return existing, variation, variation, False

    identity = variation_identity(variation)
    sku = generated_sku(item_id, variation)
    _revise_missing_sku(creds, item_id, sku, variation)
    verified_item = _get_item(creds, item_id)

    for candidate in verified_item.findall(".//{*}Variations/{*}Variation"):
        if variation_identity(candidate) == identity:
            if _text(candidate, "{*}SKU") != sku:
                raise RuntimeError("ebay_missing_variation_sku_verification_failed")
            return sku, candidate, verified_item, True

    raise RuntimeError("ebay_missing_variation_identity_not_found_after_revise")
