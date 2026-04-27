#!/usr/bin/env python3
"""KukuTV mitmproxy addon — captures all HTTPS traffic."""
import json, os
from datetime import datetime, timezone
from mitmproxy import http

KUKU = ["kukufm", "kuku.fm", "aravali", "vlv.com"]
OUT  = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "metadata", "captured_apis", "api_traffic.jsonl")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

def response(flow: http.HTTPFlow):
    url  = flow.request.pretty_url
    host = flow.request.pretty_host
    is_kuku = any(k in url or k in host for k in KUKU)

    try: body = json.loads(flow.request.get_text(strict=False) or "")
    except: body = flow.request.get_text(strict=False) or None

    try: rbody = json.loads(flow.response.get_text(strict=False) or "")
    except: rbody = None

    with open(OUT, "a") as f:
        f.write(json.dumps({
            "ts":       datetime.now(timezone.utc).isoformat(),
            "method":   flow.request.method,
            "url":      url,
            "host":     host,
            "path":     flow.request.path.split("?")[0],
            "req_hdrs": dict(flow.request.headers),
            "req_body": body,
            "status":   flow.response.status_code if flow.response else None,
            "res_hdrs": dict(flow.response.headers) if flow.response else {},
            "res_body": rbody,
            "is_kuku":  is_kuku,
        }) + "\n")

    if is_kuku:
        print(f"[KUKU] {flow.request.method} {url[:120]}")

