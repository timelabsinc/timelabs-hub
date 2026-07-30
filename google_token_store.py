#!/usr/bin/env python3
"""Atomic, cross-process access to the shared Google OAuth credential.

Drop, Sheets/order mirroring, the Shopify order sync, and nightly backup all
use the same refresh token.  Refreshing independently lets two processes
overwrite one another or truncate the only credential during an in-place
write, so every reader/refresher/writer goes through this small stdlib module.
"""
import fcntl
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request

TOKEN_PATH = "/root/ops-dashboard/.gdrive-token.json"
OAUTH_CLIENT = "/root/oauth-client.json"
LOCK_PATH = "/root/ops-dashboard/data/.gdrive-token.lock"


def _client():
    try:
        with open(OAUTH_CLIENT) as f:
            raw = json.load(f)
        client = raw.get("web") or raw.get("installed") or {}
        return client.get("client_id"), client.get("client_secret")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None, None


def _read_unlocked():
    try:
        with open(TOKEN_PATH) as f:
            token = json.load(f)
        return token if isinstance(token, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_unlocked(token):
    directory = os.path.dirname(TOKEN_PATH)
    fd, tmp = tempfile.mkstemp(
        prefix=".gdrive-token.", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(token, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, TOKEN_PATH)
        os.chmod(TOKEN_PATH, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_token(token):
    """Atomically replace the shared token after an explicit OAuth callback."""
    if not isinstance(token, dict):
        raise ValueError("Google token must be an object")
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _write_unlocked(token)


def access_token(log_prefix="[google]"):
    """Return a valid access token, refreshing exactly once across processes."""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        token = _read_unlocked()
        if not token:
            return None
        if token.get("exp", 0) > time.time() + 60:
            return token.get("access_token")
        client_id, client_secret = _client()
        if not client_id or not client_secret or not token.get("refresh_token"):
            return None
        body = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/token", data=body)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                fresh = json.loads(response.read())
            access = fresh.get("access_token")
            if not access:
                raise ValueError("refresh response had no access token")
            token["access_token"] = access
            token["exp"] = time.time() + fresh.get("expires_in", 3500)
            _write_unlocked(token)
            return access
        except Exception as exc:
            # Never include token/client material in logs.
            print(f"{log_prefix} refresh failed: {exc}", flush=True)
            return None
