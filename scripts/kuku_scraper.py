#!/usr/bin/env python3
"""
KukuTV API Scraper
Captured APIs from live traffic analysis.
Base URL: https://api.kukufm.com
CDN:      https://media.cdn.kukufm.com
"""
import os, json, time, argparse
import requests

# ── Auth (captured from traffic) ─────────────────────────────
# Get session token from captured traffic
TRAFFIC = os.path.join(os.path.dirname(__file__),
                       "../metadata/captured_apis/api_traffic.jsonl")

def get_session_token_from_traffic():
    """Extract the captured session token from mitmproxy traffic."""
    try:
        with open(TRAFFIC) as f:
            for line in f:
                d = json.loads(line)
                host = d.get("host","")
                path = d.get("path","")
                if "kukufm.com" in host and "get-session-token" in path:
                    body = d.get("response_body","")
                    if isinstance(body, dict):
                        return body.get("data",{}).get("token") or body.get("token")
                    if isinstance(body, str):
                        try:
                            bd = json.loads(body)
                            return bd.get("data",{}).get("token") or bd.get("token")
                        except: pass
    except: pass
    return None

def get_headers_from_traffic():
    """Extract auth headers used by the app."""
    headers = {}
    try:
        with open(TRAFFIC) as f:
            for line in f:
                d = json.loads(line)
                if "kukufm.com" in d.get("host","") and "media.cdn" not in d.get("host",""):
                    req_headers = d.get("request_headers", {})
                    for k, v in req_headers.items():
                        kl = k.lower()
                        if kl in ("authorization","x-session-token","x-device-id",
                                  "x-app-version","user-agent","x-platform"):
                            headers[k] = v
                    if headers: break
    except: pass
    return headers


class KukuTVScraper:
    BASE = "https://api.kukufm.com"
    CDN  = "https://media.cdn.kukufm.com"

    def __init__(self, token=None):
        self.session = requests.Session()
        # Load headers from captured traffic
        captured_headers = get_headers_from_traffic()
        self.session.headers.update({
            "User-Agent":    "okhttp/4.12.0",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })
        if captured_headers:
            self.session.headers.update(captured_headers)
            print(f"[+] Loaded {len(captured_headers)} headers from traffic")

        if token:
            self.session.headers["Authorization"] = f"Token {token}"
        else:
            t = get_session_token_from_traffic()
            if t:
                self.session.headers["Authorization"] = f"Token {t}"
                print(f"[+] Using captured session token: {t[:20]}...")

    def _get(self, path, params=None):
        url = self.BASE + path
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    # ── Content APIs (captured from traffic) ─────────────────

    def home(self, language="all"):
        """GET /api/v3/home/all/ — home feed with shows/banners."""
        return self._get(f"/api/v3/home/{language}/")

    def home_v2(self, language="all"):
        """GET /api/v2/home/all/ — alternate home feed."""
        return self._get(f"/api/v2/home/{language}/")

    def show_details(self, show_id):
        """GET /api/v1.2/channels/{id}/details/ — show metadata + episodes."""
        return self._get(f"/api/v1.2/channels/{show_id}/details/")

    def show_episodes(self, show_id, page=1):
        """GET /api/v1.2/channels/{id}/episodes/ — paginated episode list."""
        return self._get(f"/api/v1.2/channels/{show_id}/episodes/",
                         params={"page": page})

    def episode_details(self, episode_id):
        """GET /api/v1.2/episodes/{id}/details/ — single episode."""
        return self._get(f"/api/v1.2/episodes/{episode_id}/details/")

    def next_episode_autoplay(self, episode_id=None):
        """GET /api/v1.2/shows/next-episode-autoplay/ — next episode."""
        params = {"episode_id": episode_id} if episode_id else {}
        return self._get("/api/v1.2/shows/next-episode-autoplay/", params=params)

    def master_config(self):
        """POST /api/v1.0/config/master/android/ — app config."""
        r = self.session.post(self.BASE + "/api/v1.0/config/master/android/",
                              json={}, timeout=15)
        return r.json()

    def search(self, query, page=1):
        """Search shows."""
        return self._get("/api/v1.0/search/", params={"q": query, "page": page})

    def categories(self):
        """GET categories/genres."""
        return self._get("/api/v1.0/categories/")

    def trending(self):
        """GET trending shows."""
        return self._get("/api/v1.0/channels/trending/")

    def get_stream_url(self, episode_id, quality="720p"):
        """Build HLS stream URL from captured CDN pattern."""
        return f"{self.CDN}/video-episode/{episode_id}/master.m3u8"

    # ── Download ──────────────────────────────────────────────

    def download_episode(self, episode_id, output_dir="downloads"):
        """Download episode video using captured HLS URL pattern."""
        import subprocess
        os.makedirs(output_dir, exist_ok=True)

        # Get episode details to find stream URL
        try:
            ep = self.episode_details(episode_id)
            title = ep.get("data",{}).get("title", episode_id)
        except:
            title = str(episode_id)

        # Try direct HLS URL from CDN pattern captured in traffic
        # Pattern: media.cdn.kukufm.com/video-episode/{uuid}/{id}-v1/{quality}/master.m3u8
        out_file = os.path.join(output_dir, f"{episode_id}.mp4")
        hls_url  = self.get_stream_url(episode_id)

        print(f"  Downloading: {title}")
        print(f"  HLS: {hls_url}")

        cmd = ["ffmpeg", "-y", "-i", hls_url,
               "-c", "copy", "-bsf:a", "aac_adtstoasc", out_file]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  Saved: {out_file}")
            return out_file
        else:
            print(f"  ffmpeg error: {r.stderr[-200:]}")
            return None


def main():
    ap = argparse.ArgumentParser(description="KukuTV API Scraper")
    ap.add_argument("--token",    help="Session token (auto-loaded from traffic)")
    ap.add_argument("--home",     action="store_true", help="Fetch home feed")
    ap.add_argument("--show",     type=int,  help="Show/channel ID to fetch")
    ap.add_argument("--episode",  type=int,  help="Episode ID")
    ap.add_argument("--search",   help="Search query")
    ap.add_argument("--trending", action="store_true")
    ap.add_argument("--download", type=int,  help="Episode ID to download")
    ap.add_argument("--output",   default="downloads", help="Download directory")
    ap.add_argument("--dump",     action="store_true",
                    help="Dump all captured API calls summary")
    args = ap.parse_args()

    if args.dump:
        dump_captured_apis()
        return

    scraper = KukuTVScraper(token=args.token)

    if args.home:
        print("\n[HOME FEED]")
        data = scraper.home()
        sections = data.get("data", data)
        if isinstance(sections, list):
            for s in sections[:5]:
                print(f"  Section: {s.get('title','?')} ({len(s.get('channels',[]))} shows)")
        else:
            print(json.dumps(data, indent=2)[:1000])

    if args.show:
        print(f"\n[SHOW {args.show}]")
        data = scraper.show_details(args.show)
        print(json.dumps(data, indent=2)[:2000])

    if args.episode:
        print(f"\n[EPISODE {args.episode}]")
        data = scraper.episode_details(args.episode)
        print(json.dumps(data, indent=2)[:2000])

    if args.search:
        print(f"\n[SEARCH: {args.search}]")
        data = scraper.search(args.search)
        print(json.dumps(data, indent=2)[:2000])

    if args.trending:
        print("\n[TRENDING]")
        data = scraper.trending()
        print(json.dumps(data, indent=2)[:2000])

    if args.download:
        print(f"\n[DOWNLOAD episode {args.download}]")
        scraper.download_episode(args.download, args.output)

    if not any([args.home, args.show, args.episode, args.search,
                args.trending, args.download, args.dump]):
        # Default: show captured API summary
        dump_captured_apis()


def dump_captured_apis():
    """Print a summary of all captured KukuTV API calls."""
    print("\n=== Captured KukuTV API Calls ===\n")
    seen = set()
    try:
        with open(TRAFFIC) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if not d.get("is_kuku"): continue
                    host = d.get("host","")
                    path = d.get("path","")
                    method = d.get("method","GET")
                    key = f"{method} {host}{path}"
                    if key in seen: continue
                    seen.add(key)
                    print(f"  [{d.get('status','?')}] {method} https://{host}{path}")
                except: pass
    except FileNotFoundError:
        print("  No traffic file found. Run capture first.")
    print(f"\nTotal unique endpoints: {len(seen)}")


if __name__ == "__main__":
    main()
