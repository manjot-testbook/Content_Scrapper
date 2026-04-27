#!/usr/bin/env python3
"""
Script 2 -- KukuFM Episodes Scraper
For every series in all_series.json:
  - Paginate all episodes  (GET /api/v2.3/channels/{id}/episodes/?page=N)
  - Save: metadata/api_catalog/episodes/show_{id}_episodes.json

Real API response (verified from live call):
  {show: {...}, episodes: [...10 items...], has_more: bool,
   n_episodes: 81, n_pages: 9, page: 1}

Run:
    python scraper/02_episodes_scraper.py
    python scraper/02_episodes_scraper.py --show-id 275838
    python scraper/02_episodes_scraper.py --limit 10 --no-skip
"""
import argparse
import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import local as thread_local

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.auth import get_auth_headers, refresh_token_without_otp

# Force line-buffered output so progress shows immediately
sys.stdout.reconfigure(line_buffering=True)

BASE_DIR     = Path(__file__).resolve().parent.parent
CATALOG_DIR  = BASE_DIR / "metadata" / "api_catalog"
SERIES_FILE  = CATALOG_DIR / "all_series.json"
EPISODES_DIR = CATALOG_DIR / "episodes"
API_BASE     = "https://api.kukufm.com"

EPISODES_DIR.mkdir(parents=True, exist_ok=True)

# ── Global rate limiter (shared across all threads) ────────────────────────────
MAX_RPS = 1.5

class _RateLimiter:
    def __init__(self, rps):
        self._interval = 1.0 / rps
        self._lock     = threading.Lock()
        self._last     = 0.0
    def wait(self):
        with self._lock:
            gap = self._interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()

_RL = _RateLimiter(MAX_RPS)

# ── Session helpers ────────────────────────────────────────────────────────────
_thread_local = thread_local()

def build_session():
    s = requests.Session()
    s.headers.update(get_auth_headers())
    return s

def get_thread_session():
    if not hasattr(_thread_local, "session"):
        _thread_local.session = build_session()
    return _thread_local.session


def safe_get(session, url, params=None, retries=8):
    for attempt in range(retries):
        _RL.wait()
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(10 * (2 ** attempt), 120)
                print(f"  [429] attempt {attempt+1}/{retries} — wait {wait:.0f}s …", flush=True)
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503, 504):
                wait = 2 * (attempt + 1)
                print(f"  [HTTP {r.status_code}] retry in {wait:.0f}s …", flush=True)
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            if not r.ok:
                print(f"  [HTTP {r.status_code}] {url}", flush=True)
                return None
            return r.json()
        except Exception as exc:
            wait = 2 * (attempt + 1)
            print(f"  [error] {exc} — retry in {wait:.0f}s", flush=True)
            time.sleep(wait)
    print(f"  [failed] gave up: {url}", flush=True)
    return None


def fetch_all_episodes(session, show_id):
    """
    GET /api/v2.3/channels/{id}/episodes/?page=N
    Real pagination fields: has_more (bool), n_pages (int), page (int)
    Returns (show_meta, episodes_list)
    """
    all_eps  = []
    show_meta = None
    page = 1

    while True:
        data = safe_get(session,
                        f"{API_BASE}/api/v2.3/channels/{show_id}/episodes/",
                        params={"page": page, "lang": "english"})
        if not data:
            break

        # Grab show metadata from first page
        if show_meta is None:
            show_meta = data.get("show") or {"id": show_id}
            n_pages   = data.get("n_pages", "?")
            n_total   = data.get("n_episodes", "?")
            print(f"    show {show_id}: {n_total} episodes / {n_pages} pages", flush=True)

        episodes = data.get("episodes", [])
        if not episodes:
            break

        all_eps.extend(episodes)

        # ← FIXED: real field is 'has_more', not 'has_next'/'next'/'has_more_pages'
        if not data.get("has_more"):
            break

        page += 1

    return show_meta, all_eps


def show_output_path(show_id):
    return EPISODES_DIR / f"show_{show_id}_episodes.json"

def already_scraped(show_id):
    p = show_output_path(show_id)
    if not p.exists() or p.stat().st_size < 100:
        return False
    # Check episode count matches n_episodes in file
    try:
        with open(p) as f:
            d = json.load(f)
        saved = len(d.get("episodes", []))
        expected = d.get("n_total", 0)
        return saved >= expected and expected > 0
    except Exception:
        return False


def process_show(session, show_id, skip_existing=True):
    if skip_existing and already_scraped(show_id):
        return None

    show_meta, episodes = fetch_all_episodes(session, show_id)
    if show_meta is None:
        return None

    title = show_meta.get("title", f"show_{show_id}")
    n_expected = show_meta.get("n_episodes", len(episodes))
    print(f"  [ok] {title} ({show_id}): {len(episodes)}/{n_expected} episodes", flush=True)

    with open(show_output_path(show_id), "w") as fh:
        json.dump(
            {"show": show_meta, "n_total": n_expected,
             "episodes": episodes},
            fh, indent=2, ensure_ascii=False,
        )
    return {"show_id": show_id, "title": title, "n_episodes": len(episodes)}


def run(show_ids=None, limit=None, skip_existing=True, workers=4):
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

    pending = [sid for sid in ids
               if not (skip_existing and already_scraped(sid))]
    skipped = len(ids) - len(pending)
    print(f"[episodes] {len(ids)} series  |  {skipped} already complete  |  "
          f"{len(pending)} to fetch  |  {workers} workers  |  {MAX_RPS} req/s")

    results   = []
    completed = 0

    def _worker(sid):
        sess = get_thread_session()
        return process_show(sess, sid, skip_existing=False)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, sid): sid for sid in pending}
        for future in as_completed(futures):
            completed += 1
            try:
                r = future.result()
                if r:
                    results.append(r)
                    print(f"  [{completed}/{len(pending)}] ✓ {r['title']} — {r['n_episodes']} eps",
                          flush=True)
            except Exception as exc:
                sid = futures[future]
                print(f"  [{completed}/{len(pending)}] ERROR show {sid}: {exc}", flush=True)

    total = sum(r["n_episodes"] for r in results)
    print(f"\nDone: {len(results)} shows, {total} episodes → {EPISODES_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scrape KukuFM episodes for all series")
    ap.add_argument("--show-id", type=int, nargs="+", help="Specific show ID(s)")
    ap.add_argument("--limit",   type=int,             help="Limit to first N series")
    ap.add_argument("--no-skip", action="store_true",  help="Re-scrape all shows")
    ap.add_argument("--workers", type=int, default=4,
                    help="Parallel workers (default 4; rate limiter caps total req/s)")
    args = ap.parse_args()
    run(show_ids=args.show_id, limit=args.limit,
        skip_existing=not args.no_skip, workers=args.workers)

