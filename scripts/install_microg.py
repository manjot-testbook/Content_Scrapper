#!/usr/bin/env python3
"""
install_microg.py — Install MicroG on KukuTV_Root emulator.

Key insight: after mounting tmpfs over GMS dirs, restart the Android framework
(stop/start) so package manager rescans and loses the GMS signature from memory.
Then install MicroG BEFORE rebooting (rebooting clears tmpfs mounts).

Run: python scripts/install_microg.py
"""
import os, subprocess, sys, urllib.request, tempfile, time, json

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")

def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def sh(cmd):
    out, err, _ = adb("shell", cmd)
    return (out + err).strip()

def wait_boot():
    print("  Waiting for boot", end="", flush=True)
    for _ in range(48):
        out, _, _ = adb("shell", "getprop sys.boot_completed")
        if out.strip() == "1": print(" ✓"); return
        time.sleep(5); print(".", end="", flush=True)
    print(" (timed out — continuing)")

def download(url, dest):
    name = url.split("/")[-1]
    print(f"  Downloading {name} ...", end="", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
        print(f" {os.path.getsize(dest)//1024}KB ✓"); return True
    except Exception as e:
        print(f" FAILED: {e}"); return False

def get_urls():
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/microg/GmsCore/releases/latest",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        urls = {}
        for a in data.get("assets", []):
            n, u = a["name"], a["browser_download_url"]
            if "com.google.android.gms" in n and n.endswith(".apk"): urls["GmsCore"] = u
            elif "com.android.vending" in n and n.endswith(".apk"):  urls["FakeStore"] = u
        if urls:
            print(f"  Latest MicroG: {data.get('tag_name')}")
            return urls
    except Exception as e:
        print(f"  GitHub API failed ({e}), using fallback...")
    tag = "v0.3.15.250932"
    base = f"https://github.com/microg/GmsCore/releases/download/{tag}"
    return {
        "GmsCore":   f"{base}/com.google.android.gms-250932030.apk",
        "FakeStore": f"{base}/com.android.vending-84022630.apk",
    }

print("\n=== MicroG Installer (clean rewrite) ===\n")

# 0. Check device + root
print("[0] Checking device and enabling root...")
out, _, _ = adb("devices")
if "emulator" not in out:
    print("ERROR: No emulator connected."); sys.exit(1)
out, err, _ = adb("root")
print(f"  {out or err}")
if "cannot run as root" in (out + err):
    print("ERROR: Need KukuTV_Root (google_apis) emulator."); sys.exit(1)
time.sleep(4)

# 1. Find GMS APK dirs
print("\n[1] Finding GMS dirs...")
gms_dirs = {}
for pkg in ["com.google.android.gms", "com.android.vending"]:
    out, _, _ = adb("shell", f"pm path {pkg}")
    for line in out.splitlines():
        if "package:" in line:
            apk_path = line.split("package:")[-1].strip()
            d = os.path.dirname(apk_path)
            gms_dirs[pkg] = d
            print(f"  {pkg} → {d}")
            break
    else:
        print(f"  {pkg} → not found")

if not gms_dirs:
    print("  GMS not found at all — proceeding to install MicroG directly.")

# 2. Mount tmpfs over GMS dirs to hide them
print("\n[2] Hiding GMS with tmpfs mounts...")
for pkg, d in gms_dirs.items():
    result = sh(f"mount -t tmpfs tmpfs '{d}' && echo OK")
    if "OK" in result or result == "":
        print(f"  ✓ Masked: {d}")
    else:
        print(f"  ! {d}: {result}")

# 3. Restart Android framework so package manager rescans (loses GMS signature)
print("\n[3] Restarting Android framework (package manager will rescan without GMS)...")
print("  Stopping framework...", end="", flush=True)
sh("stop")
time.sleep(6)
print(" done")
print("  Starting framework...", end="", flush=True)
sh("start")
# Wait for package manager to be ready
for _ in range(30):
    time.sleep(3)
    out = sh("pm list packages 2>/dev/null | head -1")
    if "package:" in out:
        print(" ready ✓")
        break
    print(".", end="", flush=True)
else:
    print(" (continuing anyway)")
time.sleep(5)

# 4. Download + install MicroG
print("\n[4] Downloading MicroG APKs...")
urls = get_urls()
with tempfile.TemporaryDirectory() as tmp:
    apks = []
    for label, url in urls.items():
        dest = os.path.join(tmp, url.split("/")[-1])
        if download(url, dest):
            apks.append((dest, label))

    if not apks:
        print("ERROR: No APKs downloaded."); sys.exit(1)

    print("\n[5] Installing MicroG...")
    for apk, label in apks:
        print(f"  Installing {label} ...")
        out, err, code = adb("install", "-r", "-d", apk)
        combined = (out + err)
        if code == 0 or "Success" in combined:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}: {combined[:200]}")

# 5. Grant permissions
print("\n[6] Granting permissions to MicroG...")
for p in [
    "android.permission.READ_PHONE_STATE",
    "android.permission.RECEIVE_SMS",
    "android.permission.READ_SMS",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.GET_ACCOUNTS",
]:
    adb("shell", "pm", "grant", "com.google.android.gms", p)
print("  ✓ Permissions granted")

# 6. Verify MicroG installed
print("\n[7] Verifying installation...")
out = sh("pm list packages | grep google.android.gms")
if "com.google.android.gms" in out:
    print(f"  ✓ MicroG installed: {out}")
else:
    print(f"  ✗ Not found — install likely failed due to lingering signature.")
    print("    Try running: python scripts/install_microg.py  again after reboot.")

# 7. Reboot to make MicroG permanent
print("\n[8] Rebooting to make installation permanent...")
adb("reboot")
time.sleep(15)
wait_boot()

# After reboot, GMS dirs are visible again. MicroG is now in /data/app.
# Check if MicroG survived
out = sh("pm list packages | grep google.android.gms")
print(f"\n  Post-reboot GMS package: {out}")
if "com.google.android.gms" in out:
    print("  ✓ MicroG survived reboot!")
else:
    print("  ✗ MicroG gone after reboot (system GMS took precedence).")
    print("  The KukuTV_Root image has GMS baked in and protected.")
    print("  Switching to Frida-based SSL bypass instead (no MicroG needed).")
    print("  Run: python scripts/bypass_ssl_pinning.py")

print("""
============================================================
  Done. Next steps:
  1. Open KukuTV on the emulator
     - If it asks for Play Services: open 'MicroG Settings' → enable all
  2. Log in with phone + OTP
     (proxy is OFF — OTP will work)
  3. After login:
       python scripts/fix_cert.py
       python scripts/login_mode.py on
  4. Browse KukuTV, then:
       ./run.sh analyze
============================================================
""")
