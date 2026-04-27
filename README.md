# KukuTV Content Scraper

> **For testing & QA purposes only.**

---

## How It Works (The Short Version)

Modern apps use HTTPS + certificate pinning to prevent traffic inspection.
KukuTV uses **Pairip** (anti-tamper) + **NSC** (only trusts system certs).

Two approaches were tried — one failed, one works:

| Approach | Result | Why |
|---|---|---|
| Patch APK (inject NSC) + resign | ❌ Crash on launch | Pairip detects signature mismatch → `SIGABRT` |
| Original APK + system cert | ✅ Works | Pairip sees original signature; mitmproxy cert is in system store |

**The working approach:**
1. Use a `google_apis` AVD (rootable — `adb root` works)
2. Start emulator with `-writable-system`
3. `adb root` → `adb remount` → push mitmproxy CA cert to `/system/etc/security/cacerts/`
4. Install **original unmodified** APKs from the Play Store
5. Pairip is happy (original signature) + mitmproxy is trusted (system cert)

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

### Step 2 — Run the capture pipeline

```bash
# Normal run (reuses existing KukuCapture AVD if present)
python3 GO.py

# Fresh start (deletes + recreates KukuCapture AVD from scratch)
python3 GO.py --scratch
```

`GO.py` will:
1. Start the `KukuCapture` AVD with `-writable-system`
2. `adb root` + `adb remount`
3. Push mitmproxy CA cert into `/system/etc/security/cacerts/`
4. Install original KukuTV APKs (untouched)
5. Start `mitmdump` → logs all traffic to `metadata/captured_apis/api_traffic.jsonl`
6. Turn proxy **OFF** (so OTP login works)

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
├── GO.py                          # Master script — start here
├── requirements.txt
│
├── apks/                          # Original KukuTV APKs (from setup_apk_downloader_avd.py)
│   ├── base.apk
│   ├── split_config.arm64_v8a.apk
│   └── split_config.xxhdpi.apk
│
├── scripts/
│   ├── setup_apk_downloader_avd.py  # Creates Play Store AVD, installs KukuTV, pulls APKs
│   ├── analyze.py                   # Parses api_traffic.jsonl → API catalog
│   ├── kuku_scraper.py              # Makes direct API calls using captured session token
│   └── pull_apks.py                 # Standalone: pull APKs from any running device
│
├── mitm_addons/
│   └── mitm_addon.py              # mitmproxy addon: logs all traffic to JSONL
│
├── metadata/
│   ├── captured_apis/
│   │   └── api_traffic.jsonl      # Raw captured traffic (written by mitm_addon.py)
│   └── api_catalog/
│       └── all_series.json
│
├── build/                         # Working dir for patching/signing (gitignored)
└── logs/
    ├── emulator.log
    └── mitm.log
```

---

## Two AVDs — Why Both Exist

| AVD | Image | Root | Purpose |
|---|---|---|---|
| `apk_downloader_avd` | `google_apis_playstore` | ❌ | Has Play Store — used to download original KukuTV APKs |
| `KukuCapture` | `google_apis` | ✅ | Rootable — used to intercept traffic with system cert |

You can't use one for both: Play Store images block root; rootable images have no Play Store.

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

### ✅ Working Solution
`google_apis` AVD + `-writable-system` + `adb root` + push cert to system store + **original APK untouched**.

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
| App opens and immediately closes | APK was tampered (Pairip). Use `python3 GO.py --scratch` to reinstall original APK |
| No traffic in api_traffic.jsonl | Proxy not enabled after login — run the `settings put` command above |
| `adb: device offline` after root | `adb root` restarts adbd — wait 3s and retry |
| `adb remount` fails | Emulator not started with `-writable-system` — use `GO.py` which adds this flag |
| `adb: no devices` | Start emulator first, or run `python3 GO.py` |
| TLS handshake failed in mitm.log | System cert not pushed — re-run `python3 GO.py --scratch` |
| `mitmproxy-ca-cert.pem` missing | GO.py generates it automatically on first run |
| 401 on API calls | Token expired — re-run capture to get fresh session |
