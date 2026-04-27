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

def show_structure(label, url, params=None):
    r = requests.get(url, headers=BASE_HEADERS, params=params, timeout=15)
    print(f"\n=== {label} ===  HTTP {r.status_code}")
    if r.ok:
        data = r.json()
        print(json.dumps(data, indent=2, ensure_ascii=False)[:1200])

BASE = "https://api.kukufm.com"

show_structure("trending p1",     f"{BASE}/api/v1.0/channels/trending/", {"page": 1})
show_structure("show details",    f"{BASE}/api/v1.2/channels/277462/details/")
show_structure("episodes p1",     f"{BASE}/api/v2.3/channels/277462/episodes/", {"page": 1})
show_structure("search-recs",     f"{BASE}/api/v2/search/recommendations/")
show_structure("more-like-this",  f"{BASE}/api/v2/groups/more-like-this/shows/", {"show_id": 277462})

