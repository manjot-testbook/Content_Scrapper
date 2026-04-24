# KukuTV Content Scrapper

## Overview
Reverse-engineer KukuTV backend APIs by intercepting network traffic, then scrape and download video content.

## Pipeline
1. **Install APKM** → Extract split APKs, install via `adb install-multiple`
2. **MITM Proxy** → Intercept HTTPS traffic to discover API endpoints
3. **Appium Automation** → Navigate the app to trigger all API calls
4. **API Catalog** → Analyze & document discovered endpoints
5. **Scraper** → Download videos and metadata locally

## Setup

### Prerequisites
- Android SDK + emulator (or rooted device)
- Appium Server (`npm install -g appium`)
- Python 3.10+
- `adb` on PATH
- `ffmpeg` installed (`brew install ffmpeg`)

### Install
```bash
pip install -r requirements.txt
```

### Quick Start
```bash
# 1. Place your .apkm file in the apkm/ directory

# 2. Install the app on device/emulator
python scripts/install_apkm.py --apkm apkm/kukutv.apkm

# 3. Start MITM proxy (captures API traffic)
python scripts/start_proxy.py

# 4. Run Appium automation to explore the app
python scripts/appium_navigator.py

# 5. Analyze captured APIs
python scripts/analyze_apis.py

# 6. Scrape & download videos
python scripts/scraper.py
```

## Directory Structure
```
apkm/           - Place .apkm files here
scripts/         - All automation & scraping scripts
mitm_addons/     - mitmproxy addon scripts
videos/          - Downloaded videos (organized by category)
metadata/        - API responses & video metadata (JSON/SQLite)
logs/            - Execution logs
```
