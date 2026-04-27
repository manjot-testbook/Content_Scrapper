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

def test(label, url, params=None, method="GET", body=None):
    try:
        if method == "POST":
            r = requests.post(url, headers=BASE_HEADERS, json=body or {}, timeout=10)
        else:
            r = requests.get(url, headers=BASE_HEADERS, params=params, timeout=10)
        print(f"{label}: HTTP {r.status_code}")
        if r.ok:
            d = r.json()
            print(f"  keys: {list(d.keys())[:8]}")
        else:
            print(f"  {r.text[:100]}")
    except Exception as e:
        print(f"{label}: ERROR {e}")

BASE = "https://api.kukufm.com"

test("trending p1",         f"{BASE}/api/v1.0/channels/trending/", {"page": 1})
test("show details 277462", f"{BASE}/api/v1.2/channels/277462/details/")
test("episodes 277462",     f"{BASE}/api/v2.3/channels/277462/episodes/", {"page": 1})
test("search-recs",         f"{BASE}/api/v2/search/recommendations/")
test("more-like-this",      f"{BASE}/api/v2/groups/more-like-this/shows/", {"show_id": 277462})
test("home all",            f"{BASE}/api/v3/home/all/")
test("home hindi",          f"{BASE}/api/v3/home/hindi/")

