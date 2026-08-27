from inspect import getsource

from services.fbm_packlink_adapter import PacklinkAdapter
from services.fbm_packlink_draft_alignment import (
    _bind_recipient_selectors,
    install_packlink_draft_alignment,
)


def test_packlink_recipient_selector_binding_matches_provider_form_selection():
    adapter = PacklinkAdapter(api_key="test-key")
    body = {
        "to": {
            "name": "Karen",
            "surname": "Llewellyn",
            "street1": "Landscape Cottage, Primrose Hill",
            "city": "Gateshead",
            "zip_code": "NE9 5XP",
            "country": "GB",
            "country_code": "GB",
        },
        "additional_data": {
            "postal_zone_id_to": "gb-zone",
            "zip_code_id_to": "pc_ne95xp",
        },
    }

    returned = _bind_recipient_selectors(adapter, body)

    assert returned is body
    assert body["to"]["country"] == "GB"
    assert body["to"]["country_code"] == "GB"
    assert body["to"]["postal_zone_id"] == "gb-zone"
    assert body["to"]["zip_code_id"] == "pc_ne95xp"
    assert body["additional_data"]["postal_zone_id_to"] == "gb-zone"
    assert body["additional_data"]["zip_code_id_to"] == "pc_ne95xp"


def test_packlink_alignment_binds_selectors_on_existing_single_post_path_only():
    source = getsource(install_packlink_draft_alignment)

    assert "post_with_bound_recipient" in source
    assert "_bind_recipient_selectors(self, body)" in source
    assert 'normalized_endpoint == "shipments"' in source
    assert "original_post_json(endpoint, body)" in source
    assert "_put_json" not in source
    assert "orders" not in source
