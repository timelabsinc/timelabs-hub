#!/usr/bin/env python3
"""Shared Google Drive + Sheets access layer.

Stdlib-only (urllib) so the agent server on system python can import it
directly, the same constraint shopify_api.py works under.

Rides on the Google OAuth connection Drop already owns: client at
/root/oauth-client.json, refresh token at .gdrive-token.json. Needs the
spreadsheets scope, which /drop/api/gdrive/connect requests as of 2026-07-22 —
a token minted before that predates the scope, so Sheets calls fail until the
owner reconnects Drive once.
"""
import json
import mimetypes
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

OAUTH_CLIENT = "/root/oauth-client.json"
TOKEN_PATH = "/root/ops-dashboard/.gdrive-token.json"
STATE_PATH = "/root/ops-dashboard/.orders-sheet.json"

ORDER_FOLDER_NAME = "Labs OS Order Photos"
ORDER_SHEET_NAME = "Labs OS — Orders"
# Column A is the orders.id — the join key the Shopify sync and any future
# sheet→db read both rely on.
ORDERS_TAB = "Orders"
CUSTOMERS_TAB = "Customers"
# Status and Source sit right after Logged (not buried near the end) because
# those are what a pivot table would filter or group by first.
SHEET_HEADERS = ["Order #", "Logged", "Status", "Source", "Customer", "Phone",
                 "Email", "Address", "City", "State", "Pincode", "Product",
                 "Case style", "Dial colour", "Dial style", "Case colour",
                 "Movement", "Size", "Qty", "Price (INR)", "Line total (INR)",
                 "Notes", "Photos"]


def row_for(headers, data):
    """Map a {header: value} dict onto a plain row in `headers` order.

    Every sheet-writing call site builds a dict and goes through this instead
    of hand-writing a positional list — a positional list silently misaligns
    the instant SHEET_HEADERS gains, loses or reorders a column, and nothing
    would catch it (the row would just land in the wrong cells). Any header
    with no matching key just writes blank, so old and new callers can pass
    a partial dict safely.
    """
    return [data.get(h, "") for h in headers]

RECONNECT_HINT = ("Google isn't connected with permission to write Sheets. "
                  "Open Drop, tap the cloud, and reconnect Google.")

_lock = threading.Lock()


class GoogleError(Exception):
    pass


def _client():
    try:
        d = json.load(open(OAUTH_CLIENT))
        c = d.get("web") or d.get("installed") or {}
        return c.get("client_id"), c.get("client_secret")
    except (OSError, json.JSONDecodeError):
        return None, None


def configured():
    return bool(_client()[0])


def access_token():
    """A valid access token, refreshed via the stored refresh token."""
    try:
        tok = json.load(open(TOKEN_PATH))
    except (OSError, json.JSONDecodeError):
        return None
    if tok.get("exp", 0) > time.time() + 60:
        return tok.get("access_token")
    cid, csec = _client()
    if not cid or not tok.get("refresh_token"):
        return None
    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": csec,
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            fresh = json.loads(r.read())
    except Exception as e:
        print(f"[google] refresh failed: {e}", flush=True)
        return None
    tok["access_token"] = fresh["access_token"]
    tok["exp"] = time.time() + fresh.get("expires_in", 3500)
    with open(TOKEN_PATH, "w") as f:
        json.dump(tok, f)
    os.chmod(TOKEN_PATH, 0o600)
    return tok["access_token"]


def connected():
    return access_token() is not None


def _call(url, access, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {access}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        low = msg.lower()
        if e.code in (401, 403) and ("insufficient" in low or "scope" in low
                                     or "permission" in low):
            raise GoogleError(RECONNECT_HINT)
        if e.code == 403 and ("has not been used" in msg or "disabled" in msg):
            raise GoogleError("The Google Sheets API isn't switched on for this "
                              "project yet — enable it in the Google Cloud console, "
                              "wait a couple of minutes, then try again.")
        raise GoogleError(msg[:180] or f"Google API error {e.code}")
    except Exception as e:
        raise GoogleError(str(e))


# ------------------------------------------------------------------ state file
def _state():
    try:
        return json.load(open(STATE_PATH))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(st):
    with open(STATE_PATH, "w") as f:
        json.dump(st, f)
    os.chmod(STATE_PATH, 0o600)


def sheet_url():
    sid = _state().get("sheet_id")
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else None


# ----------------------------------------------------------------------- drive
def _mkfolder(access, name):
    out = _call("https://www.googleapis.com/drive/v3/files?fields=id", access,
                "POST", {"name": name,
                         "mimeType": "application/vnd.google-apps.folder"})
    return out.get("id")


def ensure_photo_folder(access):
    """Drive folder for order photos, created once and remembered."""
    with _lock:
        st = _state()
        if st.get("folder_id"):
            return st["folder_id"]
        fid = _mkfolder(access, ORDER_FOLDER_NAME)
        st["folder_id"] = fid
        _save_state(st)
        return fid


def upload_photo(access, name, local_path):
    """Multipart-upload one file into the order-photos folder. Returns a link."""
    parent = ensure_photo_folder(access)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        data = open(local_path, "rb").read()
    except OSError as e:
        raise GoogleError(str(e))
    meta = {"name": name, "parents": [parent]}
    boundary = "labs" + secrets.token_hex(12)
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + json.dumps(meta).encode()
        + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode()
        + data + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {access}",
                 "Content-Type": f"multipart/related; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            fid = json.loads(r.read()).get("id")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        low = msg.lower()
        if e.code in (401, 403) and ("insufficient" in low or "scope" in low
                                     or "permission" in low):
            raise GoogleError(RECONNECT_HINT)
        raise GoogleError(msg[:180] or f"Drive upload error {e.code}")
    except Exception as e:
        raise GoogleError(str(e))
    return f"https://drive.google.com/file/d/{fid}/view"


# ---------------------------------------------------------------------- sheets
def _meta(access, sid):
    return _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
                 "?fields=sheets.properties", access)


def _values(access, sid, rng):
    out = _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
                + urllib.parse.quote(rng), access)
    return out.get("values", [])


def _clean(row):
    return [("" if c is None else str(c)) for c in row]


def ensure_order_sheet(access):
    """The mirror spreadsheet, created once and remembered."""
    with _lock:
        st = _state()
        if st.get("sheet_id"):
            return st["sheet_id"]
        out = _call("https://sheets.googleapis.com/v4/spreadsheets", access, "POST",
                    {"properties": {"title": ORDER_SHEET_NAME},
                     "sheets": [{"properties": {"title": ORDERS_TAB}}]})
        sid = out.get("spreadsheetId")
        if not sid:
            raise GoogleError("Sheets API did not return a spreadsheet id")
        st["sheet_id"] = sid
        _save_state(st)
        return sid


def ensure_tab(access, sid, title, headers):
    """Create the tab if missing and keep its header row current.

    Headers are only rewritten while the tab has no data rows — once orders are
    in there, silently reshuffling columns would misalign every existing row.
    """
    props = {s["properties"]["title"]: s["properties"]
             for s in _meta(access, sid).get("sheets", [])}
    if title not in props:
        # a brand-new spreadsheet arrives with a default "Sheet1" — reuse it
        if "Sheet1" in props and len(props) == 1 and not _values(access, sid, "Sheet1!A2:A2"):
            _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
                  access, "POST", {"requests": [{"updateSheetProperties": {
                      "properties": {"sheetId": props["Sheet1"]["sheetId"], "title": title},
                      "fields": "title"}}]})
        else:
            _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
                  access, "POST", {"requests": [{"addSheet": {
                      "properties": {"title": title}}}]})
        props = {s["properties"]["title"]: s["properties"]
                 for s in _meta(access, sid).get("sheets", [])}

    existing = _values(access, sid, f"{title}!A1:ZZ1")
    current = existing[0] if existing else []
    if current != headers:
        has_data = bool(_values(access, sid, f"{title}!A2:A2"))
        if not has_data or len(current) < len(headers):
            _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
                  + urllib.parse.quote(f"{title}!A1") + "?valueInputOption=RAW",
                  access, "PUT", {"values": [headers]})
            _freeze_header(access, sid, props[title]["sheetId"])
    return props[title]["sheetId"]


def _freeze_header(access, sid, tab_id):
    try:
        _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
              access, "POST", {"requests": [
                  {"updateSheetProperties": {
                      "properties": {"sheetId": tab_id,
                                     "gridProperties": {"frozenRowCount": 1}},
                      "fields": "gridProperties.frozenRowCount"}},
                  {"repeatCell": {
                      "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
                      "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                      "fields": "userEnteredFormat.textFormat.bold"}}]})
    except GoogleError:
        pass   # cosmetic only


def append_order_row(access, row):
    sid = ensure_order_sheet(access)
    ensure_tab(access, sid, ORDERS_TAB, SHEET_HEADERS)
    _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
          + urllib.parse.quote(f"{ORDERS_TAB}!A:A") + ":append"
          "?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
          access, "POST", {"values": [_clean(row)]})
    return sid


def upsert_customer_row(access, headers, row):
    """Update the customer's existing row (matched on the id in column A) or
    append a new one — so the Customers tab stays one row per person."""
    sid = ensure_order_sheet(access)
    ensure_tab(access, sid, CUSTOMERS_TAB, headers)
    key = str(row[0])
    col = _values(access, sid, f"{CUSTOMERS_TAB}!A:A")
    at = None
    for i, r in enumerate(col):
        if r and str(r[0]).strip() == key:
            at = i + 1     # 1-based, header included
            break
    if at:
        _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
              + urllib.parse.quote(f"{CUSTOMERS_TAB}!A{at}")
              + "?valueInputOption=USER_ENTERED",
              access, "PUT", {"values": [_clean(row)]})
    else:
        _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
              + urllib.parse.quote(f"{CUSTOMERS_TAB}!A:A") + ":append"
              "?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
              access, "POST", {"values": [_clean(row)]})
    return sid


def update_order_field(access, order_no, header, value):
    """Patch one column of one order's row in the Orders tab, matched on the
    Order # in column A. Best-effort: an order that never reached the sheet
    (bulk/WhatsApp/Shopify rows often haven't) simply isn't found and nothing
    happens — a stage change must never fail because the mirror is behind.
    """
    sid = _state().get("sheet_id")
    if not sid or header not in SHEET_HEADERS:
        return False
    col = _values(access, sid, f"{ORDERS_TAB}!A:A")
    key = str(order_no)
    at = None
    for i, r in enumerate(col):
        if r and str(r[0]).strip() == key:
            at = i + 1
            break
    if not at:
        return False
    a1col = chr(ord("A") + SHEET_HEADERS.index(header))   # <=26 cols, fine
    _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
          + urllib.parse.quote(f"{ORDERS_TAB}!{a1col}{at}")
          + "?valueInputOption=USER_ENTERED",
          access, "PUT", {"values": [[value]]})
    return True


# ------------------------------------------------------------- sheet reshape
BACKUP_DIR = "/root/ops-dashboard/.sheet-backups"


def _tab_id(access, sid, title):
    for s in _meta(access, sid).get("sheets", []):
        if s["properties"]["title"] == title:
            return s["properties"]["sheetId"]
    return None


def _format_tab(access, sid, tab_id, headers, currency_cols=(), date_cols=()):
    """Freeze the header (+ first column), bold the header, add a filter over
    the whole range, and typed number formats — cosmetic only, so a failure
    here never loses data that's already been written."""
    if tab_id is None:
        return
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": tab_id, "gridProperties": {
            "frozenRowCount": 1, "frozenColumnCount": 1}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
        {"repeatCell": {"range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold"}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": tab_id, "startRowIndex": 0,
            "endColumnIndex": len(headers)}}}},
    ]
    for name in currency_cols:
        if name not in headers:
            continue
        c = headers.index(name)
        reqs.append({"repeatCell": {"range": {"sheetId": tab_id, "startColumnIndex": c,
            "endColumnIndex": c + 1, "startRowIndex": 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY",
                     "pattern": "₹#,##0.00"}}},
            "fields": "userEnteredFormat.numberFormat"}})
    for name in date_cols:
        if name not in headers:
            continue
        c = headers.index(name)
        reqs.append({"repeatCell": {"range": {"sheetId": tab_id, "startColumnIndex": c,
            "endColumnIndex": c + 1, "startRowIndex": 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE_TIME",
                     "pattern": "yyyy-mm-dd hh:mm"}}},
            "fields": "userEnteredFormat.numberFormat"}})
    try:
        _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate", access,
              "POST", {"requests": reqs})
    except GoogleError as e:
        print(f"[sheets] formatting skipped: {e}", flush=True)


def _reshape_tab(access, sid, title, new_headers, id_aliases=None):
    """Remap every existing row from whatever headers the tab currently has
    onto new_headers, matched by NAME not position — the only safe way to
    reorder/add columns on a tab that already has real rows in it (a plain
    header rewrite would leave the data misaligned under the new labels,
    which is exactly the corruption ensure_tab() already refuses to risk).

    Backs up the pre-reshape values to a local JSON file first, on top of
    whatever version history Sheets itself keeps, and is a no-op (besides
    formatting) if the tab already has the current headers — safe to call on
    every deploy, not just once.
    """
    tab_id = _tab_id(access, sid, title)
    values = _values(access, sid, f"{title}!A1:ZZ100000")
    if not values:
        ensure_tab(access, sid, title, new_headers)
        return 0
    old_headers, old_rows = values[0], values[1:]
    if old_headers == new_headers:
        return len(old_rows)

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = int(time.time())
    with open(f"{BACKUP_DIR}/{title.lower()}-{stamp}.json", "w") as f:
        json.dump({"headers": old_headers, "rows": old_rows}, f)

    remapped = []
    for row in old_rows:
        d = {old_headers[i]: (row[i] if i < len(row) else "")
             for i in range(len(old_headers))}
        for old_name, new_name in (id_aliases or {}).items():
            if old_name in d and not d.get(new_name):
                d[new_name] = d[old_name]
        remapped.append(row_for(new_headers, d))

    _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
          + urllib.parse.quote(f"{title}!A1:ZZ100000") + ":clear", access, "POST", {})
    _call(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
          + urllib.parse.quote(f"{title}!A1") + "?valueInputOption=USER_ENTERED",
          access, "PUT", {"values": [new_headers] + remapped})
    return len(remapped)


def migrate_orders_tab(access):
    """Reorder/reformat the Orders tab into the current SHEET_HEADERS. Safe
    to run any time, including on a tab that predates this shipping."""
    sid = ensure_order_sheet(access)
    n = _reshape_tab(access, sid, ORDERS_TAB, SHEET_HEADERS,
                     id_aliases={"Order ID": "Order #"})
    _format_tab(access, sid, _tab_id(access, sid, ORDERS_TAB), SHEET_HEADERS,
               currency_cols=("Price (INR)", "Line total (INR)"), date_cols=("Logged",))
    return {"rows": n}


def migrate_customers_tab(access):
    """Reorder/reformat the Customers tab into customers.HEADERS."""
    import customers as customers_mod
    sid = ensure_order_sheet(access)
    n = _reshape_tab(access, sid, CUSTOMERS_TAB, customers_mod.HEADERS)
    _format_tab(access, sid, _tab_id(access, sid, CUSTOMERS_TAB), customers_mod.HEADERS,
               currency_cols=("Total spent", "Avg order value"),
               date_cols=("First order", "Last order"))
    return {"rows": n}
