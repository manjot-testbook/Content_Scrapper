# KukuTV Content Scraper

> **For testing & QA purposes only.**

---

## How It Works (The Short Version)

Modern apps use HTTPS + certificate pinning to prevent traffic inspection.
KukuTV uses **Pairip** (anti-tamper) + **NSC** (only trusts system certs).

**The working approach:**
1. Use `google_apis` AVD — `adb root` works natively (no Magisk needed)
2. Start emulator with `-writable-system` → enables `adb remount`
3. Push mitmproxy CA cert to `/system/etc/security/cacerts/` (system store)
4. Install **original unmodified** KukuTV APKs — Pairip sees correct signature
5. Run mitmproxy → intercepts all HTTPS traffic

---

## Quick Start

### Prerequisites

```bash
# macOS host
brew install ffmpeg mitmproxy
pip install -r requirements.txt

# Android SDK: install via Android Studio
# Required SDK components:
#   platform-tools, emulator, build-tools, cmdline-tools
#   system-images;android-33;google_apis;arm64-v8a         ← capture AVD (rootable)
#   system-images;android-33;google_apis_playstore;arm64-v8a ← APK download AVD
```

### Step 1 — Get KukuTV APKs (one time)

Creates a separate Play Store AVD, lets you install KukuTV, pulls the APKs:

```bash
python3 scripts/setup_apk_downloader_avd.py
# Follow the interactive prompts:
#   1. Sign in to Play Store on the emulator
#   2. Install KukuTV
#   3. Press Enter — APKs saved to apks/
```

### Step 2 — One-time AVD setup (creates KukuCapture + installs cert)

```bash
python3 scripts/root_capture_avd.py
# Fully automated — no interaction needed:
#   1. Creates KukuCapture AVD (google_apis — adb root works natively)
#   2. Boots with -writable-system
#   3. adb root + adb remount + pushes mitmproxy cert to system store
#   Done in ~3 minutes
```

Re-run this only if you delete the KukuCapture AVD.

### Step 3 — Run the capture pipeline (every time)

```bash
python3 GO.py
# Recreate AVD from scratch:
python3 GO.py --scratch   # then re-run root_capture_avd.py
```

`GO.py` will:
1. Start `KukuCapture` with `-writable-system`
2. `adb root` + `adb remount`
3. Push mitmproxy cert to system store (skipped if already there)
4. Install original KukuTV APKs (untouched — Pairip happy)
5. Start `mitmdump` → logs to `metadata/captured_apis/api_traffic.jsonl`
6. Turn proxy **OFF** so OTP login works

### Step 3 — Log in + capture

```
1. KukuTV opens on emulator → log in with OTP
   (proxy is OFF so Play Integrity / OTP auth works cleanly)

2. After login, turn proxy ON:
   ~/Library/Android/sdk/platform-tools/adb shell settings put global http_proxy 10.0.2.2:8080

3. Browse: Home → pick a show → play an episode

4. Analyse:
   python3 scripts/analyze.py
```

---

## Project Structure

```
Content_Scrapper/
├── GO.py                          # Main pipeline — run after root_capture_avd.py
├── requirements.txt
│
├── apks/                          # Original KukuTV APKs (from setup_apk_downloader_avd.py)
│
├── scripts/
│   ├── root_capture_avd.py        # ONE-TIME: rootAVD + Magisk setup for KukuCapture
│   ├── setup_apk_downloader_avd.py  # ONE-TIME: get original KukuTV APKs via Play Store
│   ├── analyze.py
│   ├── kuku_scraper.py
│   └── pull_apks.py
│
├── tools/
│   └── rootAVD/
│       └── rootAVD.sh             # Downloaded by root_capture_avd.py automatically
│
├── mitm_addons/
│   └── mitm_addon.py
│
├── metadata/captured_apis/
│   └── api_traffic.jsonl
│
├── build/                         # Gitignored working dir
└── logs/
```

---

## Two AVDs — Why Both Exist

| AVD | Image | Root | Purpose |
|---|---|---|---|
| `apk_downloader_avd` | `google_apis_playstore` | ❌ | Has Play Store — pull original KukuTV APKs |
| `KukuCapture` | `google_apis` | ✅ | `adb root` works natively — used to intercept traffic |

`google_apis` has `ro.debuggable=1` so `adb root` works without any extra tools. The `-writable-system` emulator flag makes `/system` writable for cert installation.

---

## What Failed (and Why)

### Approach A — Patch APK (NSC inject + resign)
Edit `res/xml/network_security_config.xml` inside `base.apk` to also trust user certs, resign with debug key, install.

**Fails because:** KukuTV ships with `libpairipcore.so` (Pairip SDK). At startup it reads the APK's signing certificate and compares it to the expected Play Store cert. Any mismatch → `SIGABRT` before the app even shows a screen.

Logcat signature:
```
F DEBUG   : #00 pc 0000000000037cbc  .../split_config.arm64_v8a.apk!libpairipcore.so
E ActivityManager: App crashed on incremental package com.vlv.aravali.reels
```

### Approach B — MicroG on google_apis
Replace GMS stub on the rootable image with MicroG so KukuTV gets Play Services.

**Fails because:** Android's package manager refuses to install an APK signed with a different certificate over an existing system app. MicroG's cert ≠ Google's stub cert → `INSTALL_FAILED_UPDATE_INCOMPATIBLE`. Various workarounds (tmpfs overlay, editing `packages.xml`) either don't survive reboot or cause `Can't find service: package` framework crashes.

### Approach C — User cert only
Install mitmproxy CA cert via Android Settings → Security (no root needed).

**Fails because:** KukuTV's NSC is `<certificates src="system"/>` only. User certs are in a different store and are completely ignored.

### Approach D — google_apis without rootAVD/Magisk
Use the `google_apis` debug image with just `adb root` (no full Magisk).

**Fails because:** `google_apis` has a stripped GMS stub. `GmsCoreStatsService` crashes on boot. KukuTV's startup check `isGooglePlayServiceAvailable()` returns false → shows "Something went wrong, Check that Google Play is enabled" dialog and blocks login.

Logcat signature:
```
W ActivityManager: Scheduling restart of crashed service com.google.android.gms/.common.stats.GmsCoreStatsService
D StrictMode: StrictMode policy violation: BugleSurveyCommonConditions.isGooglePlayServiceAvailable
```

### ✅ Working Solution
`google_apis` AVD + `-writable-system` + `adb root` + push cert + **original APK untouched**.

- `adb root` works natively (no Magisk, no rootAVD)
- `-writable-system` makes `/system` writable → `adb remount` works
- System cert → mitmproxy trusted by all apps including KukuTV
- Original APK → Pairip happy

---

## Manual Commands

```bash
# Check a specific API endpoint with the captured token
python3 scripts/kuku_scraper.py --home

# Re-run analysis without re-capturing
python3 scripts/analyze.py

# Enable/disable proxy manually
adb shell settings put global http_proxy 10.0.2.2:8080   # ON
adb shell settings delete global http_proxy               # OFF

# Check what's in the captured traffic
python3 -c "
import json
with open('metadata/captured_apis/api_traffic.jsonl') as f:
    for line in f:
        d = json.loads(line)
        if d.get('is_kuku'):
            print(d['method'], d['url'][:80])
"
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| App opens and immediately closes | Pairip crash — APK was modified. Run `python3 GO.py` (installs original APKs) |
| "Something went wrong / Check Google Play" | GMS dialog on google_apis — try dismissing it, OTP login usually still works |
| `adb remount` fails | Emulator not started with `-writable-system` — GO.py handles this automatically |
| No traffic in api_traffic.jsonl | Proxy not enabled after login — run the `settings put` command above |
| `adb: no devices` | Start emulator first, or run `python3 GO.py` |
| TLS handshake failed in mitm.log | System cert not pushed — restart with `python3 GO.py` |
| `mitmproxy-ca-cert.pem` missing | GO.py generates it automatically |
| 401 on API calls | Token expired — re-run capture |
