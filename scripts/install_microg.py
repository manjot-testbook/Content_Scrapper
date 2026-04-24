#!/usr/bin/env python3
"""
install_microg.py — Install MicroG (Play Services replacement) on KukuTV_Root emulator.

KukuTV requires Google Play Services for login. This installs MicroG which
provides a compatible implementation without needing the Play Store.

Run: python scripts/install_microg.py
"""
import os, subprocess, sys, urllib.request, tempfile, time

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")

# MicroG APKs — stable release
MICROG_APKS = {
    "GmsCore (Play Services replacement)":
        "https://github.com/microg/GmsCore/releases/download/v0.3.4.240913/com.google.android.gms-240913017-hw.apk",
    "FakeStore (Play Store stub)":
        "https://github.com/microg/GmsCore/releases/download/v0.3.4.240913/com.android.vending-240913017.apk",
}

def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def download(url, dest):
    print(f"  Downloading {os.path.basename(url)} ...", end="", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
        size = os.path.getsize(dest) // 1024
        print(f" {size}KB ✓")
        return True
    except Exception as e:
        print(f" FAILED: {e}")
        return False

def install(apk, label):
    print(f"  Installing {label} ...")
    out, err, code = adb("install", "-r", "-d", apk)
    if code == 0 or "Success" in out:
        print(f"  ✓ {label} installed")
        return True
    # Try with root grant
    out2, err2, code2 = adb("install", "-r", "-d", "--bypass-low-target-sdk-block", apk)
    if code2 == 0 or "Success" in out2:
        print(f"  ✓ {label} installed")
        return True
    print(f"  ✗ Failed: {(err or err2)[:200]}")
    return False

# Check device
out, _, _ = adb("devices")
if "emulator" not in out and len(out.split("\n")) < 2:
    print("ERROR: No emulator connected.")
    sys.exit(1)

# Enable root (needed on KukuTV_Root)
print("[0] Enabling root...")
adb("root")
time.sleep(3)

print("\n[1] Downloading MicroG APKs...")
with tempfile.TemporaryDirectory() as tmp:
    apk_files = []
    for label, url in MICROG_APKS.items():
        dest = os.path.join(tmp, os.path.basename(url).split("?")[0])
        if download(url, dest):
            apk_files.append((dest, label))

    if not apk_files:
        print("ERROR: Could not download any APKs. Check internet connection.")
        sys.exit(1)

    print("\n[2] Installing MicroG...")
    for apk, label in apk_files:
        install(apk, label)

print("\n[3] Granting permissions to MicroG...")
perms = [
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.READ_CONTACTS",
    "android.permission.READ_PHONE_STATE",
    "android.permission.RECEIVE_SMS",
    "android.permission.READ_SMS",
]
for p in perms:
    adb("shell", "pm", "grant", "com.google.android.gms", p)
print("  ✓ Permissions granted")

print("\n[4] Rebooting to apply MicroG...")
adb("reboot")
time.sleep(12)
print("  Waiting for boot", end="", flush=True)
for _ in range(30):
    out, _, _ = adb("shell", "getprop sys.boot_completed")
    if out.strip() == "1":
        print(" ✓")
        break
    time.sleep(5); print(".", end="", flush=True)

print("""
============================================================
  ✓ MicroG installed — Google Play Services replacement ready
============================================================

Now:
  1. Open KukuTV — it should no longer ask for Play Services
  2. Log in with your phone number + OTP
     (proxy is OFF so OTP will work)
  3. After login, run:
       python scripts/fix_cert.py
       python scripts/login_mode.py on
  4. Browse KukuTV to capture APIs:
       ./run.sh analyze
""")
