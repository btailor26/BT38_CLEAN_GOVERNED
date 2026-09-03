"""Align modern eBay ORDER_RETURN_ACTIVITY into the existing BT38 lifecycle.

This is an intake adapter only. It does not create a second return system, poll
eBay, write Warehouse stock, or perform a marketplace write. It teaches the
existing governed webhook execution bridge to recognise eBay's nested return
notification envelope and then reuses the existing MarketplaceOrder lifecycle
writer already installed by governed_fbm_lifecycle_alignment.
"""
from __future__ import annotations


_RETURN_TOPIC = "ORDER_RETURN_ACTIVITY"
_RETURN_ACTIVITIES = {
    "RETURN_REQUESTED",
    "RETURN_FULFILLMENT_INITIATED",
    "RETURN_FULFILLMENT_COMPLETED",
    "RETURN_CLOSED",
}


def _nested_dict(value, *keys):
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _ebay_return_topic(payload: dict) -> str:
    value = _nested_dict(payload, "metadata", "topic")
    return str(value or "").strip().upper()


def _ebay_return_activity(payload: dict) -> str:
    value = _nested_dict(payload, "notification", "data", "activityType")
    if value in (None, ""):
        value = _nested_dict(payload, "data", "activityType")
    return str(value or "").strip().upper()


def _ebay_return_status(payload: dict) -> str:
    value = _nested_dict(payload, "notification", "data", "orderReturn", "returnStatus")
    if value in (None, ""):
        value = _nested_dict(payload, "data", "orderReturn", "returnStatus")
    return str(value or "").strip().upper()


def _return_line_order_id(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() == "returnlineitems" and isinstance(child, list):
                for line in child:
                    if isinstance(line, dict) and line.get("orderId") not in (None, ""):
                        return str(line.get("orderId")).strip()
            found = _return_line_order_id(child)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _return_line_order_id(child)
            if found:
                return found
    return None


def install_governed_ebay_return_intake_alignment() -> None:
    from services import governed_webhook_execution as execution

    if getattr(execution, "_bt38_ebay_return_activity_intake_patched", False):
        return

    original_event_type = execution._event_type
    original_classify = execution._classify_business_event
    original_order_id = execution._extract_marketplace_order_id

    def aligned_event_type(payload):
        topic = _ebay_return_topic(payload or {})
        if topic == _RETURN_TOPIC:
            return _RETURN_TOPIC
        return original_event_type(payload)

    def aligned_classify(event_type, payload):
        topic = _ebay_return_topic(payload or {})
        activity = _ebay_return_activity(payload or {})
        return_status = _ebay_return_status(payload or {})
        if (
            str(event_type or "").strip().upper() == _RETURN_TOPIC
            or topic == _RETURN_TOPIC
            or activity in _RETURN_ACTIVITIES
            or bool(return_status)
        ):
            return "return"
        return original_classify(event_type, payload)

    def aligned_order_id(payload):
        order_id = original_order_id(payload)
        if order_id:
            return order_id
        return _return_line_order_id(payload)

    execution._event_type = aligned_event_type
    execution._classify_business_event = aligned_classify
    execution._extract_marketplace_order_id = aligned_order_id
    execution._bt38_ebay_return_activity_intake_patched = True
