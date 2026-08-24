# Timelabs Co — Business & Money Flow

*The canonical explainer of how the business works and how money moves — written for the
CA / bookkeeper, kept as the agent's memory of the subject. Companion to `business-context.md`
(which describes the tooling; this file describes the commerce).*

**Status: DRAFT v0.1 — built from the repo and connected systems. Items marked `[?]` are
open questions awaiting the owner's explanation; do not treat them as facts yet.**

*Last updated 2026-08-24.*

---

## 1. What the business is

**Timelabs Co** (storefront `timelabsco.in`, Shopify store `xd2fwj-1h.myshopify.com`) is an
India-based direct-to-consumer brand selling **Seiko watch-mod parts and complete custom
builds** — homage/mod watches (Datejust, Royal Oak, Nautilus, Aquanaut styles) built on Seiko
movements (NH35 and similar). Typical selling price **₹11,000–₹27,000** per watch. Market is
India; the store runs on Shopify (Basic plan), priced in INR.

The watches are **built by a supplier partner — Hannan (the "TimeLabsCo × Sunesra"
collaboration)** — not in-house. Timelabs takes the order, the supplier builds and ships,
Timelabs handles the customer.

`[?]` Legal form of the business (sole proprietorship / partnership / Pvt Ltd), registered
name, GST registration status, and what exactly the × Sunesra relationship is on paper
(vendor? profit-share partner?).

## 2. How orders (revenue) arrive — three channels

All three land in one unified order database with one running order number.

1. **Shopify website** (`timelabsco.in`) — synced automatically every 5 minutes. Shopify
   records the price and a `financial_status` per order.
2. **WhatsApp** — customers message directly; orders are logged manually via the order form.
3. **Manual order form** (`/ops/order-form.html`) — staff paste customer details; used for
   WhatsApp and offline orders.

Order lifecycle: `new → acknowledged → paid → in transit → assembled → shipped → delivered`
(+ `cancelled`). "Paid" is an explicit, tracked stage. Some builds are **stock builds**
(built for inventory, not against a customer order) and are deliberately *not* counted as
sales.

## 3. How the money comes in `[largely ?]`

- `[?]` Website orders: which payment gateway sits behind Shopify checkout (Razorpay /
  Cashfree / PayU / other), and its settlement cycle (T+1/T+2) into which bank account.
- `[?]` WhatsApp orders: how customers pay — UPI / bank transfer / gateway payment link —
  and into which account (business or personal).
- `[?]` Whether COD is offered, and if so who the COD partner is and how remittance works.
- `[?]` Advance vs. full payment: whether custom builds take an advance/deposit before the
  supplier starts, with balance later.
- `[?]` The receiving bank account(s): how many, in whose name, current vs. savings.

## 4. How the money goes out

### 4.1 Supplier (cost of goods) — the biggest and most unusual flow

- The supplier **invoices in USD**, but is paid **from an Indian account**.
- The number that matters is the **effective rate ≈ ₹100 per USD** — market rate (~₹86 over
  the 2025-06→2026-06 invoice window) **plus** the bank's forex markup **plus** the
  wire/transfer fee. That gap (~16% on top of goods) is the real, previously-invisible
  **cost of banking**, and the Ledger tool tracks it separately from goods cost.
- Uploaded supplier invoices are treated as **already paid** (owner's rule).
- `[?]` The actual payment channel (bank wire / Wise / other), which bank, and the purpose
  code used for the outward remittance.
- `[?]` Where the supplier is located and where goods ship from.

### 4.2 Inbound freight (consignments)

Builds are grouped into **consignments/shipments**; each consignment has a total shipping
cost, giving a **per-watch freight cost** (total ÷ count). This is part of landed cost.

- `[?]` Who carries the goods (courier / freight forwarder), and whether customs duty +
  IGST on import is paid — by whom, and against whose IEC / name.

### 4.3 Outbound shipping to customers

The dashboard tracks a shipping cost per order window.
`[?]` Which courier(s), who pays (built into price vs. charged separately), COD charges.

### 4.4 Operating costs

- **Shopify subscription** (Basic plan) `[?]` billed in USD or INR.
- **Marketing**: Meta Ads account exists `[?]` current spend level and payment method.
- **Infrastructure**: one VPS (Labs OS), domain, `[?]` provider and billing.
- `[?]` Anything else recurring (tools, AI/API credits, WhatsApp Business, etc.).

## 5. Margin picture (how the Ledger computes it)

True landed cost per watch = **USD invoice total × effective rate (₹100)** + **per-watch
consignment freight**. Margin = selling price − landed cost − outbound shipping − channel
costs. The Ledger deliberately splits "goods at market rate" from "cost of banking" so the
banking overhead stays visible.

## 6. Refunds, cancellations, edge cases `[?]`

- `[?]` Refund policy and mechanics (gateway reversal / UPI transfer back).
- `[?]` What happens to a cancelled order after the supplier has bought parts (specs lock
  once paid).
- `[?]` Warranty/replacement cost handling.

## 7. Draft money-flow diagram

```mermaid
flowchart LR
  subgraph IN["Money in — customers (India, ₹11k–27k/watch)"]
    C1[Website order\nShopify checkout] -->|"gateway [?]\nsettlement T+n"| BANK[(Business bank\naccount [?])]
    C2[WhatsApp order] -->|"UPI / transfer [?]"| BANK
    C3[Manual order form\noffline] -->|"[?]"| BANK
  end

  subgraph OUT["Money out"]
    BANK -->|"USD wire at ~₹100/USD effective\n(₹86 market + ~16% bank cost)"| SUP[Supplier — Hannan\nTimeLabsCo × Sunesra\ninvoices in USD]
    BANK -->|consignment freight\nper-watch cost| FR[Inbound shipping\n+ customs? [?]]
    BANK -->|ads| META[Meta Ads]
    BANK -->|subscription + fees| SHOP[Shopify]
    BANK -->|couriers| SHIPOUT[Outbound shipping\nto customers]
    BANK -->|VPS, domain, tools| OPS[Infrastructure]
  end

  SUP -->|builds & ships watches| FR --> CUST[Customer]
```

## 8. Open questions for the owner (answering these completes this doc)

1. **Entity & tax**: legal form and registered name; GST registered or not; who files what
   today. Is "Timelabs Inc" (the email/GitHub name) the same entity as "Timelabs Co"?
2. **Money in**: gateway behind Shopify checkout + settlement account; how WhatsApp
   customers pay and into which account; COD yes/no; advances on custom builds.
3. **Bank accounts**: which accounts touch business money (business/personal, bank names —
   no numbers needed), so the CA knows which statements matter.
4. **Supplier & imports**: payment channel for the USD invoices; where goods ship from;
   customs/IGST treatment on inbound consignments; the commercial arrangement with
   Hannan/Sunesra (pure vendor, or revenue/profit share?).
5. **Recurring costs**: ads spend, subscriptions, and anything paid personally on behalf of
   the business.

---

*When the owner explains the workflow, update this file (facts replace `[?]` markers), keep
the diagram in sync, and regenerate the shareable CA version (PDF, hosted on the domain).*
