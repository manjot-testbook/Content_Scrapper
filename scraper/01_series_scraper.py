"""
Script 1 — KukuFM All-Series Scraper
======================================
Discovers every show in the KukuFM catalogue from **multiple sources**:

  1. Home feed  (/api/v3/home/all/)  — all language variants
  2. Home category "more shows" pages  (/api/v3/home/category_more_shows)
  3. Trending  (/api/v1.0/channels/trending/)
  4. Search recommendations  (/api/v2/search/recommendations/)
  5. "More like this" fan-out from seed shows  (/api/v2/groups/more-like-this/shows/)
  6. Library / watch-history items  (/api/v3.1/library/items/)
  7. Full show-details enrichment  (/api/v1.2/channels/{id}/details/)

Output
------
    metadata/api_catalog/all_series.json

Run
---
    python scraper/01_series_scraper.py
    python scraper/01_series_scraper.py --langs english hindi --max-pages 20 --out metadata/api_catalog/all_series.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

# ── project imports ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.auth import get_auth_headers, refresh_token_without_otp

# ── paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent.parent
CATALOG_DIR = BASE_DIR / "metadata" / "api_catalog"
OUT_FILE    = CATALOG_DIR / "all_series.json"
API_BASE    = "https://api.kukufm.com"

CATALOG_DIR.mkdir(parents=True, exist_ok=True)

# Language slugs used for home feed pagination
ALL_LANGS = [
    "all", "english", "hindi", "tamil", "telugu", "kannada",
    "malayalam", "marathi", "bengali", "gujarati", "punjabi", "odia",
]

# Category slugs discovered in home feed (augmented at runtime)
KNOWN_CATEGORY_SLUGS: list[str] = []


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(get_auth_headers())
    return s


def safe_get(session: requests.Session, url: str, params: dict | None = None,
             retries: int = 3, backoff: float = 2.0) -> dict | None:
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = backoff * (attempt + 1) * 3
                print(f"  [rate-limit] sleeping {wait:.0f}s …")
                time.sleep(wait)
                continue
            if not r.ok:
                print(f"  [HTTP {r.status_code}] {url}")
                return None
            return r.json()
        except Exception as exc:
            print(f"  [error] {exc} (attempt {attempt+1})")
            time.sleep(backoff)
    return None


# ── Extractors ─────────────────────────────────────────────────────────────────

def _extract_shows_from_section(section: dict) -> list[dict]:
    """Pull show objects from a home-feed section."""
    shows = []
    for key in ("channels", "shows", "items", "data"):
        items = section.get(key, [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    shows.append(item)
    return shows


def _extract_shows_from_home_response(data: dict) -> tuple[list[dict], list[str]]:
    """Returns (shows, category_slugs) from a /home/ response."""
    shows: list[dict] = []
    slugs: list[str] = []
    sections = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(sections, list):
        sections = [sections]
    for section in sections:
        if not isinstance(section, dict):
            continue
        slug = section.get("slug") or section.get("id")
        if slug:
            slugs.append(str(slug))
        shows.extend(_extract_shows_from_section(section))
    return shows, slugs


# ── Source 1 — Home feed ───────────────────────────────────────────────────────

def scrape_home_feed(session: requests.Session, langs: list[str]) -> list[dict]:
    shows: list[dict] = []
    for lang in langs:
        url = f"{API_BASE}/api/v3/home/{lang}/"
        print(f"  [home] {lang} …")
        data = safe_get(session, url)
        if not data:
            continue
        found, slugs = _extract_shows_from_home_response(data)
        shows.extend(found)
        KNOWN_CATEGORY_SLUGS.extend(s for s in slugs if s not in KNOWN_CATEGORY_SLUGS)
        print(f"    → {len(found)} shows, {len(slugs)} category slugs")
        time.sleep(0.3)
    return shows


# ── Source 2 — Category "more shows" pages ─────────────────────────────────────

def scrape_category_more_shows(session: requests.Session,
                                category_slugs: list[str],
                                max_pages: int = 10) -> list[dict]:
    shows: list[dict] = []
    seen_slugs: set[str] = set()
    for slug in category_slugs:
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        for page in range(1, max_pages + 1):
            url = f"{API_BASE}/api/v3/home/category_more_shows"
            params = {"slug": slug, "page": page}
            data = safe_get(session, url, params=params)
            if not data:
                break
            batch, _ = _extract_shows_from_home_response(data)
            if not batch:
                break
            shows.extend(batch)
            print(f"    [cat:{slug} p{page}] +{len(batch)} shows")
            if not data.get("has_more_pages", len(batch) >= 10):
                break
            time.sleep(0.2)
    return shows


# ── Source 3 — Trending ────────────────────────────────────────────────────────

def scrape_trending(session: requests.Session, max_pages: int = 5) -> list[dict]:
    shows: list[dict] = []
    for page in range(1, max_pages + 1):
        url = f"{API_BASE}/api/v1.0/channels/trending/"
        data = safe_get(session, url, params={"page": page})
        if not data:
            break
        batch = data.get("data", {}).get("channels") or data.get("channels", [])
        if not isinstance(batch, list):
            batch = []
        if not batch:
            break
        shows.extend(batch)
        print(f"  [trending p{page}] +{len(batch)} shows")
        time.sleep(0.2)
    return shows


# ── Source 4 — Search recommendations ─────────────────────────────────────────

SEARCH_SEEDS = [
    "", "love", "drama", "crime", "horror", "comedy", "thriller",
    "romance", "action", "mystery", "family", "historical",
    "hindi", "english", "motivational",
]


def scrape_search_recommendations(session: requests.Session) -> list[dict]:
    shows: list[dict] = []
    url = f"{API_BASE}/api/v2/search/recommendations/"
    data = safe_get(session, url)
    if data:
        batch, _ = _extract_shows_from_home_response(data)
        shows.extend(batch)
        print(f"  [search-recs] {len(batch)} shows")

    # also try keyword search
    for kw in SEARCH_SEEDS[:8]:
        s_url = f"{API_BASE}/api/v1.0/search/"
        d = safe_get(session, s_url, params={"q": kw, "page": 1})
        if d:
            batch, _ = _extract_shows_from_home_response(d)
            shows.extend(batch)
            if batch:
                print(f"    [search:{kw!r}] +{len(batch)}")
        time.sleep(0.2)
    return shows


# ── Source 5 — More like this fan-out ─────────────────────────────────────────

def scrape_more_like_this(session: requests.Session, seed_ids: list[int],
                           max_per_seed: int = 1) -> list[dict]:
    """Expand show catalogue via show-based recommendations."""
    shows: list[dict] = []
    url = f"{API_BASE}/api/v2/groups/more-like-this/shows/"
    for show_id in seed_ids[:30]:   # cap to avoid infinite fan-out
        data = safe_get(session, url, params={"show_id": show_id})
        if not data:
            time.sleep(0.3)
            continue
        batch, _ = _extract_shows_from_home_response(data)
        shows.extend(batch)
        if batch:
            print(f"  [more-like:{show_id}] +{len(batch)}")
        time.sleep(0.3)
    return shows


# ── Source 6 — Library / watch history ────────────────────────────────────────

def scrape_library(session: requests.Session) -> list[dict]:
    shows: list[dict] = []
    url = f"{API_BASE}/api/v3.1/library/items/"
    for page in range(1, 6):
        data = safe_get(session, url, params={"page": page})
        if not data:
            break
        batch, _ = _extract_shows_from_home_response(data)
        if not batch:
            break
        shows.extend(batch)
        print(f"  [library p{page}] +{len(batch)}")
        time.sleep(0.2)
    return shows


# ── Source 7 — Full show details enrichment ───────────────────────────────────

def enrich_show_details(session: requests.Session, show_id: int) -> dict | None:
    url = f"{API_BASE}/api/v1.2/channels/{show_id}/details/"
    data = safe_get(session, url)
    if not data:
        return None
    return data.get("data", {}).get("channel") or data.get("channel") or data.get("data")


# ── Deduplicate helpers ────────────────────────────────────────────────────────

def dedup_shows(raw: list[dict]) -> dict[int, dict]:
    """Deduplicate by show ID, merging fields from multiple appearances."""
    merged: dict[int, dict] = {}
    for show in raw:
        sid = show.get("id")
        if not sid:
            continue
        if sid not in merged:
            merged[sid] = show
        else:
            # prefer the richer object
            if len(show) > len(merged[sid]):
                merged[sid] = show
    return merged


def _normalize_show(show: dict) -> dict:
    """Return a clean flat dict with the canonical fields we want."""
    return {
        "id":               show.get("id"),
        "slug":             show.get("slug"),
        "title":            show.get("title"),
        "description":      show.get("description") or show.get("description_secondary"),
        "language":         show.get("language") or show.get("lang"),
        "status":           show.get("status"),
        "n_episodes":       show.get("n_episodes"),
        "n_seasons":        show.get("n_seasons"),
        "n_listens":        show.get("n_listens"),
        "duration_s":       show.get("duration_s"),
        "is_premium":       show.get("is_premium"),
        "monetization_type": show.get("monetization_type"),
        "overall_rating":   show.get("overall_rating"),
        "n_reviews":        show.get("n_reviews"),
        "is_fictional":     show.get("is_fictional"),
        "age_rating":       show.get("age_rating"),
        "ip_source":        show.get("ip_source"),
        "content_descriptors": show.get("content_descriptors", []),
        "show_script_url":  show.get("show_script_url"),
        "show_type":        show.get("show_type"),
        "genre":            show.get("genre"),
        "tropes":           show.get("tropes", []),
        "app_tags":         show.get("app_tags", []),
        "author":           show.get("author"),
        "image":            show.get("image"),
        "reel_image":       show.get("reel_image"),
        "landscape_image":  show.get("landscape_image"),
        "dynamic_link":     show.get("dynamic_link"),
        "published_on":     show.get("published_on"),
        "uri":              show.get("uri"),
        "preview_url":      show.get("preview_url") or show.get("banner_preview_url"),
        "is_verified":      show.get("is_verified"),
        "is_adult_content": show.get("is_adult_content"),
        "is_safe_for_kids": show.get("is_safe_for_kids"),
        "is_top_10":        show.get("is_top_10"),
        "is_coming_soon":   show.get("is_coming_soon"),
        "is_reel":          show.get("is_reel"),
        "sharing_text":     show.get("sharing_text"),
        "meta_data":        show.get("meta_data"),
        "thumbnail_color":  show.get("thumbnail_color"),
        "recommendation_score": show.get("recommendation_score"),
        "n_impressions":    show.get("n_impressions"),
        "users_completion_p": show.get("users_completion_p"),
        "completion_status": show.get("completion_status"),
        "labels":           show.get("labels", []),
        "credits":          show.get("credits", []),
        "content_type":     show.get("content_type"),
        "other_images":     show.get("other_images"),
        "trailer_v2":       show.get("trailer_v2"),
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def run(langs: list[str] = None,
        max_cat_pages: int = 10,
        max_mlt_seeds: int = 30,
        enrich: bool = True,
        out: Path = OUT_FILE):

    if langs is None:
        langs = ALL_LANGS

    # Ensure we have a token
    refresh_token_without_otp()
    session = build_session()

    raw_shows: list[dict] = []

    # --- Source 1: Home feeds ---
    print("\n[1/6] Home feed …")
    raw_shows.extend(scrape_home_feed(session, langs))

    # --- Source 2: Category pages ---
    print("\n[2/6] Category more-shows pages …")
    raw_shows.extend(scrape_category_more_shows(session, KNOWN_CATEGORY_SLUGS, max_cat_pages))

    # --- Source 3: Trending ---
    print("\n[3/6] Trending …")
    raw_shows.extend(scrape_trending(session))

    # --- Source 4: Search recommendations ---
    print("\n[4/6] Search recommendations …")
    raw_shows.extend(scrape_search_recommendations(session))

    # --- Deduplicate before fan-out ---
    merged = dedup_shows(raw_shows)
    print(f"\n  Unique shows so far: {len(merged)}")

    # --- Source 5: More like this ---
    print("\n[5/6] More-like-this fan-out …")
    seed_ids = list(merged.keys())[:max_mlt_seeds]
    raw_shows.extend(scrape_more_like_this(session, seed_ids))
    merged = dedup_shows(list(merged.values()) + raw_shows)

    # --- Source 6: Library ---
    print("\n[6/6] Library …")
    raw_shows.extend(scrape_library(session))
    merged = dedup_shows(list(merged.values()) + raw_shows)

    print(f"\n  Total unique shows collected: {len(merged)}")

    # --- Enrich with full show details ---
    if enrich:
        print(f"\n[enrich] Fetching full details for {len(merged)} shows …")
        enriched: dict[int, dict] = {}
        for idx, (sid, show) in enumerate(merged.items(), 1):
            detail = enrich_show_details(session, sid)
            if detail:
                enriched[sid] = detail
            else:
                enriched[sid] = show
            if idx % 50 == 0:
                print(f"  … {idx}/{len(merged)} enriched")
            time.sleep(0.15)
        merged = enriched

    # --- Normalize + save ---
    result = [_normalize_show(v) for v in merged.values()]
    result.sort(key=lambda x: x.get("n_listens") or 0, reverse=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n✓ Saved {len(result)} series to {out}")
    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scrape all KukuFM series")
    ap.add_argument("--langs",      nargs="*", default=None,
                    help="Language slugs to crawl (default: all)")
    ap.add_argument("--max-pages",  type=int, default=10,
                    help="Max pages per category (default 10)")
    ap.add_argument("--max-seeds",  type=int, default=30,
                    help="Max seed shows for more-like-this fan-out")
    ap.add_argument("--no-enrich",  action="store_true",
                    help="Skip enriching every show with /details/")
    ap.add_argument("--out",        default=str(OUT_FILE),
                    help="Output JSON path")
    args = ap.parse_args()

    run(
        langs=args.langs,
        max_cat_pages=args.max_pages,
        max_mlt_seeds=args.max_seeds,
        enrich=not args.no_enrich,
        out=Path(args.out),
    )

