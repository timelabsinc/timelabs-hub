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
# Column A is the orders.id — the join key a future sheet→db sync needs.
ORDERS_TAB = "Orders"
CUSTOMERS_TAB = "Customers"
SHEET_HEADERS = ["Order ID", "Logged", "Source", "Customer", "Phone", "Email",
                 "Address", "City", "State", "Pincode", "Product", "Case style",
                 "Dial colour", "Dial style", "Case colour", "Movement", "Size",
                 "Qty", "Price (INR)", "Notes", "Status", "Photos"]

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
