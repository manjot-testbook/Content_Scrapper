#!/usr/bin/env python3
"""
install_microg.py — Install MicroG on KukuTV_Root emulator.

Strategy: mount tmpfs over GMS system app dirs to hide them (root-only, no remount),
clear package DB, install MicroG.

Run: python scripts/install_microg.py
"""
import os, subprocess, sys, urllib.request, tempfile, time, json

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")

def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def shell(cmd):
    out, err, code = adb("shell", cmd)
    return (out + err).strip(), code

def wait_boot():
    print("  Waiting for boot", end="", flush=True)
    for _ in range(40):
        out, _, _ = adb("shell", "getprop sys.boot_completed")
        if out.strip() == "1": print(" ✓"); return
        time.sleep(5); print(".", end="", flush=True)
    print(" (continuing)")

def get_microg_urls():
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/microg/GmsCore/releases/latest",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        urls = {}
        for a in data.get("assets", []):
            n, u = a["name"], a["browser_download_url"]
            if "com.google.android.gms" in n and n.endswith(".apk"): urls["GmsCore"] = u
            elif "com.android.vending" in n and n.endswith(".apk"):  urls["FakeStore"] = u
        if urls:
            print(f"  MicroG release: {data.get('tag_name')}")
            return urls
    except Exception as e:
        print(f"  GitHub API error: {e}")
    tag = "v0.3.15.250932"
    base = f"https://github.com/microg/GmsCore/releases/download/{tag}"
    return {
        "GmsCore":   f"{base}/com.google.android.gms-250932030.apk",
        "FakeStore": f"{base}/com.android.vending-84022630.apk",
    }

def download(url, dest):
    print(f"  Downloading {url.split('/')[-1]} ...", end="", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
        print(f" {os.path.getsize(dest)//1024}KB ✓"); return True
    except Exception as e:
        print(f" FAILED: {e}"); return False

# ── Check device ──────────────────────────────────────────────────────────────
print("\n=== MicroG Installer ===\n")
out, _, _ = adb("devices")
if "emulator" not in out:
    print("ERROR: No emulator connected."); sys.exit(1)

# ── Step 0: Root ──────────────────────────────────────────────────────────────
print("[0] Enabling root...")
out, err, _ = adb("root")
print(f"  {out or err}")
time.sleep(4)
if "cannot run as root" in (out + err):
    print("ERROR: Need KukuTV_Root (google_apis) emulator, not Play Store emulator.")
    sys.exit(1)

# ── Step 1: Find GMS system app directories ───────────────────────────────────
print("\n[1] Finding GMS system app directories...")
GMS_PACKAGES = {
    "com.google.android.gms": None,
    "com.android.vending":    None,
}
for pkg in list(GMS_PACKAGES.keys()):
    out, _, code = adb("shell", f"pm path {pkg}")
    for line in out.splitlines():
        if "package:" in line:
            apk = line.split("package:")[-1].strip()
            pkg_dir = os.path.dirname(apk)
            GMS_PACKAGES[pkg] = pkg_dir
            print(f"  {pkg} → {pkg_dir}")
            break
    if not GMS_PACKAGES[pkg]:
        print(f"  {pkg} → not found (already removed?)")

# ── Step 2: Hide GMS dirs using tmpfs mount (no remount needed) ───────────────
print("\n[2] Hiding GMS dirs with tmpfs mounts (root trick, no /system remount)...")
for pkg, pkg_dir in GMS_PACKAGES.items():
    if not pkg_dir:
        continue
    print(f"  Mounting tmpfs over {pkg_dir} ...")
    out, code = shell(f"mount -t tmpfs tmpfs {pkg_dir}")
    if code == 0 or out == "":
        print(f"  ✓ Hidden: {pkg_dir}")
    else:
        print(f"  ! Warning: {out}")

# ── Step 3: Clear package DB entries ──────────────────────────────────────────
print("\n[3] Clearing GMS from package manager database...")
for pkg in GMS_PACKAGES:
    out, code = shell(f"pm uninstall --user 0 {pkg} 2>/dev/null; echo done")
    print(f"  {pkg}: cleared")

# Force package manager to re-scan (stop → it restarts automatically)
shell("am force-stop com.google.android.gms 2>/dev/null")
shell("am force-stop com.android.vending 2>/dev/null")
time.sleep(3)

# ── Step 4: Download MicroG ───────────────────────────────────────────────────
print("\n[4] Downloading MicroG APKs...")
urls = get_microg_urls()
apk_files = []
with tempfile.TemporaryDirectory() as tmp:
    for label, url in urls.items():
        dest = os.path.join(tmp, url.split("/")[-1])
        if download(url, dest):
            apk_files.append((dest, label))

    if not apk_files:
        print("ERROR: No APKs downloaded."); sys.exit(1)

    # ── Step 5: Install MicroG ────────────────────────────────────────────────
    print("\n[5] Installing MicroG...")
    all_ok = True
    for apk, label in apk_files:
        print(f"  Installing {label} ...")
        out, err, code = adb("install", "-r", "-d", apk)
        if code == 0 or "Success" in out:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}: {(err or out)[:300]}")
            all_ok = False

    if not all_ok:
        # Try pushing directly to /system/priv-app as a last resort
        print("\n  Trying direct push to /system/priv-app via tmpfs overlay...")
        out2, code2 = shell("mount -t tmpfs tmpfs /system/priv-app 2>/dev/null && echo ok")
        if "ok" in out2 or code2 == 0:
            for apk, label in apk_files:
                pkg = "com.google.android.gms" if "gms" in apk else "com.android.vending"
                dir_name = pkg
                shell(f"mkdir -p /system/priv-app/{dir_name}")
                adb("push", apk, f"/system/priv-app/{dir_name}/{os.path.basename(apk)}")
                shell(f"chmod 644 /system/priv-app/{dir_name}/*.apk")
                print(f"  Pushed {label} to /system/priv-app/{dir_name}/")
            shell("pm scan /system/priv-app")

# ── Step 6: Grant permissions ─────────────────────────────────────────────────
print("\n[6] Granting MicroG permissions...")
for p in ["android.permission.READ_PHONE_STATE", "android.permission.RECEIVE_SMS",
          "android.permission.READ_SMS", "android.permission.ACCESS_COARSE_LOCATION",
          "android.permission.ACCESS_FINE_LOCATION", "android.permission.GET_ACCOUNTS"]:
    adb("shell", "pm", "grant", "com.google.android.gms", p)
print("  ✓ Done")

# ── Step 7: Enable signature spoofing ─────────────────────────────────────────
print("\n[7] Enabling signature spoofing...")
shell("settings put global development_settings_enabled 1")
shell("device_config set runtime_native core.allow_gms_signature_faking true 2>/dev/null")

# ── Step 8: Reboot ────────────────────────────────────────────────────────────
print("\n[8] Rebooting...")
adb("reboot"); time.sleep(15)
wait_boot()

print("""
============================================================
  Setup complete. Now:
  1. Open MicroG Settings app on emulator → enable all toggles
     (especially "Google device registration" and "Cloud Messaging")
  2. Open KukuTV — Play Services prompt should be gone
  3. Log in with phone + OTP  (proxy is OFF)
  4. After login run:
       python scripts/fix_cert.py
       python scripts/login_mode.py on
  5. Browse KukuTV → ./run.sh analyze
============================================================
""")
