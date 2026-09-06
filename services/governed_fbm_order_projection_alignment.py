"""Keep FBM reads on the strongest persisted logical order truth.

Older eBay webhook intake could create a sparse sibling row keyed by listingId
beside the exact hydrated order-line row. This alignment is deliberately read
only: it never deletes/merges MarketplaceOrder rows and never calls a marketplace
or provider.

The FBM page still performs its existing bounded query. For only the identities
already selected for that page, one set-based sibling read chooses the strongest
persisted representative. Provider parcel preparation also ignores the precise
legacy sparse eBay sibling pattern so one physical order is not counted twice.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import tuple_

from extensions import db
from models import MarketplaceOrder


def _text(value) -> str:
    return str(value or "").strip()


def _destination_score(row: MarketplaceOrder) -> int:
    return sum(bool(_text(getattr(row, field, None))) for field in (
        "ship_to_name",
        "ship_to_address",
        "ship_to_city",
        "ship_to_postcode",
        "ship_to_phone",
    ))


def _representative_rank(row: MarketplaceOrder) -> tuple:
    """Prefer lifecycle truth first, then complete/processed persisted identity."""
    status = _text(getattr(row, "status", None)).lower()
    source = _text(getattr(row, "import_source", None)).lower()
    return (
        1 if _text(getattr(row, "tracking_number", None)) else 0,
        1 if getattr(row, "shipped_at", None) is not None else 0,
        1 if status in {"shipped", "dispatched", "fulfilled", "delivered"} else 0,
        1 if _text(getattr(row, "carrier", None)) else 0,
        1 if getattr(row, "processed_at", None) is not None else 0,
        _destination_score(row),
        1 if "exact_order_hydration" in source else 0,
        int(getattr(row, "id", 0) or 0),
    )


def _is_sparse_legacy_ebay_sibling(row: MarketplaceOrder) -> bool:
    """Match only the known listingId-as-lineId ghost created by old webhook intake."""
    source = _text(getattr(row, "import_source", None)).lower()
    if source != "webhook_ebay":
        return False
    if getattr(row, "processed_at", None) is not None:
        return False
    if _text(getattr(row, "tracking_number", None)) or getattr(row, "shipped_at", None) is not None:
        return False
    return _destination_score(row) <= 1


def _has_exact_hydrated_twin(row: MarketplaceOrder, rows: list[MarketplaceOrder]) -> bool:
    sku = _text(getattr(row, "sku", None))
    try:
        quantity = int(getattr(row, "quantity", 0) or 0)
    except (TypeError, ValueError):
        quantity = 0
    for candidate in rows:
        if candidate is row:
            continue
        source = _text(getattr(candidate, "import_source", None)).lower()
        if "exact_order_hydration" not in source:
            continue
        if getattr(candidate, "processed_at", None) is None:
            continue
        if _text(getattr(candidate, "sku", None)) != sku:
            continue
        try:
            candidate_quantity = int(getattr(candidate, "quantity", 0) or 0)
        except (TypeError, ValueError):
            candidate_quantity = 0
        if candidate_quantity == quantity:
            return True
    return False


def canonical_order_lines(rows: list[MarketplaceOrder]) -> list[MarketplaceOrder]:
    """Suppress only proven sparse eBay ghost siblings; preserve genuine multi-lines."""
    if len(rows) < 2:
        return rows
    return [
        row for row in rows
        if not (_is_sparse_legacy_ebay_sibling(row) and _has_exact_hydrated_twin(row, rows))
    ] or rows


def _install_order_line_projection() -> None:
    import services.fbm_order_mapper as mapper
    import governed_fbm_routes as routes

    current = mapper.order_lines
    if getattr(current, "_bt38_logical_order_lines", False):
        return

    def logical_order_lines(order):
        rows = current(order)
        return canonical_order_lines(rows)

    logical_order_lines._bt38_logical_order_lines = True
    mapper.order_lines = logical_order_lines
    # governed_fbm_routes imported the function directly, so align that bound
    # reference too. This changes only read projection, not order persistence.
    routes.order_lines = logical_order_lines


def _install_page_representative_projection() -> None:
    import services.governed_fbm_page_alignment as page

    current = page._latest_distinct_fbm_rows
    if getattr(current, "_bt38_strongest_order_projection", False):
        return

    def strongest_rows(limit: int):
        selected, has_more = current(limit)
        identities = sorted({
            (int(row.store_id), str(row.marketplace_order_id))
            for row in selected
            if row.store_id is not None and row.marketplace_order_id
        })
        if not identities:
            return selected, has_more

        # One bounded set-based sibling query for the already selected page only.
        siblings = (
            db.session.query(MarketplaceOrder)
            .filter(tuple_(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id).in_(identities))
            .all()
        )
        grouped: dict[tuple[int, str], list[MarketplaceOrder]] = defaultdict(list)
        for row in siblings:
            grouped[(int(row.store_id), str(row.marketplace_order_id))].append(row)

        projected: list[MarketplaceOrder] = []
        for original in selected:
            key = (int(original.store_id), str(original.marketplace_order_id))
            candidates = grouped.get(key) or [original]
            projected.append(max(candidates, key=_representative_rank))
        return projected, has_more

    strongest_rows._bt38_strongest_order_projection = True
    page._latest_distinct_fbm_rows = strongest_rows


def install_governed_fbm_order_projection_alignment() -> None:
    _install_order_line_projection()
    _install_page_representative_projection()
