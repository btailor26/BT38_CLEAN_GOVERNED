from types import SimpleNamespace

import pytest

from services.fbm_marketplace_confirmation import (
    FBMMarketplaceConfirmationError,
    _amazon_package_reference_id,
)


def test_amazon_package_reference_id_is_numeric_string():
    shipment = SimpleNamespace(id=23)

    value = _amazon_package_reference_id(shipment)

    assert value == "23"
    assert isinstance(value, str)
    assert value.isdigit()


def test_amazon_package_reference_id_rejects_non_positive_values():
    with pytest.raises(FBMMarketplaceConfirmationError):
        _amazon_package_reference_id(SimpleNamespace(id=0))
