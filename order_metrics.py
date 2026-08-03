"""Canonical SQL helpers for sales metrics.

Operational orders remain visible elsewhere, but a sale excludes cancelled,
refunded/voided, stock builds, and locally-hidden records (the "delete" a
staff member applied to a website/messaging order that can't be truly
removed without risking a Shopify resync resurrecting it — see
_handle_orders_delete). Keep both forms so queries can use either a bare
``orders`` table or an ``o`` alias without rewriting the definition.

Product revenue needs one extra rule: ``orders.sale_total_paise`` is a
whole-order amount while ``order_items`` may contain several products. The
shared join/expression below allocates that amount by each item's original
line-total share, with quantity as the fallback for zero-value item rows.
Using the same SQL in the web dashboard, generated Home page, and ``labs``
CLI prevents those three surfaces from reporting different revenue.
"""

SALE_ORDER_PREDICATE = (
    "status != 'cancelled' "
    "AND (financial_status IS NULL OR LOWER(financial_status) "
    "NOT IN ('refunded','voided')) "
    "AND COALESCE(is_stock,0)=0 "
    "AND COALESCE(local_hidden,0)=0"
)

SALE_ORDER_PREDICATE_O = (
    "o.status != 'cancelled' "
    "AND (o.financial_status IS NULL OR LOWER(o.financial_status) "
    "NOT IN ('refunded','voided')) "
    "AND COALESCE(o.is_stock,0)=0 "
    "AND COALESCE(o.local_hidden,0)=0"
)

ORDER_ITEM_TOTALS_JOIN = (
    "LEFT JOIN (SELECT order_id, "
    "SUM(COALESCE(line_total,0)) AS raw_total, "
    "SUM(COALESCE(quantity,0)) AS item_qty "
    "FROM order_items GROUP BY order_id) oit ON oit.order_id=oi.order_id "
)

ALLOCATED_ITEM_REVENUE_SQL = (
    "CASE "
    "WHEN o.sale_total_paise IS NULL THEN COALESCE(oi.line_total,0) "
    "WHEN COALESCE(oit.raw_total,0)>0 THEN "
    "(o.sale_total_paise/100.0)*COALESCE(oi.line_total,0)/oit.raw_total "
    "WHEN COALESCE(oit.item_qty,0)>0 THEN "
    "(o.sale_total_paise/100.0)*COALESCE(oi.quantity,0)/oit.item_qty "
    "ELSE 0 END"
)
