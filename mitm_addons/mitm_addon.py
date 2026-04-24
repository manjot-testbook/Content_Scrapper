"""
mitm_addon.py — mitmproxy addon that captures all HTTP(S) requests/responses
and saves them to a structured JSON log for API analysis.

Usage:
    mitmdump -s mitm_addons/mitm_addon.py --set confdir=~/.mitmproxy
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from mitmproxy import http, ctx

# Output directory
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metadata", "captured_apis")
os.makedirs(LOG_DIR, exist_ok=True)

# Master log file
MASTER_LOG = os.path.join(LOG_DIR, "api_traffic.jsonl")

# Track unique endpoints
seen_endpoints: set[str] = set()


class KukuTVCapture:
    """Capture and log all API traffic, with special attention to KukuTV domains."""

    def __init__(self):
        self.request_count = 0
        self.kukutv_count = 0

    def response(self, flow: http.HTTPFlow) -> None:
        self.request_count += 1

        request = flow.request
        response = flow.response
        parsed = urlparse(request.pretty_url)

        # Build record
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "url": request.pretty_url,
            "host": parsed.hostname,
            "path": parsed.path,
            "query": parsed.query,
            "request_headers": dict(request.headers),
            "request_body": None,
            "status_code": response.status_code if response else None,
            "response_headers": dict(response.headers) if response else None,
            "response_body": None,
            "content_type": response.headers.get("content-type", "") if response else "",
        }

        # Capture request body for POST/PUT/PATCH
        if request.method in ("POST", "PUT", "PATCH") and request.content:
            try:
                record["request_body"] = json.loads(request.content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                record["request_body"] = request.content.decode("utf-8", errors="replace")[:2000]

        # Capture response body (only JSON/text, skip binary/video)
        if response and response.content:
            ct = response.headers.get("content-type", "")
            if "json" in ct or "text" in ct or "xml" in ct:
                try:
                    record["response_body"] = json.loads(response.content)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    record["response_body"] = response.content.decode("utf-8", errors="replace")[:5000]
            elif "video" in ct or "audio" in ct or "octet-stream" in ct:
                record["response_body"] = f"<binary: {len(response.content)} bytes>"

        # Check if this is a KukuTV-related request
        is_kukutv = False
        host = (parsed.hostname or "").lower()
        if any(kw in host for kw in ["kuku", "kukutv", "kukufm"]):
            is_kukutv = True
            self.kukutv_count += 1

        record["is_kukutv"] = is_kukutv

        # Log endpoint signature for dedup tracking
        endpoint_sig = f"{request.method} {parsed.hostname}{parsed.path}"
        is_new = endpoint_sig not in seen_endpoints
        seen_endpoints.add(endpoint_sig)
        record["is_new_endpoint"] = is_new

        # Write to master JSONL log
        with open(MASTER_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

        # If it's a KukuTV API and new, also save individual response
        if is_kukutv and is_new and response:
            safe_path = parsed.path.replace("/", "_").strip("_") or "root"
            filename = f"{request.method}_{safe_path}_{response.status_code}.json"
            filepath = os.path.join(LOG_DIR, filename)
            with open(filepath, "w") as f:
                json.dump(record, f, indent=2, default=str)

        # Log to console
        marker = "🟢 KUKU" if is_kukutv else "⚪"
        new_tag = " [NEW]" if is_new else ""
        ctx.log.info(
            f"{marker}{new_tag} {request.method} {response.status_code if response else '???'} "
            f"{request.pretty_url[:120]}"
        )

    def done(self):
        ctx.log.info(f"\n{'='*60}")
        ctx.log.info(f"Total requests captured: {self.request_count}")
        ctx.log.info(f"KukuTV requests: {self.kukutv_count}")
        ctx.log.info(f"Unique endpoints: {len(seen_endpoints)}")
        ctx.log.info(f"Logs saved to: {LOG_DIR}")
        ctx.log.info(f"{'='*60}")


addons = [KukuTVCapture()]
