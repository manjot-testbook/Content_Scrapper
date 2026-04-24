#!/usr/bin/env python3
"""
setup.py - Complete fresh setup for KukuTV API capture.

What this does (in order):
  1. Creates a new rootable AVD (google_apis, API 33)
  2. Starts it with -writable-system so /system is writable
  3. adb root + adb remount
  4. Pushes MicroG directly into /system/priv-app/ (replaces GMS, no signature conflict)
  5. Installs mitmproxy CA cert as system cert
  6. Reboots
  7. Installs KukuTV APKs
  8. Starts mitmproxy + sets proxy

Run: python scripts/setup.py
"""
import os, subprocess, sys, time, shutil, lzma, json
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────
ADB       = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
EMULATOR  = os.path.expanduser("~/Library/Android/sdk/emulator/emulator")
AVDMGR    = os.path.expanduser("~/Library/Android/sdk/cmdline-tools/latest/bin/avdmanager")
PROJECT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AVD_NAME  = "KukuCapture"
PACKAGE   = "com.vlv.aravali.reels"
APK_CACHE = "/tmp/kukutv_apks"
CERT_PEM  = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
MITM_PORT = 8080

# ── Helpers ───────────────────────────────────────────────────────────────────
def run(*cmd, check=False):
    r = subprocess.run(list(cmd), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args):
    return run(ADB, *args)

def sh(cmd):
    out, err, _ = adb("shell", cmd)
    return (out + err).strip()

def wait_boot(label="device"):
    print(f"  Waiting for boot", end="", flush=True)
    for _ in range(60):
        out, _, _ = adb("shell", "getprop sys.boot_completed")
        if out.strip() == "1":
            print(" ✓")
            return True
        time.sleep(5); print(".", end="", flush=True)
    print(" timed out")
    return False

def download(url, dest):
    print(f"  Downloading {url.split('/')[-1]}...", end="", flush=True)
    urllib.request.urlretrieve(url, dest)
    print(f" {os.path.getsize(dest)//1024}KB ✓")

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("  KukuTV Capture Setup — Fresh Start")
print("="*60 + "\n")

# ── Step 1: Create AVD ────────────────────────────────────────────────────────
print("[1] Creating AVD...")
# Delete if exists
run(AVDMGR, "delete", "avd", "-n", AVD_NAME)

# Find available google_apis image
sdk_imgs = os.path.expanduser("~/Library/Android/sdk/system-images")
ga_image = None
for api in ["android-33", "android-34", "android-32", "android-31", "android-30"]:
    path = os.path.join(sdk_imgs, api, "google_apis", "arm64-v8a")
    if os.path.isdir(path):
        ga_image = f"system-images;{api};google_apis;arm64-v8a"
        print(f"  Found: {ga_image}")
        break

if not ga_image:
    print("  ERROR: No google_apis system image found.")
    print("  Install one in Android Studio: SDK Manager → System Images → Google APIs (not Google Play)")
    print("  Suggested: Android 13 (API 33) → ABI: arm64-v8a → Google APIs")
    sys.exit(1)

out, err, code = run(AVDMGR, "create", "avd",
    "-n", AVD_NAME,
    "-k", ga_image,
    "-d", "pixel_6",
    "--force")
if code != 0 and "already exists" not in err:
    print(f"  ERROR creating AVD: {err}")
    sys.exit(1)
print(f"  ✓ AVD '{AVD_NAME}' created")

# ── Step 2: Start emulator with -writable-system ──────────────────────────────
print("\n[2] Starting emulator with -writable-system...")
os.makedirs(os.path.join(PROJECT, "logs"), exist_ok=True)
log = open(os.path.join(PROJECT, "logs", "emulator.log"), "w")
subprocess.Popen(
    [EMULATOR, "-avd", AVD_NAME, "-writable-system", "-no-snapshot-save", "-no-audio", "-gpu", "swiftshader_indirect"],
    stdout=log, stderr=log
)
time.sleep(10)
wait_boot()

# ── Step 3: Root + remount ────────────────────────────────────────────────────
print("\n[3] Enabling root + remounting /system...")
out, err, _ = adb("root")
print(f"  root: {out or err}")
time.sleep(5)

out, err, code = adb("remount")
print(f"  remount: {out or err}")
if "remount failed" in (out + err).lower():
    print("  Trying disable-verity + reboot...")
    adb("disable-verity")
    time.sleep(2)
    adb("reboot")
    time.sleep(15)
    wait_boot()
    adb("root"); time.sleep(5)
    out, err, _ = adb("remount")
    print(f"  remount after disable-verity: {out or err}")
time.sleep(2)

# ── Step 4: Push MicroG directly into /system/priv-app/ ──────────────────────
print("\n[4] Installing MicroG into /system/priv-app/ (replacing GMS)...")

# Get MicroG URLs
try:
    req = urllib.request.Request(
        "https://api.github.com/repos/microg/GmsCore/releases/latest",
        headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    gms_url = next((a["browser_download_url"] for a in data["assets"]
                    if "com.google.android.gms" in a["name"] and a["name"].endswith(".apk")), None)
    vend_url = next((a["browser_download_url"] for a in data["assets"]
                     if "com.android.vending" in a["name"] and a["name"].endswith(".apk")), None)
    print(f"  MicroG release: {data.get('tag_name')}")
except Exception as e:
    print(f"  GitHub API failed: {e} — using fallback")
    tag = "v0.3.15.250932"
    base = f"https://github.com/microg/GmsCore/releases/download/{tag}"
    gms_url  = f"{base}/com.google.android.gms-250932030.apk"
    vend_url = f"{base}/com.android.vending-84022630.apk"

import tempfile
with tempfile.TemporaryDirectory() as tmp:
    gms_apk  = os.path.join(tmp, "GmsCore.apk")
    vend_apk = os.path.join(tmp, "FakeStore.apk")
    download(gms_url,  gms_apk)
    download(vend_url, vend_apk)

    # Find and delete existing GMS in /system and /product
    for pkg in ["com.google.android.gms", "com.android.vending"]:
        out_pm, _, _ = adb("shell", f"pm path {pkg}")
        for line in out_pm.splitlines():
            if "package:" in line:
                apk_path = line.split("package:")[-1].strip()
                pkg_dir  = os.path.dirname(apk_path)
                print(f"  Deleting existing: {pkg_dir}")
                sh(f"rm -rf '{pkg_dir}'")

    # Push MicroG directly into /system/priv-app/
    for apk, name in [(gms_apk, "GmsCore"), (vend_apk, "FakeStore")]:
        dest_dir = f"/system/priv-app/{name}"
        sh(f"mkdir -p {dest_dir}")
        adb("push", apk, f"{dest_dir}/{name}.apk")
        sh(f"chmod 644 {dest_dir}/{name}.apk")
        sh(f"chown root:root {dest_dir}/{name}.apk")
        print(f"  ✓ Pushed {name} → {dest_dir}/")

# ── Step 5: Install mitmproxy cert as system cert ─────────────────────────────
print("\n[5] Installing mitmproxy CA cert as system cert...")
if not os.path.isfile(CERT_PEM):
    print("  Generating cert (running mitmdump briefly)...")
    p = subprocess.Popen(["mitmdump", "--listen-port", "8081"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5); p.terminate()

if os.path.isfile(CERT_PEM):
    r = subprocess.run(["openssl", "x509", "-inform", "PEM", "-subject_hash_old", "-in", CERT_PEM],
                       capture_output=True, text=True)
    cert_hash = r.stdout.strip().splitlines()[0]
    cert_file = f"{cert_hash}.0"
    adb("push", CERT_PEM, f"/system/etc/security/cacerts/{cert_file}")
    sh(f"chmod 644 /system/etc/security/cacerts/{cert_file}")
    sh(f"chown root:root /system/etc/security/cacerts/{cert_file}")
    # Verify
    out, _, _ = adb("shell", f"ls /system/etc/security/cacerts/{cert_file}")
    if cert_file in out:
        print(f"  ✓ Cert installed: {cert_file}")
    else:
        print(f"  ✗ Cert install failed")
else:
    print("  ✗ mitmproxy cert not found — run 'mitmdump' once first")

# ── Step 6: Reboot ────────────────────────────────────────────────────────────
print("\n[6] Rebooting (MicroG + cert take effect)...")
adb("reboot")
time.sleep(15)
wait_boot()

# Grant MicroG permissions after reboot
print("  Granting MicroG permissions...")
adb("root"); time.sleep(3)
for p in ["android.permission.READ_PHONE_STATE", "android.permission.RECEIVE_SMS",
          "android.permission.READ_SMS", "android.permission.ACCESS_COARSE_LOCATION",
          "android.permission.GET_ACCOUNTS"]:
    adb("shell", "pm", "grant", "com.google.android.gms", p)
print("  ✓ Permissions granted")

# ── Step 7: Install KukuTV ────────────────────────────────────────────────────
print("\n[7] Installing KukuTV...")
apks = sorted([os.path.join(APK_CACHE, f)
               for f in os.listdir(APK_CACHE) if f.endswith(".apk")]) if os.path.isdir(APK_CACHE) else []

if not apks:
    print(f"  ERROR: No APKs found in {APK_CACHE}")
    print("  Run this first with Medium_Phone emulator running and KukuTV installed:")
    print("  python scripts/pull_apks.py")
else:
    print(f"  Installing {len(apks)} APKs...")
    out, err, code = run(ADB, "install-multiple", "-r", "-d", *apks)
    if code == 0 or "Success" in (out+err):
        print("  ✓ KukuTV installed")
    else:
        print(f"  ✗ Failed: {(err or out)[:200]}")
        print("  Try: adb install-multiple -r -d " + " ".join(apks))

# ── Step 8: Start proxy ───────────────────────────────────────────────────────
print("\n[8] Starting mitmproxy + setting device proxy...")
subprocess.run(["pkill", "-f", "mitmdump"], capture_output=True)
time.sleep(1)

os.makedirs(os.path.join(PROJECT, "metadata", "captured_apis"), exist_ok=True)
open(os.path.join(PROJECT, "metadata", "captured_apis", "api_traffic.jsonl"), "w").close()

subprocess.Popen(
    ["mitmdump", "-s", os.path.join(PROJECT, "mitm_addons", "mitm_addon.py"),
     "--listen-port", str(MITM_PORT), "--ssl-insecure"],
    stdout=open(os.path.join(PROJECT, "logs", "mitm.log"), "w"),
    stderr=subprocess.STDOUT
)
time.sleep(3)

# 10.0.2.2 = Android emulator's alias for the Mac host
adb("shell", "settings", "put", "global", "http_proxy", f"10.0.2.2:{MITM_PORT}")
print(f"  ✓ Proxy set to 10.0.2.2:{MITM_PORT}")

print(f"""
{'='*60}
  ✓ SETUP COMPLETE
{'='*60}

Now:
  1. Open KukuTV on the emulator
     - MicroG replaces Play Services — login should work
     - Log in with your phone number + OTP
  2. Browse the app for 2-3 minutes:
     - Home screen → tap a show → play a video → browse more
  3. Run: python scripts/analyze.py
     to see all captured API endpoints
""")
