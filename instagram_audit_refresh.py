#!/usr/bin/env python3
"""Refresh a sanitized, read-only Instagram snapshot through Composio MCP."""
import json
import os
import time
import uuid
import urllib.request
from pathlib import Path

BASE = "https://connect.composio.dev/mcp"
TOKEN_FILE = Path("/root/.hermes/mcp-tokens/composio.json")
SNAPSHOT = Path("/root/ops-dashboard/data/instagram_audit_snapshot.json")

def rpc(token, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}).encode()
    req = urllib.request.Request(BASE, data=body, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read().decode()
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise RuntimeError("Composio returned no JSON-RPC response")

def text_result(result):
    return json.loads(result["result"]["content"][0]["text"])

def main():
    token = json.loads(TOKEN_FILE.read_text())["access_token"]
    rpc(token, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "labs-instagram-audit", "version": "1.0"}})
    search = text_result(rpc(token, "tools/call", {"name": "COMPOSIO_SEARCH_TOOLS", "arguments": {"queries": [{"use_case": "read-only audit of our connected Instagram Business account profile, recent media and account insights"}], "session": {"generate_id": True}, "model": "gpt-5.2"}}))["data"]
    session_id = search["session"]["id"]
    now = int(time.time())
    since = now - 90 * 86400
    calls = [
        {"tool_slug": "INSTAGRAM_GET_USER_INFO", "arguments": {"ig_user_id": "me"}},
        {"tool_slug": "INSTAGRAM_GET_USER_INSIGHTS", "arguments": {"since": since, "until": now, "metric": ["reach", "follower_count"], "period": "day", "metric_type": "time_series"}},
        {"tool_slug": "INSTAGRAM_GET_IG_USER_MEDIA", "arguments": {"ig_user_id": "me", "limit": 100, "since": since, "until": now, "fields": "id,caption,media_type,media_product_type,permalink,timestamp"}},
    ]
    out = text_result(rpc(token, "tools/call", {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": {"tools": calls, "thought": "Read-only Instagram audit; return profile, account reach and recent media.", "sync_response_to_workbench": False, "current_step": "FETCHING_ACCOUNT_SNAPSHOT", "current_step_metric": "0/3 calls", "session_id": session_id}}))["data"]["results"]
    info = out[0]["response"]["data"]
    insights = out[1]["response"]["data"].get("data", [])
    media = out[2]["response"]["data"].get("data", [])
    reach_sum = sum(v.get("value", 0) for m in insights if m.get("name") == "reach" for v in m.get("values", []))
    posts = []
    for item in media:
        caption = (item.get("caption") or "").splitlines()[0].strip()
        posts.append({"id": item.get("id", ""), "date": item.get("timestamp", "")[:10], "kind": "Reel" if item.get("media_product_type") == "REELS" else "Carousel" if item.get("media_type") == "CAROUSEL_ALBUM" else "Post", "title": caption[:90] or item.get("media_product_type", "Instagram media"), "views": 0, "reach": 0, "interactions": 0, "total_interactions": 0, "saves": 0, "shares": 0, "comments": 0, "likes": 0, "url": item.get("permalink", "")})
    insight_calls = [{"tool_slug": "INSTAGRAM_GET_IG_MEDIA_INSIGHTS", "arguments": {"ig_media_id": p["id"], "metric": ["views", "reach", "saved", "likes", "comments", "shares", "total_interactions"]}} for p in posts if p["id"]]
    if insight_calls:
        insight_result = text_result(rpc(token, "tools/call", {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": {"tools": insight_calls, "thought": "Read-only per-media Instagram performance metrics.", "sync_response_to_workbench": False, "current_step": "FETCHING_MEDIA_INSIGHTS", "current_step_metric": "0/%d calls" % len(insight_calls), "session_id": session_id}}))["data"]["results"]
        for post, result in zip(posts, insight_result):
            for metric in result.get("response", {}).get("data", {}).get("data", []):
                values = metric.get("values") or []
                if values:
                    key = "saves" if metric.get("name") == "saved" else "interactions" if metric.get("name") == "total_interactions" else metric.get("name")
                    if key in post:
                        post[key] = values[-1].get("value", 0)
            post.pop("id", None)
    snapshot = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "account": {"username": info.get("username", "timelabs.co"), "name": info.get("name", ""), "followers": info.get("followers_count", 0), "media_count": info.get("media_count", 0), "window": time.strftime("%d %b %Y", time.gmtime(since)) + "–" + time.strftime("%d %b %Y", time.gmtime(now)), "reach_sum": reach_sum}, "posts": posts}
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    os.replace(tmp, SNAPSHOT)
    import instagram_audit
    instagram_audit.build()
    print("refreshed sanitized Instagram snapshot")

if __name__ == "__main__":
    main()
