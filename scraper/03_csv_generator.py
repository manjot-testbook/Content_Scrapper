#!/usr/bin/env python3
"""
Script 4 -- KukuFM Master CSV Generator
==========================================
Reads every show_*_episodes.json file from metadata/api_catalog/episodes/
and produces a single flat CSV at metadata/kuku_master.csv.

Each row = one episode, columns:
  Series metadata (id, title, description, language, genre, ...)
  + Episode metadata (id, title, index, duration_s, n_plays, subtitle_url, ...)
  + subtitle_text     (plain text from the .srt subtitle file)
  + episode_script    (plain text extracted from the .docx script file)

Run:
    python scraper/03_csv_generator.py
    python scraper/03_csv_generator.py --out metadata/my_export.csv --workers 8
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
from threading import local as thread_local

import requests

# python-docx (already installed)
try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False
    print("[warn] python-docx not installed. Script columns will be empty.")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.auth import get_auth_headers

# ── paths ──────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent.parent
CATALOG_DIR  = BASE_DIR / "metadata" / "api_catalog"
EPISODES_DIR = CATALOG_DIR / "episodes"
OUT_FILE     = BASE_DIR / "metadata" / "kuku_master.csv"

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


# ── HTTP helpers ───────────────────────────────────────────────────────────────

_thread_local = thread_local()

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(get_auth_headers())
    return s


def get_session() -> requests.Session:
    """Per-thread session – avoids race conditions in ThreadPoolExecutor."""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = _make_session()
    return _thread_local.session


def fetch_url(url: str, binary: bool = False, retries: int = 3) -> bytes | str | None:
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
        except Exception as exc:
            time.sleep(2)
    return None


# ── Subtitle (.srt) extractor ──────────────────────────────────────────────────

_SRT_CUE_RE = re.compile(
    r"^\d+\s*\n"                        # sequence number
    r"[\d:,]+ --> [\d:,]+[^\n]*\n"     # timecode
    r"((?:.+\n?)+)",                    # text lines
    re.MULTILINE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def extract_subtitle_text(url: str) -> str:
    """Download .srt and return plain text (cues only, no timecodes)."""
    raw = fetch_url(url)
    if not raw:
        return ""
    lines = []
    for m in _SRT_CUE_RE.finditer(raw):
        cue = m.group(1).strip()
        cue = _HTML_TAG_RE.sub("", cue)
        lines.append(cue)
    return " ".join(lines).strip()


# ── Script (.docx) extractor ───────────────────────────────────────────────────

def extract_docx_text(url: str) -> str:
    """Download .docx and extract all paragraph text using python-docx."""
    if not HAS_DOCX or not url:
        return ""
    raw = fetch_url(url, binary=True)
    if not raw:
        return ""
    try:
        doc = DocxDocument(io.BytesIO(raw))
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paras)
    except Exception as exc:
        return f"[docx-error: {exc}]"


# ── Row builder ────────────────────────────────────────────────────────────────

def _show_row(show: dict) -> dict:
    """Extract show-level fields, prefixed with show_."""
    return {
        "show_id":                 show.get("id"),
        "show_slug":               show.get("slug"),
        "show_title":              show.get("title"),
        "show_description":        show.get("description"),
        "show_language":           show.get("language") or show.get("lang"),
        "show_status":             show.get("status"),
        "show_n_episodes":         show.get("n_episodes"),
        "show_n_seasons":          show.get("n_seasons"),
        "show_n_listens":          show.get("n_listens"),
        "show_duration_s":         show.get("duration_s"),
        "show_is_premium":         show.get("is_premium"),
        "show_monetization_type":  show.get("monetization_type"),
        "show_overall_rating":     show.get("overall_rating"),
        "show_n_reviews":          show.get("n_reviews"),
        "show_is_fictional":       show.get("is_fictional"),
        "show_genre":              show.get("genre"),
        "show_tropes":             json.dumps(show.get("tropes", []), ensure_ascii=False),
        "show_app_tags":           json.dumps(show.get("app_tags", []), ensure_ascii=False),
        "show_author":             show.get("author"),
        "show_content_type":       show.get("content_type"),
        "show_is_adult_content":   show.get("is_adult_content"),
        "show_is_safe_for_kids":   show.get("is_safe_for_kids"),
        "show_published_on":       show.get("published_on"),
        "show_image":              show.get("image"),
        "show_dynamic_link":       show.get("dynamic_link"),
        "show_uri":                show.get("uri"),
        "show_sharing_text":       show.get("sharing_text"),
        "show_n_impressions":      show.get("n_impressions"),
        "show_users_completion_p": show.get("users_completion_p"),
        "show_recommendation_score": show.get("recommendation_score"),
    }


def _episode_row(ep: dict) -> dict:
    """Extract episode-level fields, prefixed with episode_."""
    return {
        "episode_id":             ep.get("id"),
        "episode_slug":           ep.get("slug"),
        "episode_title":          ep.get("title"),
        "episode_index":          ep.get("index"),
        "episode_status":         ep.get("status"),
        "episode_season_no":      ep.get("season_no"),
        "episode_duration_s":     ep.get("duration_s"),
        "episode_published_on":   ep.get("published_on"),
        "episode_is_premium":     ep.get("is_premium"),
        "episode_is_locked":      ep.get("is_locked"),
        "episode_is_free_unlocked": ep.get("is_free_unlocked"),
        "episode_n_plays":        ep.get("n_plays"),
        "episode_video_hls_url":  ep.get("video_hls_url"),
        "episode_subtitle_url":   ep.get("subtitle_url"),
        "episode_audio_url":      ep.get("audio_url"),
        "episode_show_script_url": ep.get("show_script_url"),
        "episode_thumbnail":      ep.get("thumbnail"),
        "episode_reel_image":     ep.get("reel_image"),
        "episode_image":          ep.get("image"),
    }


def build_row(show_fields: dict, ep: dict, fetch_content: bool = True) -> dict:
    row = {}
    row.update(show_fields)
    row.update(_episode_row(ep))

    if fetch_content:
        row["subtitle_text"]   = extract_subtitle_text(ep.get("subtitle_url") or "")
        row["episode_script"]  = extract_docx_text(ep.get("show_script_url") or "")
    else:
        row["subtitle_text"]   = ""
        row["episode_script"]  = ""

    return row


# ── Main ───────────────────────────────────────────────────────────────────────

def collect_episode_files() -> list[Path]:
    if not EPISODES_DIR.exists():
        return []
    return sorted(EPISODES_DIR.glob("show_*_episodes.json"))


def run(out: Path = OUT_FILE,
        fetch_content: bool = True,
        workers: int = 12,
        limit_shows: int | None = None):

    files = collect_episode_files()
    if not files:
        print(f"[error] No episode files found in {EPISODES_DIR}")
        print("  Run 01_series_scraper.py and 02_episodes_scraper.py first.")
        sys.exit(1)

    if limit_shows:
        files = files[:limit_shows]

    print(f"[csv] Found {len(files)} show episode files.")
    out.parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    with open(out, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=ALL_COLS, extrasaction="ignore")
        writer.writeheader()

        for file_idx, fpath in enumerate(files, 1):
            try:
                with open(fpath) as fh:
                    data = json.load(fh)
            except Exception as exc:
                print(f"  [warn] Could not read {fpath.name}: {exc}")
                continue

            show   = data.get("show", {})
            episodes = data.get("episodes", [])
            show_fields = _show_row(show)
            show_title = show.get("title", fpath.stem)
            print(f"  [{file_idx}/{len(files)}] {show_title}: {len(episodes)} episodes")

            if not fetch_content or workers <= 1:
                # Sequential
                for ep in episodes:
                    row = build_row(show_fields, ep, fetch_content=fetch_content)
                    writer.writerow(row)
                    total_rows += 1
            else:
                # Parallel content fetching
                def _fetch_ep(ep):
                    return build_row(show_fields, ep, fetch_content=True)

                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_fetch_ep, ep): ep for ep in episodes}
                    for future in as_completed(futures):
                        try:
                            row = future.result()
                            writer.writerow(row)
                            total_rows += 1
                        except Exception as exc:
                            print(f"    [ep-error] {exc}")

    print(f"\nDone. {total_rows} rows written to {out}")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate master KukuFM CSV")
    ap.add_argument("--out",           default=str(OUT_FILE),
                    help="Output CSV path")
    ap.add_argument("--no-content",    action="store_true",
                    help="Skip downloading subtitle/script text (faster)")
    ap.add_argument("--workers",       type=int, default=12,
                    help="Parallel workers for content downloads (default 12)")
    ap.add_argument("--limit-shows",   type=int,
                    help="Process only first N show files (for testing)")
    args = ap.parse_args()

    run(
        out=Path(args.out),
        fetch_content=not args.no_content,
        workers=args.workers,
        limit_shows=args.limit_shows,
    )

