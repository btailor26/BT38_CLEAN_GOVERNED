"""Governed free UK postcode lookup for manual shipping.

BT38 uses Postcodes.io for postcode validation and locality metadata. No API key
is required. Lookup runs only when the user explicitly searches a postcode; no
polling or background work is introduced. Postcodes.io does not provide PAF
property-level addresses, so house number/street remains user-entered.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, request
from flask_login import login_required


governed_fbm_address_lookup_bp = Blueprint("governed_fbm_address_lookup", __name__)


def _provider_json(url: str) -> dict:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "BT38/1.0"})
    with urlopen(req, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


@governed_fbm_address_lookup_bp.get("/fbm/manual/address-lookup")
@login_required
def manual_address_lookup():
    postcode = " ".join(str(request.args.get("postcode") or "").upper().split())
    if not postcode or len(postcode) < 5 or len(postcode) > 8:
        return jsonify({"success": False, "message": "Enter a valid UK postcode."}), 400

    url = f"https://api.postcodes.io/postcodes/{quote(postcode, safe='')}"
    try:
        payload = _provider_json(url)
    except HTTPError as exc:
        if exc.code == 404:
            return jsonify({"success": False, "message": "UK postcode was not found."}), 404
        return jsonify({"success": False, "message": f"Postcode lookup failed (HTTP {exc.code})."}), 502
    except (URLError, TimeoutError, ValueError):
        return jsonify({"success": False, "message": "Postcode lookup provider is unavailable."}), 502

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return jsonify({"success": False, "message": "Postcode lookup returned an invalid response."}), 502

    city = str(result.get("post_town") or result.get("admin_district") or result.get("parish") or "").strip()
    region = str(result.get("admin_county") or result.get("region") or result.get("admin_district") or "").strip()
    canonical_postcode = str(result.get("postcode") or postcode).upper().strip()

    return jsonify({
        "success": True,
        "postcode": canonical_postcode,
        "address": {
            "ship_to_city": city,
            "ship_to_region": region,
            "ship_to_postcode": canonical_postcode,
            "ship_to_country": "GB",
        },
        "message": "Postcode verified. City/region filled where available; enter the house number and street manually.",
        "property_lookup": False,
        "provider": "postcodes.io",
    })
