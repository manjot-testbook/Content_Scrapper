#!/usr/bin/env python3
"""
install_microg.py — Install MicroG (Play Services replacement) on KukuTV_Root emulator.

KukuTV requires Google Play Services for login. This installs MicroG which
provides a compatible implementation without needing the Play Store.

Run: python scripts/install_microg.py
"""
import os, subprocess, sys, urllib.request, urllib.error, tempfile, time, json

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")

def get_microg_urls():
    """Fetch latest MicroG APK URLs from GitHub API."""
    print("  Fetching latest MicroG release info from GitHub...")
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/microg/GmsCore/releases/latest",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        urls = {}
        for asset in data.get("assets", []):
            name = asset["name"]
            url  = asset["browser_download_url"]
            if "com.google.android.gms" in name and name.endswith(".apk"):
                urls["GmsCore (Play Services)"] = url
            elif "com.android.vending" in name and name.endswith(".apk"):
                urls["FakeStore (Play Store stub)"] = url
        if urls:
            print(f"  Found {len(urls)} APK(s) in release {data.get('tag_name')}")
            return urls
    except Exception as e:
        print(f"  GitHub API failed: {e}")

    # Hardcoded fallback — latest known working
    print("  Using hardcoded fallback URLs...")
    tag = "v0.3.15.250932"
    base = f"https://github.com/microg/GmsCore/releases/download/{tag}"
    return {
        "GmsCore (Play Services)": f"{base}/com.google.android.gms-250932037-hw.apk",
        "FakeStore (Play Store stub)": f"{base}/com.android.vending-84022630-hw.apk",
    }

MICROG_APKS = get_microg_urls()

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

def uninstall_system_app(package):
    """Remove system app updates and disable so MicroG can replace it."""
    print(f"  Uninstalling system {package} ...")
    # Remove user-installed updates first
    adb("shell", "pm", "uninstall", "-k", "--user", "0", package)
    # If still present as system app, delete the APK via root
    out, _, _ = adb("shell", f"pm path {package}")
    for line in out.splitlines():
        if "package:" in line:
            path = line.split("package:")[-1].strip()
            # Only delete if it's in /system or /data/app
            if path:
                adb("shell", f"rm -f {path}")
                # Also remove base dir
                adb("shell", f"rm -rf {os.path.dirname(path)}")
    print(f"  ✓ Removed {package}")

# Check device
out, _, _ = adb("devices")
if "emulator" not in out and len(out.split("\n")) < 2:
    print("ERROR: No emulator connected.")
    sys.exit(1)

# Enable root (needed on KukuTV_Root)
print("[0] Enabling root + remount...")
adb("root")
time.sleep(4)
adb("remount")
time.sleep(2)

print("\n[1] Removing existing Google Play Services / Play Store (system apps)...")
for pkg in ["com.google.android.gms", "com.android.vending"]:
    uninstall_system_app(pkg)

print("\n[2] Rebooting to clear package manager state...")
adb("reboot")
time.sleep(12)
print("  Waiting for boot", end="", flush=True)
for _ in range(30):
    out, _, _ = adb("shell", "getprop sys.boot_completed")
    if out.strip() == "1": print(" ✓"); break
    time.sleep(5); print(".", end="", flush=True)
adb("root"); time.sleep(4)

print("\n[3] Downloading MicroG APKs...")
if not MICROG_APKS:
    print("ERROR: Could not determine MicroG download URLs.")
    sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    apk_files = []
    for label, url in MICROG_APKS.items():
        dest = os.path.join(tmp, url.split("/")[-1].split("?")[0])
        if download(url, dest):
            apk_files.append((dest, label))

    if not apk_files:
        print("ERROR: Could not download any APKs. Check internet connection.")
        sys.exit(1)

    print("\n[4] Installing MicroG...")
    for apk, label in apk_files:
        install(apk, label)

print("\n[5] Granting permissions to MicroG...")
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

print("\n[6] Rebooting to apply MicroG...")
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
