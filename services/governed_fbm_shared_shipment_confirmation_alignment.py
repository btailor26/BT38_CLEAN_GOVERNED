"""Extend the existing external marketplace-confirmation path to shared parcels.

No new shipment or marketplace-write path is created. The existing physical
FBMShipment is confirmed first for its primary order; only after that succeeds
are explicitly linked same-marketplace orders given the same tracking number.
"""
from __future__ import annotations


def install_governed_fbm_shared_shipment_confirmation_alignment() -> None:
    import services.fbm_marketplace_confirmation as confirmation
    import services.fbm_post_purchase as post_purchase

    if getattr(confirmation, "_bt38_shared_shipment_confirmation_patched", False):
        return

    original = confirmation.confirm_external_shipment

    def confirm_with_linked_orders(*, shipment, mapping):
        result = original(shipment=shipment, mapping=mapping)
        if not isinstance(result, dict) or result.get("success") is not True:
            return result
        if str(getattr(shipment, "provider", "") or "").strip().lower() == "amazon_buy_shipping":
            return result

        from services.fbm_shared_shipment_confirmation import confirm_linked_external_orders
        linked = confirm_linked_external_orders(shipment=shipment, mapping=mapping)
        if linked.get("attempted"):
            result = dict(result)
            result["linked_orders"] = linked
            if linked.get("failed"):
                result["linked_orders_attention_required"] = True
        return result

    confirmation.confirm_external_shipment = confirm_with_linked_orders
    # fbm_post_purchase imported the original symbol directly, so align that
    # module-global binding too. Future carrier-mapping release imports resolve
    # the patched function from fbm_marketplace_confirmation automatically.
    post_purchase.confirm_external_shipment = confirm_with_linked_orders
    confirmation._bt38_shared_shipment_confirmation_patched = True
