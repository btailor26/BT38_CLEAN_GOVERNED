"""Governed UK property-address lookup for manual shipping.

BT38 asks Homedata's postcode endpoint only when the user explicitly searches a
postcode. Homedata's free tier requires an API key for the postcode/address
endpoint. The key is read server-side from HOMEDATA_API_KEY and is never exposed
to the browser. No polling or background work is introduced.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, request
from flask_login import login_required


governed_fbm_address_lookup_bp = Blueprint("governed_fbm_address_lookup", __name__)


def _api_key() -> str:
    return str(os.environ.get("HOMEDATA_API_KEY") or "").strip()


def _provider_json(url: str, api_key: str) -> dict:
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "BT38/1.0",
            "Authorization": f"Api-Key {api_key}",
        },
    )
    with urlopen(req, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _clean(value) -> str:
    return str(value or "").strip()


@governed_fbm_address_lookup_bp.get("/fbm/manual/address-lookup")
@login_required
def manual_address_lookup():
    postcode = "".join(str(request.args.get("postcode") or "").upper().split())
    if not postcode or len(postcode) < 5 or len(postcode) > 7:
        return jsonify({"success": False, "message": "Enter a valid UK postcode."}), 400

    api_key = _api_key()
    if not api_key:
        return jsonify({
            "success": False,
            "message": "Address lookup is not configured. Set HOMEDATA_API_KEY on BT38.",
        }), 503

    url = f"https://api.homedata.co.uk/address/postcode/{quote(postcode, safe='')}/"
    try:
        payload = _provider_json(url, api_key)
    except HTTPError as exc:
        if exc.code == 404:
            return jsonify({
                "success": True,
                "postcode": postcode,
                "addresses": [],
                "message": "No addresses found for this postcode.",
            })
        if exc.code in (401, 403):
            return jsonify({
                "success": False,
                "message": "Address lookup authentication failed. Check HOMEDATA_API_KEY and free-tier access.",
            }), 502
        if exc.code == 429:
            return jsonify({
                "success": False,
                "message": "Address lookup rate limit reached. Try again shortly.",
            }), 429
        return jsonify({"success": False, "message": f"Address lookup failed (HTTP {exc.code})."}), 502
    except (URLError, TimeoutError, ValueError):
        return jsonify({"success": False, "message": "Address lookup provider is unavailable."}), 502

    if not isinstance(payload, dict):
        return jsonify({"success": False, "message": "Address lookup returned an invalid response."}), 502

    canonical_postcode = _clean(payload.get("postcode")) or postcode
    addresses = []
    for index, item in enumerate(payload.get("addresses") or []):
        if not isinstance(item, dict):
            continue
        label = _clean(item.get("address"))
        building = _clean(item.get("building_name") or item.get("building_number"))
        street = _clean(item.get("street"))
        town = _clean(item.get("town"))
        county = _clean(item.get("county"))
        line1 = " ".join(part for part in (building, street) if part).strip()
        if not line1 and label:
            parts = [part.strip() for part in label.split(",") if part.strip()]
            line1 = parts[0] if parts else label
        if not town and label:
            parts = [part.strip() for part in label.split(",") if part.strip()]
            if len(parts) > 1:
                town = parts[-1]
        if not label:
            label = ", ".join(part for part in (line1, town, canonical_postcode) if part)
        addresses.append({
            "id": str(index),
            "label": label,
            "address": {
                "ship_to_address": line1,
                "ship_to_address2": "",
                "ship_to_city": town,
                "ship_to_region": county,
                "ship_to_postcode": canonical_postcode,
                "ship_to_country": "GB",
            },
        })

    return jsonify({
        "success": True,
        "postcode": canonical_postcode,
        "addresses": addresses,
        "message": f"{len(addresses)} address{'es' if len(addresses) != 1 else ''} found.",
        "property_lookup": True,
        "provider": "homedata",
    })
