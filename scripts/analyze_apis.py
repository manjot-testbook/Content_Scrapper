"""
analyze_apis.py — Parse the captured MITM traffic and produce a summary
of all discovered KukuTV API endpoints, auth patterns, and video URLs.
"""

import json
import os
import sys
from collections import defaultdict
from urllib.parse import parse_qs

from rich.console import Console
from rich.table import Table

console = Console()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAFFIC_LOG = os.path.join(PROJECT_ROOT, "metadata", "captured_apis", "api_traffic.jsonl")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "metadata", "api_catalog")


def load_traffic() -> list[dict]:
    """Load all captured traffic records."""
    if not os.path.isfile(TRAFFIC_LOG):
        console.print(f"[red]Traffic log not found: {TRAFFIC_LOG}[/red]")
        console.print("Run start_proxy.py and appium_navigator.py first.")
        sys.exit(1)

    records = []
    with open(TRAFFIC_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def analyze(records: list[dict]):
    """Analyze traffic and extract API patterns."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Group by host
    by_host: dict[str, list] = defaultdict(list)
    for r in records:
        by_host[r.get("host", "unknown")].append(r)

    # Identify KukuTV hosts
    kuku_hosts = {h for h in by_host if any(kw in (h or "").lower() for kw in ["kuku", "kukufm", "kukutv"])}
    all_hosts = set(by_host.keys())

    # === Summary Table ===
    console.print(f"\n[bold]API Traffic Analysis[/bold]")
    console.print(f"Total requests: {len(records)}")
    console.print(f"Unique hosts: {len(all_hosts)}")
    console.print(f"KukuTV hosts: {kuku_hosts or 'None identified'}\n")

    table = Table(title="Requests by Host")
    table.add_column("Host", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("KukuTV?", justify="center")
    for host in sorted(by_host, key=lambda h: len(by_host[h]), reverse=True)[:30]:
        is_kuku = "✅" if host in kuku_hosts else ""
        table.add_row(host or "—", str(len(by_host[host])), is_kuku)
    console.print(table)

    # === Endpoint Catalog ===
    endpoints: dict[str, dict] = {}
    for r in records:
        if not r.get("is_kukutv"):
            continue
        sig = f"{r['method']} {r.get('path', '/')}"
        if sig not in endpoints:
            endpoints[sig] = {
                "method": r["method"],
                "host": r.get("host"),
                "path": r.get("path"),
                "example_url": r.get("url"),
                "status_codes": set(),
                "content_types": set(),
                "auth_headers": {},
                "query_params": [],
                "sample_response": None,
                "count": 0,
            }
        ep = endpoints[sig]
        ep["count"] += 1
        if r.get("status_code"):
            ep["status_codes"].add(r["status_code"])
        ct = r.get("content_type", "")
        if ct:
            ep["content_types"].add(ct.split(";")[0].strip())

        # Extract auth headers
        req_headers = r.get("request_headers", {})
        for key in ["Authorization", "authorization", "x-api-key", "X-Api-Key", "Cookie", "x-auth-token"]:
            if key in req_headers:
                ep["auth_headers"][key] = req_headers[key]

        # Capture query params
        if r.get("query"):
            ep["query_params"].append(parse_qs(r["query"]))

        # Keep first successful response as sample
        if ep["sample_response"] is None and r.get("status_code") == 200:
            ep["sample_response"] = r.get("response_body")

    # Print endpoint catalog
    if endpoints:
        console.print(f"\n[bold]KukuTV API Endpoints ({len(endpoints)})[/bold]\n")
        ep_table = Table(title="Discovered Endpoints")
        ep_table.add_column("Method", style="bold")
        ep_table.add_column("Path", style="cyan")
        ep_table.add_column("Status", style="green")
        ep_table.add_column("Count", justify="right")
        ep_table.add_column("Auth?", justify="center")

        for sig in sorted(endpoints):
            ep = endpoints[sig]
            statuses = ",".join(str(s) for s in sorted(ep["status_codes"]))
            has_auth = "🔑" if ep["auth_headers"] else ""
            ep_table.add_row(ep["method"], ep["path"], statuses, str(ep["count"]), has_auth)
        console.print(ep_table)

    # === Detect Video/Stream URLs ===
    video_urls = []
    for r in records:
        url = r.get("url", "")
        ct = r.get("content_type", "")
        if any(ext in url.lower() for ext in [".m3u8", ".mpd", ".mp4", ".ts", ".mp3"]):
            video_urls.append({"url": url, "content_type": ct, "status": r.get("status_code")})
        elif any(t in ct for t in ["video/", "audio/", "application/vnd.apple.mpegurl", "application/dash+xml"]):
            video_urls.append({"url": url, "content_type": ct, "status": r.get("status_code")})

    if video_urls:
        console.print(f"\n[bold]Video/Stream URLs ({len(video_urls)})[/bold]\n")
        for v in video_urls[:20]:
            console.print(f"  [green]{v['status']}[/green] {v['content_type']}")
            console.print(f"    {v['url'][:150]}")

    # === Save catalog ===
    catalog = {
        "summary": {
            "total_requests": len(records),
            "unique_hosts": len(all_hosts),
            "kuku_hosts": list(kuku_hosts),
            "kuku_endpoints": len(endpoints),
            "video_urls_found": len(video_urls),
        },
        "endpoints": {
            sig: {
                **ep,
                "status_codes": list(ep["status_codes"]),
                "content_types": list(ep["content_types"]),
                "query_params": ep["query_params"][:3],  # Keep just a few samples
            }
            for sig, ep in endpoints.items()
        },
        "video_urls": video_urls,
    }

    catalog_path = os.path.join(OUTPUT_DIR, "api_catalog.json")
    with open(catalog_path, "w") as f:
        json.dump(catalog, f, indent=2, default=str)
    console.print(f"\n[green]✓ Catalog saved to {catalog_path}[/green]")

    # Save video URLs separately for the scraper
    if video_urls:
        video_urls_path = os.path.join(OUTPUT_DIR, "video_urls.json")
        with open(video_urls_path, "w") as f:
            json.dump(video_urls, f, indent=2)
        console.print(f"[green]✓ Video URLs saved to {video_urls_path}[/green]")


def main():
    records = load_traffic()
    analyze(records)


if __name__ == "__main__":
    main()
