"""Governed UK address lookup for manual shipping.

The browser never receives the provider API key. Lookup happens only when the
user explicitly searches a postcode; there is no polling or background work.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, request
from flask_login import login_required


governed_fbm_address_lookup_bp = Blueprint("governed_fbm_address_lookup", __name__)


def _api_key() -> str:
    return str(os.environ.get("GETADDRESS_API_KEY") or "").strip()


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

    key = _api_key()
    if not key:
        return jsonify({
            "success": False,
            "message": "Postcode lookup is not configured. Set GETADDRESS_API_KEY on BT38.",
        }), 503

    # getAddress autocomplete returns all suggestions for a complete postcode.
    params = urlencode({"api-key": key, "all": "true", "show-postcode": "true", "top": "6"})
    url = f"https://api.getAddress.io/autocomplete/{quote(postcode, safe='')}?{params}"
    try:
        payload = _provider_json(url)
    except HTTPError as exc:
        if exc.code == 404:
            return jsonify({"success": True, "postcode": postcode, "addresses": []})
        return jsonify({"success": False, "message": f"Address lookup failed (HTTP {exc.code})."}), 502
    except (URLError, TimeoutError, ValueError):
        return jsonify({"success": False, "message": "Address lookup provider is unavailable."}), 502

    suggestions = payload.get("suggestions") if isinstance(payload, dict) else []
    addresses = []
    for item in suggestions or []:
        if not isinstance(item, dict):
            continue
        address_id = str(item.get("id") or "").strip()
        label = str(item.get("address") or "").strip()
        if address_id and label:
            addresses.append({"id": address_id, "label": label})
    return jsonify({"success": True, "postcode": postcode, "addresses": addresses})


@governed_fbm_address_lookup_bp.get("/fbm/manual/address-lookup/<path:address_id>")
@login_required
def manual_address_lookup_resolve(address_id: str):
    key = _api_key()
    if not key:
        return jsonify({"success": False, "message": "Postcode lookup is not configured."}), 503
    address_id = str(address_id or "").strip()
    if not address_id or len(address_id) > 256:
        return jsonify({"success": False, "message": "Address selection is invalid."}), 400

    params = urlencode({"api-key": key})
    url = f"https://api.getAddress.io/get/{quote(address_id, safe='')}?{params}"
    try:
        payload = _provider_json(url)
    except HTTPError as exc:
        return jsonify({"success": False, "message": f"Address lookup failed (HTTP {exc.code})."}), 502
    except (URLError, TimeoutError, ValueError):
        return jsonify({"success": False, "message": "Address lookup provider is unavailable."}), 502

    if not isinstance(payload, dict):
        return jsonify({"success": False, "message": "Address lookup returned an invalid response."}), 502

    line2 = str(payload.get("line_2") or "").strip()
    line3 = str(payload.get("line_3") or "").strip()
    if line3:
        line2 = ", ".join(part for part in (line2, line3) if part)

    return jsonify({
        "success": True,
        "address": {
            "ship_to_address": str(payload.get("line_1") or "").strip(),
            "ship_to_address2": line2,
            "ship_to_city": str(payload.get("town_or_city") or "").strip(),
            "ship_to_region": str(payload.get("county") or payload.get("district") or "").strip(),
            "ship_to_postcode": str(payload.get("postcode") or "").upper().strip(),
            "ship_to_country": "GB",
        },
    })
