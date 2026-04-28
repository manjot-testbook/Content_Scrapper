#!/usr/bin/env python3
"""
Script 3 -- KukuFM Master CSV Generator
==========================================
Reads every show_*_episodes.json from metadata/api_catalog/episodes/
and produces a single flat CSV at metadata/kuku_master.csv.

Each row = one episode. Columns:
  Series metadata  (show_id, show_title, ...)
  Episode metadata (episode_id, episode_title, episode_subtitle_url, ...)
  subtitle_text    (plain text extracted from the .srt file)
  episode_script   (plain text extracted from the .docx script file, if any)

URL-to-text lookups are cached in:
  metadata/subtitle_cache.json   { url -> plain_text }
  metadata/script_cache.json     { url -> plain_text }

Run:
    python scraper/03_csv_generator.py
    python scraper/03_csv_generator.py --out metadata/my_export.csv --workers 10
"""

import argparse
import csv
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, local as thread_local

import requests

sys.stdout.reconfigure(line_buffering=True)

# python-docx (optional)
try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False
    print("[warn] python-docx not installed. Script columns will be empty.")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.auth import get_auth_headers

# ── paths ──────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).resolve().parent.parent
CATALOG_DIR   = BASE_DIR / "metadata" / "api_catalog"
EPISODES_DIR  = CATALOG_DIR / "episodes"
OUT_FILE      = BASE_DIR / "metadata" / "kuku_master.csv"
SUBTITLE_CACHE_FILE = BASE_DIR / "metadata" / "subtitle_cache.json"
SCRIPT_CACHE_FILE   = BASE_DIR / "metadata" / "script_cache.json"

# ── CSV columns ────────────────────────────────────────────────────────────────
SHOW_COLS = [
    "show_id", "show_slug", "show_title", "show_description",
    "show_language", "show_status", "show_n_episodes", "show_n_seasons",
    "show_n_listens", "show_duration_s", "show_is_premium",
    "show_monetization_type", "show_overall_rating", "show_n_reviews",
    "show_is_fictional", "show_genre", "show_tropes", "show_app_tags",
    "show_author", "show_content_type", "show_is_adult_content",
    "show_is_safe_for_kids", "show_published_on", "show_image",
    "show_dynamic_link", "show_uri", "show_sharing_text",
    "show_n_impressions", "show_users_completion_p", "show_recommendation_score",
]

EPISODE_COLS = [
    "episode_id", "episode_slug", "episode_title", "episode_index",
    "episode_status", "episode_season_no", "episode_duration_s",
    "episode_published_on", "episode_is_premium", "episode_is_locked",
    "episode_is_free_unlocked", "episode_n_plays",
    "episode_video_hls_url", "episode_subtitle_url",
    "episode_audio_url", "episode_show_script_url",
    "episode_thumbnail", "episode_reel_image", "episode_image",
]

CONTENT_COLS = ["subtitle_text", "episode_script"]

ALL_COLS = SHOW_COLS + EPISODE_COLS + CONTENT_COLS


# ── Cache helpers (thread-safe) ───────────────────────────────────────────────

class _URLCache:
    """Thread-safe URL → text cache backed by a JSON file."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = Lock()
        self._dirty = False
        if path.exists():
            try:
                with open(path, encoding="utf-8") as fh:
                    self._data: dict = json.load(fh)
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def get(self, url: str) -> str | None:
        with self._lock:
            return self._data.get(url)

    def set(self, url: str, text: str) -> None:
        with self._lock:
            self._data[url] = text
            self._dirty = True

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not (self._dirty or force):
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
            self._dirty = False

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


_subtitle_cache: _URLCache | None = None
_script_cache:   _URLCache | None = None


def _get_caches() -> tuple["_URLCache", "_URLCache"]:
    global _subtitle_cache, _script_cache
    if _subtitle_cache is None:
        _subtitle_cache = _URLCache(SUBTITLE_CACHE_FILE)
        _script_cache   = _URLCache(SCRIPT_CACHE_FILE)
        print(f"[cache] subtitle: {len(_subtitle_cache)} entries | "
              f"script: {len(_script_cache)} entries", flush=True)
    return _subtitle_cache, _script_cache


# ── HTTP helpers ───────────────────────────────────────────────────────────────

_thread_local = thread_local()


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(get_auth_headers())
    return s


def get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = _make_session()
    return _thread_local.session


def fetch_url(url: str, binary: bool = False, retries: int = 4) -> bytes | str | None:
    if not url:
        return None
    sess = get_session()
    for attempt in range(retries):
        try:
            r = sess.get(url, timeout=20)
            if r.ok:
                return r.content if binary else r.text
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
        except Exception:
            time.sleep(2)
    return None


# ── Subtitle (.srt) extractor ─────────────────────────────────────────────────

_SRT_CUE_RE = re.compile(
    r"^\d+\s*\n"
    r"[\d:,]+ --> [\d:,]+[^\n]*\n"
    r"((?:.+\n?)+)",
    re.MULTILINE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def extract_subtitle_text(url: str) -> str:
    """Download .srt, return plain text. Uses/populates subtitle_cache."""
    if not url:
        return ""
    sc, _ = _get_caches()
    cached = sc.get(url)
    if cached is not None:
        return cached

    raw = fetch_url(url)
    if not raw:
        sc.set(url, "")
        return ""
    lines = []
    for m in _SRT_CUE_RE.finditer(raw):
        cue = m.group(1).strip()
        cue = _HTML_TAG_RE.sub("", cue)
        lines.append(cue)
    text = " ".join(lines).strip()
    sc.set(url, text)
    return text


# ── Script (.docx) extractor ──────────────────────────────────────────────────

def extract_docx_text(url: str) -> str:
    """Download .docx, extract paragraph text. Uses/populates script_cache."""
    if not HAS_DOCX or not url:
        return ""
    _, scc = _get_caches()
    cached = scc.get(url)
    if cached is not None:
        return cached

    raw = fetch_url(url, binary=True)
    if not raw:
        scc.set(url, "")
        return ""
    try:
        doc = DocxDocument(io.BytesIO(raw))
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paras)
    except Exception as exc:
        text = f"[docx-error: {exc}]"
    scc.set(url, text)
    return text


# ── Row builders ──────────────────────────────────────────────────────────────

def _show_row(show: dict) -> dict:
    return {
        "show_id":                  show.get("id"),
        "show_slug":                show.get("slug"),
        "show_title":               show.get("title"),
        "show_description":         show.get("description"),
        "show_language":            show.get("language") or show.get("lang"),
        "show_status":              show.get("status"),
        "show_n_episodes":          show.get("n_episodes"),
        "show_n_seasons":           show.get("n_seasons"),
        "show_n_listens":           show.get("n_listens"),
        "show_duration_s":          show.get("duration_s"),
        "show_is_premium":          show.get("is_premium"),
        "show_monetization_type":   show.get("monetization_type"),
        "show_overall_rating":      show.get("overall_rating"),
        "show_n_reviews":           show.get("n_reviews"),
        "show_is_fictional":        show.get("is_fictional"),
        "show_genre":               show.get("genre"),
        "show_tropes":              json.dumps(show.get("tropes", []), ensure_ascii=False),
        "show_app_tags":            json.dumps(show.get("app_tags", []), ensure_ascii=False),
        "show_author":              show.get("author"),
        "show_content_type":        show.get("content_type"),
        "show_is_adult_content":    show.get("is_adult_content"),
        "show_is_safe_for_kids":    show.get("is_safe_for_kids"),
        "show_published_on":        show.get("published_on"),
        "show_image":               show.get("image"),
        "show_dynamic_link":        show.get("dynamic_link"),
        "show_uri":                 show.get("uri"),
        "show_sharing_text":        show.get("sharing_text"),
        "show_n_impressions":       show.get("n_impressions"),
        "show_users_completion_p":  show.get("users_completion_p"),
        "show_recommendation_score": show.get("recommendation_score"),
    }


def _episode_row(ep: dict) -> dict:
    # subtitle_url and video_hls_url are nested inside ep['content']
    content = ep.get("content") or {}
    subtitle_url  = content.get("subtitle_url") or ep.get("subtitle_url") or ""
    video_hls_url = (content.get("video_hls_url") or content.get("hls_url")
                     or content.get("url") or ep.get("video_hls_url") or "")
    audio_url     = content.get("premium_audio_url") or ep.get("audio_url") or ""

    # show_script_url is a show-level field in meta_data; not per-episode
    show_script_url = ep.get("show_script_url") or ""

    other  = ep.get("other_images") or {}
    return {
        "episode_id":              ep.get("id"),
        "episode_slug":            ep.get("slug"),
        "episode_title":           ep.get("title"),
        "episode_index":           ep.get("index"),
        "episode_status":          ep.get("status"),
        "episode_season_no":       ep.get("season_no"),
        "episode_duration_s":      ep.get("duration_s"),
        "episode_published_on":    ep.get("published_on"),
        "episode_is_premium":      ep.get("is_premium"),
        "episode_is_locked":       ep.get("is_locked"),
        "episode_is_free_unlocked": ep.get("is_free_unlocked"),
        "episode_n_plays":         ep.get("n_plays"),
        "episode_video_hls_url":   video_hls_url,
        "episode_subtitle_url":    subtitle_url,
        "episode_audio_url":       audio_url,
        "episode_show_script_url": show_script_url,
        "episode_thumbnail":       ep.get("thumbnail_image") or ep.get("thumbnail"),
        "episode_reel_image":      other.get("reel_image") or ep.get("reel_image"),
        "episode_image":           ep.get("image"),
    }


def build_row(show_fields: dict, ep: dict, fetch_content: bool = True) -> dict:
    row = {}
    row.update(show_fields)
    ep_fields = _episode_row(ep)
    row.update(ep_fields)

    if fetch_content:
        row["subtitle_text"]  = extract_subtitle_text(ep_fields["episode_subtitle_url"])
        row["episode_script"] = extract_docx_text(ep_fields["episode_show_script_url"])
    else:
        row["subtitle_text"]  = ""
        row["episode_script"] = ""

    return row


# ── Main ───────────────────────────────────────────────────────────────────────

def collect_episode_files() -> list[Path]:
    if not EPISODES_DIR.exists():
        return []
    return sorted(EPISODES_DIR.glob("show_*_episodes.json"))


def run(out: Path = OUT_FILE,
        fetch_content: bool = True,
        workers: int = 10,
        limit_shows: int | None = None):

    # Pre-load caches
    sc, scc = _get_caches()

    files = collect_episode_files()
    if not files:
        print(f"[error] No episode files found in {EPISODES_DIR}")
        sys.exit(1)

    if limit_shows:
        files = files[:limit_shows]

    print(f"[csv] Processing {len(files)} show files → {out}", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    try:
        with open(out, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=ALL_COLS, extrasaction="ignore")
            writer.writeheader()

            for file_idx, fpath in enumerate(files, 1):
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        data = json.load(fh)
                except Exception as exc:
                    print(f"  [warn] Cannot read {fpath.name}: {exc}", flush=True)
                    continue

                show      = data.get("show") or {}
                episodes  = data.get("episodes") or []
                show_fields = _show_row(show)
                show_title  = show.get("title") or fpath.stem
                print(f"  [{file_idx}/{len(files)}] {show_title}: {len(episodes)} eps", flush=True)

                if not fetch_content or workers <= 1:
                    for ep in episodes:
                        row = build_row(show_fields, ep, fetch_content=fetch_content)
                        writer.writerow(row)
                        total_rows += 1
                else:
                    def _fetch_ep(ep, sf=show_fields):
                        return build_row(sf, ep, fetch_content=True)

                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {pool.submit(_fetch_ep, ep): ep for ep in episodes}
                        for future in as_completed(futures):
                            try:
                                writer.writerow(future.result())
                                total_rows += 1
                            except Exception as exc:
                                print(f"    [ep-err] {exc}", flush=True)

                # Save caches after each show file
                if fetch_content:
                    sc.save()
                    scc.save()

    finally:
        # Always persist caches on exit
        if fetch_content:
            sc.save(force=True)
            scc.save(force=True)
            print(f"[cache] saved — subtitle: {len(sc)} | script: {len(scc)}", flush=True)

    print(f"\nDone. {total_rows} rows → {out}", flush=True)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate KukuFM master CSV")
    ap.add_argument("--out",         default=str(OUT_FILE))
    ap.add_argument("--no-content",  action="store_true",
                    help="Skip subtitle/script downloads")
    ap.add_argument("--workers",     type=int, default=10)
    ap.add_argument("--limit-shows", type=int,
                    help="Only process first N show files (testing)")
    args = ap.parse_args()

    run(
        out=Path(args.out),
        fetch_content=not args.no_content,
        workers=args.workers,
        limit_shows=args.limit_shows,
    )
