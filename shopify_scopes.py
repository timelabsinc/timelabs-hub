"""One least-privilege Shopify OAuth scope contract for app and monitor."""

REQUESTED_SCOPES = (
    "read_products",
    "write_products",
    "read_content",
    "write_content",
    "read_online_store_pages",
    "write_online_store_pages",
    "read_online_store_navigation",
    "write_online_store_navigation",
    "read_themes",
    "write_themes",
    "read_orders",
    # Needed with read_orders to reconcile edits/refunds on records older
    # than Shopify's default 60-day order window.
    "read_all_orders",
    "read_inventory",
    "write_inventory",
    "read_customers",
)

REQUESTED_SCOPE_STRING = ",".join(REQUESTED_SCOPES)


def normalized_scopes(scopes):
    """Shopify write scopes imply their paired read scope."""
    normalized = {str(scope).strip() for scope in scopes if str(scope).strip()}
    for scope in tuple(normalized):
        if scope.startswith("write_"):
            normalized.add("read_" + scope[len("write_"):])
    return normalized
