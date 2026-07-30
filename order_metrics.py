"""Canonical SQL predicates for sales metrics.

Operational orders remain visible elsewhere, but a sale excludes cancelled,
refunded/voided, and stock builds. Keep both forms so queries can use either a
bare ``orders`` table or an ``o`` alias without rewriting the definition.
"""

SALE_ORDER_PREDICATE = (
    "status != 'cancelled' "
    "AND (financial_status IS NULL OR LOWER(financial_status) "
    "NOT IN ('refunded','voided')) "
    "AND COALESCE(is_stock,0)=0"
)

SALE_ORDER_PREDICATE_O = (
    "o.status != 'cancelled' "
    "AND (o.financial_status IS NULL OR LOWER(o.financial_status) "
    "NOT IN ('refunded','voided')) "
    "AND COALESCE(o.is_stock,0)=0"
)
