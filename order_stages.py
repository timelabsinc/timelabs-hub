#!/usr/bin/env python3
"""The order lifecycle — one canonical definition for the whole OS.

The supplier queue, the order form, the sheet mirror and the server validation
all read the pipeline from here, so there is exactly one place that knows what
a stage is called and what comes after it. Before this the stages lived as a
bare list in order_form.py and the supplier page grouped them by hand into
attn/wip/done buckets; the stages ARE the sections now.

The pipeline a build actually moves through:

    Pending  ->  Ordered to supplier  ->  Shipped from China  ->  Received  ->  Delivered

Cancelled is a side state, not a step. It sits off the forward line; a
cancelled row that had already been shared remains visible to staff in the
supplier queue's Problems area so committed work is never silently hidden.
"""

# (key, label) in pipeline order. The key is what's stored in orders.status and
# passed over the wire; the label is the only thing a person ever sees.
STAGES = [
    ("pending",   "Pending"),
    ("ordered",   "Ordered to supplier"),
    ("shipped",   "Shipped from China"),
    ("received",  "Received"),
    ("delivered", "Delivered"),
]

PIPELINE = [k for k, _ in STAGES]          # the 5 forward stages, in order
LABELS = dict(STAGES)
LABELS["cancelled"] = "Cancelled"
# Everything the server will accept as a status. Cancelled last so it never
# shows up as "the next stage" after Delivered.
STATUSES = PIPELINE + ["cancelled"]

# New orders start here; the supplier walks them forward one tap at a time.
DEFAULT_STAGE = "pending"

# Build detail freezes once we've committed the order to the supplier — from
# here on they may have bought parts against it. (Was "paid" in the old 8-status
# model; "ordered" is the equivalent commitment point now.)
LOCK_FROM = "ordered"

# The old 8-status pipeline these replace, mapped so the 32 orders already in
# the table land in the right section. Keyed on the OLD value; a single CASE
# using this runs the migration in one idempotent pass (see agent_chat_server
# _ensure_orders_schema). Note "shipped" meant shipped-to-customer before and
# maps to Delivered, while the NEW "shipped" means shipped-from-China — the
# migration reads the old value, so they don't collide.
OLD_TO_NEW = {
    "new": "pending",
    "acknowledged": "ordered",
    "paid": "ordered",
    "in transit": "shipped",
    "assembled": "received",
    "shipped": "delivered",
    "delivered": "delivered",
    "cancelled": "cancelled",
}


def label(key):
    return LABELS.get(key, key or "")


def next_of(key):
    """The stage after `key`, or None if it's the last one (or off-pipeline)."""
    if key not in PIPELINE:
        return None
    i = PIPELINE.index(key)
    return PIPELINE[i + 1] if i + 1 < len(PIPELINE) else None


def is_committed(key, supplier_visible=False, shipment_id=None, bill_id=None):
    """Whether a build has crossed a boundary that must not be undone casually."""
    return (
        bool(supplier_visible)
        and key in PIPELINE
        and PIPELINE.index(key) >= PIPELINE.index(LOCK_FROM)
    ) or shipment_id is not None or bill_id is not None


def supplier_can_advance(current, target):
    """Suppliers move a build exactly one step forward and never cancel it."""
    return next_of(current) == target


def staff_transition_allowed(current, target, committed=False):
    """Protect committed work from cancellation or a backwards stage move."""
    if target not in STATUSES:
        return False
    if current == target or not committed:
        return True
    if target == "cancelled" or current not in PIPELINE or target not in PIPELINE:
        return False
    return PIPELINE.index(target) >= PIPELINE.index(current)


def is_terminal(key):
    return key == PIPELINE[-1] or key == "cancelled"
