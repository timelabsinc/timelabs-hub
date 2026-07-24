# Labs OS — Business Context & Tool Reference

*Timelabs Co · ops.timelabsco.in · last updated 2026-07-24*

A single self-hosted operating system for a Shopify DTC watch-mod brand. This file is the
plain-text context for the whole business system — safe to hand to another AI session or a
second machine to bring it up to speed. It contains **no credentials** (only where they live).

---

## 1. The business

- **Timelabs Co** — direct-to-consumer brand selling **Seiko watch-mod parts and complete
  custom builds**. Market: **India**. Storefront: Shopify (`xd2fwj-1h.myshopify.com`).
- Products are homage/mod builds (Datejust, Royal Oak, Nautilus, Aquanaut styles etc.) built
  on Seiko movements (NH35 and similar), typically ₹11,000–₹25,000.
- Orders come through **three channels**: the Shopify website, WhatsApp messages, and a
  manual order form. One supplier (partner: **Hannan**, TimeLabsCo × Sunesra) builds the
  watches; the historical method was sharing a screenshot of the watch + an order number in a
  WhatsApp group.

## 2. What Labs OS is

A browser-only business OS running on **one VPS**. It unifies sales, orders, files, supplier
fulfilment, store content, and an AI assistant that can act on all of it. Signed in with
**Google (SSO)**; what each person can do is decided by their **role** and enforced by the
server, not merely hidden in the UI.

Design language is **"Meridian"** — clean, modern, light + dark, gold accent (#996c1f /
#d8a94c), no gauges or gimmicks.

---

## 3. Tools (16)

### Daily
| Tool | URL | What it does |
|---|---|---|
| **Home** | `/ops/#overview` | Live dashboard — sales, funnel, headline order totals for the current window (orders / value / shipping cost / average), what's selling across all channels, service health. |
| **Drop** | `/drop/` | Files & product media — resumable chunked uploads, **AI photo search** ("black watch", "green dial"), **view-only customer share links** (block download, thumbnails only), Google Drive import. |
| **Ledger** | `/ops/ledger.html` | Costs & margins from supplier invoices, with true landed cost — USD converted at the **₹100 effective rate** (bank fees included), banking cost surfaced separately. |
| **Chat** | `/ops/agent/` | The AI assistant (Hermes / Claude) — ask about the business or have it act on the store; photos in, PDF export out. |
| **Order form** | `/ops/order-form.html` | Log an order in seconds — paste details (auto-splits name/phone/address, fills city+state, extracts watch spec), product type-ahead, editable status, What's-selling table. Includes the **Bulk add** tab for backfilling screenshots. |

### Content & growth
| Tool | URL | What it does |
|---|---|---|
| **Blog builder** | `/ops/blog.html` | Queued topics → a content plan, in the brand voice. |
| **Blog uploader** | `/ops/blog-uploader.html` | Write a post in a rich editor and publish it to the store. |
| **Content updater** | `/ops/content-updater.html` | Bulk-refresh product copy and edit store pages, with a dry-run preview. |
| **Reddit listener** | `/ops/reddit.html` | Surfaces watch-hobbyist threads worth a genuine reply — read-only, nothing auto-posts. *(Setup pending — see Roadmap.)* |

### Shopify
| Tool | URL | What it does |
|---|---|---|
| **Theme editor** | `/ops/theme-editor.html` | Describe a look → the assistant restyles the live theme → you approve the exact diff → applied, with one-click undo (every apply is snapshotted). |
| **Quick product updater** | `/ops/product-updater.html` | Change prices & status fast, in bulk. |
| **Product builder** | `/ops/product-builder.html` | Create a new product from parts, photos & a spec; the assistant can draft the copy from the photos; push live or save draft. |

### Admin (owner-only)
| Tool | URL | What it does |
|---|---|---|
| **People & access** | `/ops/access.html` | Invite people, set a role, remove — who signs in and exactly what they can do. |
| **System files** | `/ops/files.html` | Read-only browser over the server (`/root`); credential file **contents are always withheld**. |
| **System map** | `/ops/architecture.html` | How the OS fits together — services, live health, roadmap. |
| **Supplier build queue** | `/ops/supplier.html` | What the supplier sees — order number, spec, photo, status. No customer details, no price. Their own sign-in, confined to this one page. |

---

## 4. Orders & the supplier system

The largest piece of the system — turning a WhatsApp-screenshot process into one tracked,
shared workflow.

### Unified orders
- Orders from **website, WhatsApp, and the order form** all land in one database with **one
  running order number**.
- **Storefront orders sync in automatically every 5 minutes** (no manual step).
- Per-product-line analytics via an `order_items` table; product names are renamable
  retroactively for clean "what's selling" reporting.

### Supplier queue (phone-first)
- Shows each build's **reference photo, spec, quantity, status** — never customer name,
  phone, address, or price. Enforced in the query itself, not just the UI.
- **One-tap** stage advance; hold/hover a photo to enlarge; per-order **timeline**;
  supplier **notes** ("dial out of stock"); **tracking numbers**.
- **Shipments**: group builds into a consignment, enter its total cost, see **per-watch cost**
  (total ÷ count, recalculated live).
- **Bulk actions**: select several builds → set status / tracking / shipment at once.
- **Share a status**: post straight into the team WhatsApp, copy as text, or export a PDF
  (with the photo).
- **Build-request PDF**: export a selection as a photo + spec sheet the supplier can forward
  to *their* supplier — no prices.

### Backfill
- **Bulk add** tab in the order form: drop a batch of watch screenshots → each becomes a
  build, carrying its **original order number** (so the supplier still sees "Order 101"). No
  customer or price needed; add later on anything that sells.

### Build pipeline
`new → acknowledged → paid → in transit → assembled → shipped → delivered` (+ `cancelled`)

- "Acknowledged" and "paid" are deliberately **separate** — the two states that used to get
  lost.
- Build details **lock once paid**: the supplier may have bought parts, so the spec can't
  change underneath them. Status, notes and tracking stay editable.

---

## 5. Access roles

| Role | Lands on | Can use |
|---|---|---|
| **admin** | Home | Everything, incl. access control, system files, the map |
| **full** | Home | Every day-to-day tool; no admin |
| **orders** | Order form | Order form + order/customer lists only |
| **intake** | Intake form | Log new orders and nothing else |
| **supplier** | Supplier queue | The build queue only — no PII, no price |

Enforced in **two layers**: nginx bounces a restricted role off any page it doesn't own, and
every data endpoint independently checks the caller's role (so the API can't be called
directly to bypass the page gate).

---

## 6. Architecture (for extending it)

**Generator pattern (core rule):** every page is server-rendered by a Python generator that
imports the shared shell (`hub_shell.py`). **Never hand-edit `/var/www/ops/*.html`** — edit
the generator and re-run. Each tool = a `<name>.py` generator, registered in `tools.py` and
in `generate.py`'s refresh loop.

**Services**
- **nginx** (:443 TLS) — front door, routes `/ops/`, `/drop/`, public share links `/s/`.
- **oauth2-proxy** — Google SSO + per-person allow-list.
- **agent_chat_server.py** (:8901) — the app API + AI backend (system Python; stdlib-only
  shared layers so it can import them).
- **drop_server.py** (:8903) — uploads, thumbnails, share links, AI photo search.
- **ops-order-sync.timer** — Shopify → local order sync, every 5 min.
- **Daily dashboard refresh** timer — rebuilds Home.

**Data**
- `hermes.db` — orders, order_items, order_events, shipments, customers, products,
  memory_facts, content, and **hub_events** (a live activity feed across the whole OS).
- `suppliers.db` — supplier invoices & costs (Ledger).

**Shared layers & credentials**
- `shopify_api.py` (Shopify Admin GraphQL + REST) and `google_api.py` (Drive + Sheets) are
  reused by every tool **and** the `labs` command-line tool — so nothing needs a second key
  issued. All credentials live in one `.env` (referenced here, never exposed).
- `labs` CLI: run the whole OS from a terminal; every write is a **dry run until `--execute`**.

---

## 7. Roadmap

- **Reddit listener** — `/ops/reddit.html`, scaffolding built 2026-07-24. Read-only: surfaces
  watch-hobbyist threads worth a genuine reply, tagged (buying intent / question / complaint /
  style) and ranked. Schema, endpoints and UI are all live; blocked only on Reddit's manual app
  approval (self-service closed Nov 2025 — a Developer Support ticket at support.reddithelp.com
  still needs to be submitted, ~7 day review once it is). Drop the client_id/secret into `.env`
  and Sync goes live with no further build.
  Phase 2 (draft-and-approve replies, human-approved, never auto-posted) is scoped but not
  built — see `reddit_drafts` table.
- **Google Sheets mirror** — order & customer tabs, reordered and auto-formatted; written and
  tested, waiting on Drive being reconnected.
- **Scheduling & automation** — moving from an assistant that acts when asked to one that acts
  on a schedule.

---

*This document is generated as a business reference. It deliberately omits security internals
(SSH, firewall, credential values). For those, see the on-server admin context file.*
