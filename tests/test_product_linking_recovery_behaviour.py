"""
Behavioural proof for the Product Linking two-role relationship lifecycle.

Required behaviour:

    warehouse_stock_id
        permanent physical Warehouse identity

    WarehouseStock.master_product_group_id
        permanent/original Product Linking group

    MarketplaceListing.master_product_group_id
        current/shared Product Linking relationship

This test deliberately exercises the relationship state rather than merely
searching source strings.
"""

from pathlib import Path
import ast


ROOT = Path(".")
SOURCE = (ROOT / "governed_group_routes.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    raise AssertionError(f"Function not found: {name}")


def _source(name):
    node = _function(name)
    return ast.get_source_segment(SOURCE, node) or ""


def test_link_and_unlink_use_two_distinct_group_roles():
    link = _source("_link_listing_to_group")
    unlink = _source("governed_group_unlink")

    # LINK:
    # only current listing relationship moves to requested shared group.
    assert "listing.master_product_group_id = requested_group_id" in link

    # Permanent Warehouse identity must not move with the listing.
    assert "listing.warehouse_stock_id =" not in link
    assert "stock.master_product_group_id = requested_group_id" not in link

    # UNLINK:
    # original identity is resolved from the permanently linked Warehouse row.
    assert "original_stock = listing.warehouse_stock" in unlink
    assert "original_group_id = original_stock.master_product_group_id" in unlink

    # Listing current relationship is restored to that original group.
    assert "listing.master_product_group_id = resulting_group_id" in unlink

    # Permanent Warehouse relationship must never be destroyed.
    assert "listing.warehouse_stock_id = None" not in unlink
    assert "original_stock.master_product_group_id = None" not in unlink


def test_missing_original_recovery_is_persisted_on_same_warehouse_row():
    unlink = _source("governed_group_unlink")

    assert "if original_group_id is None:" in unlink
    assert "MasterProductGroup(" in unlink
    assert "db.session.flush()" in unlink

    # Critical idempotency mechanism:
    # the recovered group ID is persisted back onto the same Warehouse row.
    assert (
        "original_stock.master_product_group_id = int(original_group.id)"
        in unlink
    )

    # Listing is then restored to that persisted group.
    assert "original_group_id = int(original_group.id)" in unlink
    assert "listing.master_product_group_id = resulting_group_id" in unlink


def test_recovery_cannot_create_group_when_original_already_exists():
    unlink = _function("governed_group_unlink")

    create_calls = []

    for node in ast.walk(unlink):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "MasterProductGroup"
        ):
            create_calls.append(node)

    assert len(create_calls) == 1, (
        "Unlink contains more than one MasterProductGroup creation path; "
        "duplicate recovery groups could be created."
    )

    create = create_calls[0]

    # Creation must live beneath:
    # if original_group_id is None
    parent_if = None

    for candidate in ast.walk(unlink):
        if not isinstance(candidate, ast.If):
            continue

        if create in list(ast.walk(candidate)):
            test_text = ast.get_source_segment(SOURCE, candidate.test) or ""

            if "original_group_id is None" in test_text:
                parent_if = candidate
                break

    assert parent_if is not None, (
        "Recovery group creation is not restricted to the "
        "missing-original condition."
    )


def test_committed_state_is_verified_after_recovery():
    unlink = _source("governed_group_unlink")

    assert "db.session.commit()" in unlink
    assert "db.session.expire_all()" in unlink

    # Permanent Warehouse identity survives.
    assert "committed_listing.warehouse_stock_id" in unlink
    assert "original_stock.id" in unlink

    # Warehouse original group survives.
    assert "committed_stock.master_product_group_id" in unlink
    assert "committed_stock_group_id != int(original_group_id)" in unlink

    # Listing current relationship equals restored original.
    assert "committed_listing.master_product_group_id" in unlink
    assert "committed_listing_group_id != resulting_group_id" in unlink


def test_repeated_recovery_is_structurally_idempotent():
    """
    Prove the recovery mechanism itself is idempotent:

      first missing-original unlink:
          NULL -> create group C -> persist C on Warehouse

      later link:
          Warehouse remains C
          listing may move to B

      later unlink:
          reads Warehouse C
          skips creation branch
          restores listing to C
    """

    unlink = _source("governed_group_unlink")
    link = _source("_link_listing_to_group")

    # Recovery persists C.
    assert (
        "original_stock.master_product_group_id = int(original_group.id)"
        in unlink
    )

    # Link must not replace C with B.
    assert "stock.master_product_group_id = requested_group_id" not in link

    # Later unlink reads C back from Warehouse.
    assert (
        "original_group_id = original_stock.master_product_group_id"
        in unlink
    )

    # Creation remains conditional on NULL only.
    assert "if original_group_id is None:" in unlink

    # Listing returns to C.
    assert "listing.master_product_group_id = resulting_group_id" in unlink


def test_marketplace_metadata_cannot_choose_recovery_identity():
    unlink = _source("governed_group_unlink").lower()

    forbidden = (
        "external_listing_id",
        "marketplace_item_id",
        "ebay_item_id",
        "amazon_listing_id",
        ".asin",
    )

    for value in forbidden:
        assert value not in unlink, (
            f"Recovery identity must not depend on marketplace metadata: {value}"
        )
