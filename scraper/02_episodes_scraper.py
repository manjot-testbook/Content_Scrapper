#!/usr/bin/env python3
"""
Script 2+3 -- KukuFM Episodes Scraper
For every series in all_series.json:
  - Fetch full show metadata  (GET /api/v1.2/channels/{id}/details/)
  - Paginate all episodes     (GET /api/v2.3/channels/{id}/episodes/)
  - Save: metadata/api_catalog/episodes/show_{id}_episodes.json

Run:
    python scraper/02_episodes_scraper.py
    python scraper/02_episodes_scraper.py --show-id 277462
    python scraper/02_episodes_scraper.py --limit 10 --no-skip
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import local as thread_local

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.auth import get_auth_headers, refresh_token_without_otp

BASE_DIR     = Path(__file__).resolve().parent.parent
CATALOG_DIR  = BASE_DIR / "metadata" / "api_catalog"
SERIES_FILE  = CATALOG_DIR / "all_series.json"
EPISODES_DIR = CATALOG_DIR / "episodes"
API_BASE     = "https://api.kukufm.com"

EPISODES_DIR.mkdir(parents=True, exist_ok=True)


_thread_local = thread_local()

def build_session():
    s = requests.Session()
    s.headers.update(get_auth_headers())
    return s

def get_thread_session():
    """Per-thread session – thread-safe."""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = build_session()
    return _thread_local.session


def safe_get(session, url, params=None, retries=3, backoff=2.0):
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(backoff * (attempt + 1) * 3)
                continue
            if r.status_code == 404:
                return None
            if not r.ok:
                print(f"    [HTTP {r.status_code}] {url}")
                return None
            return r.json()
        except Exception as exc:
            print(f"    [error] {exc} (attempt {attempt+1})")
            time.sleep(backoff)
    return None


def fetch_show_details(session, show_id):
    """GET /api/v1.2/channels/{id}/details/"""
    data = safe_get(session, f"{API_BASE}/api/v1.2/channels/{show_id}/details/")
    if not data:
        return None
    return (data.get("data", {}).get("channel")
            or data.get("channel")
            or data.get("data")
            or data)


def fetch_all_episodes(session, show_id):
    """GET /api/v2.3/channels/{id}/episodes/ (paginated)"""
    all_eps = []
    page = 1
    while True:
        data = safe_get(session,
                        f"{API_BASE}/api/v2.3/channels/{show_id}/episodes/",
                        params={"page": page})
        if not data:
            break
        episodes = []
        for key in ("episodes", "data", "items"):
            val = data.get(key)
            if isinstance(val, list):
                episodes = val
                break
            if isinstance(val, dict):
                sub = val.get("episodes") or val.get("items", [])
                if isinstance(sub, list):
                    episodes = sub
                    break
        if not episodes:
            break
        all_eps.extend(episodes)
        has_more = bool(
            data.get("has_next") or data.get("next") or data.get("has_more_pages")
        )
        if not has_more:
            break
        page += 1
        time.sleep(0.2)
    return all_eps


_EP_FIELDS = (
    "id", "slug", "title", "index", "status", "season_no", "duration_s",
    "published_on", "is_premium", "is_locked", "is_free_unlocked", "n_plays",
    "show_id", "show_slug", "show_title", "video_hls_url", "subtitle_url",
    "audio_url", "sprite_metadata", "show_script_url", "thumbnail",
    "reel_image", "image", "description", "tags", "cast",
)


def normalize_episode(ep):
    return {k: ep.get(k) for k in _EP_FIELDS}


def show_output_path(show_id):
    return EPISODES_DIR / f"show_{show_id}_episodes.json"


def already_scraped(show_id):
    p = show_output_path(show_id)
    return p.exists() and p.stat().st_size > 100


def process_show(session, show_id, skip_existing=True):
    if skip_existing and already_scraped(show_id):
        return None   # silently skipped
    meta = fetch_show_details(session, show_id) or {"id": show_id}
    episodes = fetch_all_episodes(session, show_id)
    title = meta.get("title", f"show_{show_id}")
    print(f"  [ok] {title} ({show_id}): {len(episodes)} episodes")
    with open(show_output_path(show_id), "w") as fh:
        json.dump(
            {"show": meta, "n_total": len(episodes),
             "episodes": [normalize_episode(e) for e in episodes]},
            fh, indent=2, ensure_ascii=False,
        )
    return {"show_id": show_id, "title": title, "n_episodes": len(episodes)}


def run(show_ids=None, limit=None, skip_existing=True, workers=8):
    refresh_token_without_otp()

    if show_ids:
        ids = show_ids
    else:
        if not SERIES_FILE.exists():
            print("[error] Run 01_series_scraper.py first.")
            sys.exit(1)
        with open(SERIES_FILE) as fh:
            series = json.load(fh)
        ids = [s["id"] for s in series if s.get("id")]
        if limit:
            ids = ids[:limit]

    # Skip already-done shows before scheduling
    pending = [sid for sid in ids
               if not (skip_existing and already_scraped(sid))]
    skipped = len(ids) - len(pending)
    print(f"[episodes] {len(ids)} series total  |  "
          f"{skipped} already done  |  {len(pending)} to fetch  |  "
          f"{workers} workers")

    results = []
    completed = 0

    def _worker(sid):
        sess = get_thread_session()
        return process_show(sess, sid, skip_existing=False)  # already filtered above

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, sid): sid for sid in pending}
        for future in as_completed(futures):
            completed += 1
            try:
                r = future.result()
                if r:
                    results.append(r)
                    print(f"  [{completed}/{len(pending)}] done: {r['title']}")
            except Exception as exc:
                sid = futures[future]
                print(f"  [{completed}/{len(pending)}] ERROR show {sid}: {exc}")

    total = sum(r["n_episodes"] for r in results)
    print(f"\nDone: {len(results)} shows scraped, {total} episodes  ->  {EPISODES_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scrape KukuFM episodes for all series")
    ap.add_argument("--show-id", type=int, nargs="+", help="Specific show ID(s)")
    ap.add_argument("--limit",   type=int,             help="Limit to first N series")
    ap.add_argument("--no-skip", action="store_true",  help="Re-scrape existing shows")
    ap.add_argument("--workers", type=int, default=8,  help="Parallel download threads (default 8)")
    args = ap.parse_args()
    run(show_ids=args.show_id, limit=args.limit,
        skip_existing=not args.no_skip, workers=args.workers)

