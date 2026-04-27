#!/usr/bin/env python3
"""Analyze captured KukuTV API traffic."""
import json, os, sys
from collections import defaultdict

OUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                   "metadata", "captured_apis", "api_traffic.jsonl")

if not os.path.isfile(OUT) or os.path.getsize(OUT) == 0:
    print("No traffic captured yet. Browse KukuTV while the proxy is running.")
    sys.exit(0)

entries   = [json.loads(l) for l in open(OUT) if l.strip()]
kuku      = [e for e in entries if e.get("is_kuku")]
endpoints = defaultdict(list)

for e in kuku:
    key = f"{e['method']} {e['path']}"
    endpoints[key].append(e)

print(f"\n{'='*60}")
print(f"  KukuTV API Analysis")
print(f"{'='*60}")
print(f"  Total requests : {len(entries)}")
print(f"  KukuTV requests: {len(kuku)}")
print(f"  Unique endpoints: {len(endpoints)}")
print(f"{'='*60}\n")

for endpoint, calls in sorted(endpoints.items()):
    e = calls[0]
    print(f"  {endpoint}")
    print(f"    URL    : {e['url'][:100]}")
    print(f"    Status : {e['status']}")
    if e.get("res_body") and isinstance(e["res_body"], dict):
        keys = list(e["res_body"].keys())[:5]
        print(f"    Body   : {keys}")
    print()

# Show video URLs
video_urls = [e["url"] for e in entries
              if any(ext in e["url"] for ext in [".m3u8", ".mp4", ".ts", "stream", "video", "cdn"])]
if video_urls:
    print(f"\n{'='*60}")
    print(f"  Video/Stream URLs Found: {len(video_urls)}")
    print(f"{'='*60}")
    for u in set(video_urls[:20]):
        print(f"  {u[:120]}")

# Save report
report = os.path.join(os.path.dirname(OUT), "api_report.json")
with open(report, "w") as f:
    json.dump({
        "total": len(entries),
        "kuku_total": len(kuku),
        "endpoints": {k: [{"url": e["url"], "status": e["status"]} for e in v]
                      for k, v in endpoints.items()},
        "video_urls": list(set(video_urls)),
    }, f, indent=2)
print(f"\nFull report saved: {report}")

