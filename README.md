# KukuTV Content Scraper

> **For testing & QA purposes only.** Built to help testers at Kuku understand what data the app exposes via its backend APIs.

## Overview

This toolkit:
1. **Intercepts API traffic** from the KukuTV Android app using `mitmproxy`
2. **Navigates the app** automatically via `Appium` to trigger all API endpoints
3. **Bypasses SSL pinning** (if present) using `Frida`
4. **Analyzes** the captured endpoints and video URLs
5. **Downloads** video/audio content using `ffmpeg` / `yt-dlp`

---

## Prerequisites

### macOS host
```bash
brew install ffmpeg
pip install -r requirements.txt
# Install Appium server
npm install -g appium
appium driver install uiautomator2
```

### Android Emulator (Android Studio)
- Create an AVD with **Google Play Store** (Pixel 6, API 33+)
- Start the emulator from Android Studio
- Log in with a Google account and **install KukuTV from Play Store** (`com.vlv.aravali.reels`)

### mitmproxy CA Certificate (one-time)
```bash
# Generate the cert (run once)
mitmdump --listen-port 8080 &
sleep 2 && kill %1

# Push cert to emulator
adb push ~/.mitmproxy/mitmproxy-ca-cert.cer /sdcard/Download/
# On emulator: Settings → Security → Encryption & credentials → Install a certificate → CA Certificate
```

---

## Quick Start

```bash
# Check emulator + app status
./run.sh status

# Full pipeline: proxy + navigate + analyze + download
./run.sh all

# Individual steps
./run.sh proxy        # Start mitmproxy + configure emulator proxy
./run.sh navigate     # Appium automation (needs: appium --port 4723)
./run.sh analyze      # Parse captured traffic → API catalog
./run.sh scrape       # Download discovered videos
./run.sh stop         # Stop proxy, clear device proxy setting
```

### If app has SSL pinning (no traffic captured)
```bash
# Option A: Frida (requires rooted emulator + frida-server on device)
adb push frida-server /data/local/tmp/
adb shell chmod +x /data/local/tmp/frida-server
adb shell "/data/local/tmp/frida-server &"
./run.sh bypass --spawn &
./run.sh capture

# Option B: objection (easier)
pip install objection
objection -g com.vlv.aravali.reels explore
# Then inside objection shell:
android sslpinning disable
```

---

## Project Structure

```
Content_Scrapper/
├── run.sh                        # Master script — start here
├── requirements.txt
│
├── mitm_addons/
│   ├── mitm_addon.py             # mitmproxy addon: logs all traffic to JSONL
│   └── frida_ssl_bypass.js       # Frida script: disables SSL/TLS pinning
│
├── scripts/
│   ├── capture_pipeline.py       # Full pipeline: proxy → launch → capture
│   ├── appium_navigator.py       # Appium automation: navigates app screens
│   ├── bypass_ssl_pinning.py     # Python wrapper for Frida bypass
│   ├── analyze_apis.py           # Parses JSONL → API endpoint catalog
│   ├── quick_analyze.py          # Fast traffic summary
│   ├── scraper.py                # Downloads videos from discovered URLs
│   ├── start_proxy.py            # Standalone proxy launcher
│   └── install_apkm.py           # Install split APK from .apkm file
│
├── metadata/
│   ├── captured_apis/
│   │   └── api_traffic.jsonl     # Raw captured traffic (one JSON per line)
│   └── api_catalog/
│       └── api_catalog.json      # Parsed API endpoint catalog
│
├── videos/                       # Downloaded video files
└── logs/
    ├── mitm.log                  # mitmproxy output
    └── traffic_summary.json      # Quick analysis output
```

---

## How It Works

### Step 1 — Traffic Capture
`mitm_addon.py` intercepts every HTTP(S) request/response and writes structured records to `metadata/captured_apis/api_traffic.jsonl`:

```json
{
  "method": "GET",
  "url": "https://api.kukutv.com/v2/content/home",
  "host": "api.kukutv.com",
  "path": "/v2/content/home",
  "status_code": 200,
  "response_body": { "shows": [...] },
  "request_headers": { "Authorization": "Bearer eyJ..." },
  "is_kukutv": true
}
```

### Step 2 — API Analysis
`analyze_apis.py` parses the JSONL and produces `metadata/api_catalog/api_catalog.json`:
- All unique endpoints with method, host, path
- Auth headers (tokens, API keys)
- Video/stream URLs (`.m3u8`, `.mp4`, `.mpd`)
- Sample responses

### Step 3 — Video Download
`scraper.py` reads the catalog and downloads:
- HLS streams (`.m3u8`) → `ffmpeg`
- DASH streams (`.mpd`) → `yt-dlp`
- Direct MP4/MP3 → `httpx` streaming

---

## Manual Commands

```bash
# Download a specific video URL
python scripts/scraper.py --url "https://cdn.example.com/episode.m3u8" --output videos/ep.mp4

# Analyze traffic without running the full pipeline
python scripts/analyze_apis.py

# Quick summary
python scripts/quick_analyze.py && cat logs/traffic_summary.json | python -m json.tool
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No traffic captured | App uses SSL pinning → run `./run.sh bypass` |
| `adb: no devices` | Start Android emulator first |
| Appium connection refused | Run `appium --port 4723` in a separate terminal |
| `ffmpeg not found` | `brew install ffmpeg` |
| TLS handshake failed in `mitm.log` | Install mitmproxy CA cert on emulator (see Prerequisites) |
| 401 errors on API calls | Auth token expired — re-run capture to get fresh token |
