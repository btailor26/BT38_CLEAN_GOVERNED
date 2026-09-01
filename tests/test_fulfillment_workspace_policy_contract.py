from decimal import Decimal

from services.governed_fulfillment_workspace_policy import (
    SpendEntry,
    fulfillment_section,
    seller_courier_policy,
    spend_totals,
)


def test_fbm_pending_is_dispatch_due_and_shipped_moves_to_dispatched():
    pending = fulfillment_section(fulfillment_type="FBM", status="pending")
    shipped = fulfillment_section(fulfillment_type="FBM", status="processed", shipped_at="2026-09-01")
    assert (pending.family, pending.view) == ("FBM", "dispatch_due")
    assert (shipped.family, shipped.view) == ("FBM", "dispatched")


def test_fba_never_enters_fbm_and_pending_stays_pending_until_dispatch():
    pending = fulfillment_section(fulfillment_type="FBA", status="pending")
    shipped = fulfillment_section(fulfillment_type="FBA", status="shipped")
    assert (pending.family, pending.view) == ("FBA", "pending")
    assert (shipped.family, shipped.view) == ("FBA", "dispatched")


def test_profile_fba_truth_overrides_stale_fbm_field():
    section = fulfillment_section(
        fulfillment_type="FBM",
        profile_channel="AFN",
        status="pending",
    )
    assert (section.family, section.view) == ("FBA", "pending")


def test_shipping_spend_is_per_dispatch_not_units_and_fba_fbm_are_separate():
    totals = spend_totals([
        SpendEntry("label-1", "FBM", Decimal("3.25"), source="packlink"),
        # Same physical dispatch represented again must not create a second dispatch cost.
        SpendEntry("label-1", "FBM", Decimal("3.25"), source="invoice"),
        SpendEntry("amazon-fulfilment-1", "FBA", Decimal("4.50"), source="marketplace"),
    ])
    assert totals == {
        "fbm_dispatches": 1,
        "fbm_spend": Decimal("3.25"),
        "fba_dispatches": 1,
        "fba_spend": Decimal("4.50"),
    }


def test_seller_courier_is_hard_blocked_for_prime_sfp():
    policy = seller_courier_policy(is_prime_or_sfp=True, owned_channel=False)
    assert policy["allowed"] is False
    assert policy["hard_blocked"] is True


def test_marketplace_seller_courier_requires_metrics_warning_but_owned_channel_does_not():
    marketplace = seller_courier_policy(is_prime_or_sfp=False, owned_channel=False)
    owned = seller_courier_policy(is_prime_or_sfp=False, owned_channel=True)
    assert marketplace["allowed"] is True
    assert marketplace["marketplace_metrics_warning"] is True
    assert owned["allowed"] is True
    assert owned["marketplace_metrics_warning"] is False
