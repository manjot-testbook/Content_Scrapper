#!/usr/bin/env python3
"""
setup_apk_downloader_avd.py

Creates a dedicated AVD (apk_downloader_avd) using a Google Play Store image,
boots it, waits for you to:
  1. Sign in to the Play Store
  2. Install KukuTV

Then automatically pulls all KukuTV APKs into <project_root>/apks/

Usage:
    python3 scripts/setup_apk_downloader_avd.py
"""

import os
import subprocess
import sys
import time
import shutil

# ── Config ────────────────────────────────────────────────────────────────────
SDK           = os.path.expanduser("~/Library/Android/sdk")
ADB           = os.path.join(SDK, "platform-tools", "adb")
EMULATOR_BIN  = os.path.join(SDK, "emulator", "emulator")
SDKMANAGER    = os.path.join(SDK, "cmdline-tools", "latest", "bin", "sdkmanager")
AVDMANAGER    = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")

AVD_NAME      = "apk_downloader_avd"
# Google Play Store (non-rooted) image — needed to access Play Store
SYSTEM_IMAGE  = "system-images;android-33;google_apis_playstore;arm64-v8a"
DEVICE_PROFILE = "pixel_6"
PACKAGE       = "com.vlv.aravali.reels"

HERE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
APK_OUT       = os.path.join(HERE, "apks")


# ── Helpers ───────────────────────────────────────────────────────────────────
def run(*cmd, check=False, timeout=120):
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        print(f"  ERROR: {r.stderr.strip() or r.stdout.strip()}")
        sys.exit(1)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args, timeout=60):
    return run(ADB, *args, timeout=timeout)

def banner(msg):
    print(f"\n{'='*52}")
    print(f"  {msg}")
    print(f"{'='*52}")


# ── Step 1: Install system image ──────────────────────────────────────────────
banner("Step 1 — Installing system image (if needed)")
print(f"  Image : {SYSTEM_IMAGE}")
print("  This may take a few minutes on first run...\n")
subprocess.run(
    [SDKMANAGER, "--install", SYSTEM_IMAGE],
    check=True,
    timeout=600,
)
print("  ✓ System image ready")


# ── Step 2: Create AVD ──────────────────────��─────────────────────────────────
banner("Step 2 — Creating AVD")

# Check if AVD already exists
existing, _, _ = run(AVDMANAGER, "list", "avd")
if AVD_NAME in existing:
    ans = input(f"  AVD '{AVD_NAME}' already exists. Recreate? [y/N] ").strip().lower()
    if ans == "y":
        run(AVDMANAGER, "delete", "avd", "--name", AVD_NAME)
        print(f"  Deleted existing '{AVD_NAME}'")
    else:
        print("  Using existing AVD — skipping creation.")

if AVD_NAME not in run(AVDMANAGER, "list", "avd")[0]:
    result = subprocess.run(
        [AVDMANAGER, "create", "avd",
         "--name", AVD_NAME,
         "--package", SYSTEM_IMAGE,
         "--device", DEVICE_PROFILE,
         "--force"],
        input="no\n",          # decline custom hardware profile
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        sys.exit(1)
    print(f"  ✓ AVD '{AVD_NAME}' created")


# ── Step 3: Start emulator ────────────────────────────────────────────────────
banner("Step 3 — Starting emulator")

# Kill any stale adb server so we get a clean device list
adb("kill-server", timeout=10)
time.sleep(1)
adb("start-server", timeout=10)

logs_dir = os.path.join(HERE, "logs")
os.makedirs(logs_dir, exist_ok=True)
log_path = os.path.join(logs_dir, "apk_downloader_avd.log")

emulator_proc = subprocess.Popen(
    [EMULATOR_BIN, "-avd", AVD_NAME,
     "-no-snapshot-save",
     "-no-audio",
     "-gpu", "swiftshader_indirect"],
    stdout=open(log_path, "w"),
    stderr=subprocess.STDOUT,
)

print(f"  Emulator PID : {emulator_proc.pid}")
print(f"  Log          : {log_path}")
print("  Waiting for boot", end="", flush=True)

booted = False
for _ in range(90):          # up to 7.5 minutes
    time.sleep(5)
    try:
        out, _, _ = adb("shell", "getprop", "sys.boot_completed", timeout=8)
        if out.strip() == "1":
            booted = True
            print(" ✓")
            break
    except Exception:
        pass
    print(".", end="", flush=True)

if not booted:
    print("\n  ERROR: Emulator did not boot in time. Check logs/apk_downloader_avd.log")
    sys.exit(1)

# Give the launcher a moment to settle
time.sleep(5)


# ── Step 4: Wait for Play Store login ────────────────────────────────────────
banner("Step 4 — Sign in to Play Store")
print("""
  The emulator is running.

  Please:
    1. Open the Play Store app on the emulator
    2. Sign in with your Google account
    3. Come back here when you are signed in
""")
input("  Press ENTER once you are signed in to Play Store ▶  ")


# ── Step 5: Wait for KukuTV installation ─────────────────────────────────────
banner("Step 5 — Install KukuTV")
print(f"""
  Now install KukuTV from Play Store:
    • Search for "KukuTV" — package: {PACKAGE}
    • Tap Install and wait for it to complete fully

  Tip: You can also run in another terminal:
    {ADB} shell am start -a android.intent.action.VIEW \\
      -d "market://details?id={PACKAGE}"
""")
input("  Press ENTER once KukuTV is fully installed ▶  ")

# Verify the package is present
out, _, _ = adb("shell", "pm", "path", PACKAGE, timeout=15)
paths = [l.split("package:")[-1].strip() for l in out.splitlines() if "package:" in l]
if not paths:
    print(f"\n  ERROR: {PACKAGE} still not found on device.")
    print("  Make sure installation finished, then re-run this script.")
    sys.exit(1)

print(f"  ✓ Found {len(paths)} APK(s) for {PACKAGE}")


# ── Step 6: Pull APKs ─────────────────────────────────────────────────────────
banner("Step 6 — Pulling APKs")

# Clean destination
if os.path.isdir(APK_OUT):
    shutil.rmtree(APK_OUT)
os.makedirs(APK_OUT)

pulled = []
for p in paths:
    name = os.path.basename(p)
    dest = os.path.join(APK_OUT, name)
    _, err, code = adb("pull", p, dest, timeout=120)
    if code == 0 and os.path.isfile(dest):
        size_kb = os.path.getsize(dest) // 1024
        print(f"  ✓ {name}  ({size_kb} KB)")
        pulled.append(dest)
    else:
        print(f"  ✗ {name}: {err}")

if not pulled:
    print("  ERROR: No APKs were pulled successfully.")
    sys.exit(1)


# ── Done ─────────────────────────────────────────────────────────────────────
banner("Done ✓")
print(f"""
  {len(pulled)} APK(s) saved to:
    {APK_OUT}

  Files:
""")
for f in sorted(os.listdir(APK_OUT)):
    fpath = os.path.join(APK_OUT, f)
    print(f"    • {f}  ({os.path.getsize(fpath)//1024} KB)")

print(f"""
  You can now run the main pipeline:
    python3 GO.py
""")

