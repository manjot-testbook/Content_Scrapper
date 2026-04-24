import json, os

log = "/Users/manjotsingh/PycharmProjects/Content_Scrapper/metadata/captured_apis/api_traffic.jsonl"
out = "/Users/manjotsingh/PycharmProjects/Content_Scrapper/logs/traffic_summary.json"

records = []
with open(log) as f:
    for line in f:
        if line.strip():
            try:
                records.append(json.loads(line))
            except:
                pass

hosts = {}
kuku = []
video_urls = []
endpoints = set()

for r in records:
    h = r.get("host", "unknown")
    hosts[h] = hosts.get(h, 0) + 1
    if r.get("is_kukutv"):
        kuku.append(r)
        endpoints.add(f"{r['method']} {r.get('path','')}")
    url = r.get("url", "")
    ct = r.get("content_type", "")
    if any(ext in url.lower() for ext in [".m3u8", ".mp4", ".mpd", ".mp3", ".m4a", ".ts"]):
        video_urls.append(url)
    elif any(t in ct for t in ["video/", "audio/", "mpegurl"]):
        video_urls.append(url)

summary = {
    "total_requests": len(records),
    "unique_hosts": sorted(hosts.items(), key=lambda x: -x[1]),
    "kuku_requests": len(kuku),
    "kuku_endpoints": sorted(endpoints),
    "kuku_sample_requests": [
        {"method": r["method"], "status": r.get("status_code"), "path": r.get("path", ""),
         "host": r.get("host"), "has_response": r.get("response_body") is not None}
        for r in kuku[:50]
    ],
    "video_urls": list(set(video_urls))[:50],
}

os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w") as f:
    json.dump(summary, f, indent=2, default=str)
