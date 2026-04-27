import requests
import json

BASE_HEADERS = {
    "content-type":   "application/json",
    "client-country": "IN",
    "install-source": "google_play",
    "lang":           "english",
    "app-version":    "50706",
    "user-agent":     "kukufm-android-reels/5.7.6",
    "package-name":   "com.vlv.aravali.reels",
    "build-number":   "5070600",
}

# Test 1: No auth
r = requests.get("https://api.kukufm.com/api/v3/home/english/", headers=BASE_HEADERS, timeout=15)
print("No-auth home status:", r.status_code)
if r.ok:
    data = r.json()
    d = data.get("data", [])
    print("data type:", type(d).__name__, "len:", len(d) if hasattr(d, "__len__") else "-")
    if isinstance(d, list) and d:
        print("First section keys:", list(d[0].keys())[:10])
        for sec in d[:5]:
            for k in ("channels", "shows", "items", "data"):
                items = sec.get(k, [])
                if isinstance(items, list) and items:
                    print(f"  section '{sec.get('title','?')}' -> key={k}: {len(items)} shows, first id={items[0].get('id')}")
                    break
else:
    print(r.text[:300])

# Test 2: trending
r2 = requests.get("https://api.kukufm.com/api/v1.0/channels/trending/", headers=BASE_HEADERS, timeout=15)
print("\nTrending status:", r2.status_code)
if r2.ok:
    d2 = r2.json()
    print("keys:", list(d2.keys())[:8])
else:
    print(r2.text[:200])

