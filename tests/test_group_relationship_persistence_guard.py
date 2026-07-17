from pathlib import Path
import sys

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import models


@pytest.fixture()
def app():
    app = Flask(__name__)
    app.config.update(TESTING=True)
    return app


@pytest.mark.parametrize(
    "path",
    [
        "/governed/groups/create",
        "/governed/groups/45/link-stock",
        "/governed/groups/45/link-listing",
        "/governed/groups/45/unlink",
        "/governed/product-linking/link-listing-to-warehouse",
        "/governed/product-linking/merge-warehouse-group",
    ],
)
def test_explicit_user_post_relationship_actions_are_allowed(app, path):
    with app.test_request_context(path, method="POST"):
        assert (
            models._bt38_is_explicit_group_relationship_user_action()
            is True
        )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/governed/groups/45/unlink"),
        ("POST", "/governed/groups/45/propagate-quantity"),
        ("POST", "/governed/webhooks/amazon"),
        ("POST", "/governed/webhooks/ebay"),
        ("POST", "/governed/amazon/inventory/import"),
        ("POST", "/governed/sync"),
        ("GET", "/product-linking"),
        ("GET", "/governed/product-linking/data"),
        ("GET", "/warehouse"),
        ("GET", "/governed/warehouse/runtime-state"),
    ],
)
def test_automatic_and_read_paths_cannot_change_relationships(
    app,
    method,
    path,
):
    with app.test_request_context(path, method=method):
        assert (
            models._bt38_is_explicit_group_relationship_user_action()
            is False
        )


def test_background_worker_has_no_relationship_authority():
    assert (
        models._bt38_is_explicit_group_relationship_user_action()
        is False
    )


def test_guard_covers_all_permanent_relationship_fields():
    source = Path("models.py").read_text(
        encoding="utf-8",
    )

    required = (
        "MarketplaceListing.warehouse_stock_id",
        "MarketplaceListing.master_product_group_id",
        "WarehouseStock.master_product_group_id",
        "WarehouseStock.is_group_controlled",
    )

    for field in required:
        assert field in source


def test_only_explicit_relationship_paths_are_whitelisted():
    source = Path("models.py").read_text(
        encoding="utf-8",
    )

    forbidden = (
        "propagate-quantity",
        "/webhooks/",
        "/inventory/import",
        "/sync",
        "/runtime-state",
    )

    guard_block = source.split(
        "# BT38 PERMANENT GROUP RELATIONSHIP PERSISTENCE GUARD",
        1,
    )[1]

    whitelist_block = guard_block.split(
        "def _bt38_is_explicit_group_relationship_user_action",
        1,
    )[0]

    for value in forbidden:
        assert value not in whitelist_block
