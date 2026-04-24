#!/usr/bin/env python3
"""
install_microg.py — Install MicroG (Play Services replacement) on KukuTV_Root emulator.

Strategy: patch /data/system/packages.xml to remove Google's GMS signature entries,
then install MicroG. This works WITHOUT needing adb remount or /system write access.

Run: python scripts/install_microg.py
"""
import os, subprocess, sys, urllib.request, tempfile, time, json, re

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")

def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def wait_boot():
    print("  Waiting for boot", end="", flush=True)
    for _ in range(36):
        out, _, _ = adb("shell", "getprop sys.boot_completed")
        if out.strip() == "1": print(" ✓"); return
        time.sleep(5); print(".", end="", flush=True)
    print(" (continuing anyway)")

def get_microg_urls():
    print("  Fetching latest MicroG release from GitHub...")
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/microg/GmsCore/releases/latest",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        urls = {}
        for asset in data.get("assets", []):
            name, url = asset["name"], asset["browser_download_url"]
            if "com.google.android.gms" in name and name.endswith(".apk"):
                urls["GmsCore"] = url
            elif "com.android.vending" in name and name.endswith(".apk"):
                urls["FakeStore"] = url
        if urls:
            print(f"  Release: {data.get('tag_name')} — {len(urls)} APK(s)")
            return urls
    except Exception as e:
        print(f"  GitHub API failed: {e}")
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
        print(f" {os.path.getsize(dest)//1024}KB ✓")
        return True
    except Exception as e:
        print(f" FAILED: {e}"); return False

def remove_pkg_from_xml(xml_text, package):
    """Remove a <package> entry from packages.xml by package name."""
    # Match full <package ... name="com.google.android.gms" .../> or multiline block
    pattern = rf'<package[^>]+name="{re.escape(package)}"[^>]*/>'
    result, count = re.subn(pattern, '', xml_text)
    if count == 0:
        # Try multiline block: <package ...> ... </package>
        pattern2 = rf'<package[^>]+name="{re.escape(package)}".*?</package>'
        result, count = re.subn(pattern2, '', xml_text, flags=re.DOTALL)
    return result, count

# ── Check device ──────────────────────────────────────────────────────────────
print("\n=== MicroG Installer (packages.xml method) ===\n")
out, _, _ = adb("devices")
if "emulator" not in out:
    print("ERROR: No emulator connected."); sys.exit(1)

# ── Step 0: Root ──────────────────────────────────────────────────────────────
print("[0] Enabling root...")
out, err, _ = adb("root")
print(f"  {out or err}")
time.sleep(4)
if "cannot run as root" in (out + err):
    print("ERROR: This emulator doesn't support root. Need KukuTV_Root (google_apis) AVD.")
    sys.exit(1)

# ── Step 1: Patch packages.xml ────────────────────────────────────────────────
print("\n[1] Patching /data/system/packages.xml to remove GMS signature entries...")
with tempfile.TemporaryDirectory() as tmp:
    xml_local = os.path.join(tmp, "packages.xml")
    xml_bak   = os.path.join(tmp, "packages.xml.bak")

    # Pull packages.xml
    out, err, code = adb("pull", "/data/system/packages.xml", xml_local)
    if code != 0 or not os.path.isfile(xml_local):
        print(f"  ERROR pulling packages.xml: {err}"); sys.exit(1)
    print(f"  Pulled packages.xml ({os.path.getsize(xml_local)//1024}KB)")

    with open(xml_local, "r", encoding="utf-8", errors="replace") as f:
        xml = f.read()

    # Backup
    import shutil; shutil.copy(xml_local, xml_bak)

    total_removed = 0
    for pkg in ["com.google.android.gms", "com.android.vending"]:
        xml, count = remove_pkg_from_xml(xml, pkg)
        print(f"  Removed {count} entry/entries for {pkg}")
        total_removed += count

    if total_removed == 0:
        print("  No entries found — packages may already be clean.")
    else:
        with open(xml_local, "w", encoding="utf-8") as f:
            f.write(xml)
        # Push back
        adb("push", xml_local, "/data/system/packages.xml")
        adb("shell", "chmod", "660", "/data/system/packages.xml")
        adb("shell", "chown", "system:system", "/data/system/packages.xml")
        print("  ✓ packages.xml updated")

# ── Step 2: Download MicroG ───────────────────────────────────────────────────
print("\n[2] Downloading MicroG APKs...")
urls = get_microg_urls()
apk_files = []
with tempfile.TemporaryDirectory() as tmp:
    for label, url in urls.items():
        dest = os.path.join(tmp, url.split("/")[-1])
        if download(url, dest):
            apk_files.append((dest, label))

    if not apk_files:
        print("ERROR: No APKs downloaded."); sys.exit(1)

    # ── Step 3: Reboot so package manager reloads without GMS entries ─────────
    print("\n[3] Rebooting (package manager will reload without GMS entries)...")
    adb("reboot")
    time.sleep(15)
    wait_boot()
    adb("root"); time.sleep(3)

    # ── Step 4: Install MicroG ────────────────────────────────────────────────
    print("\n[4] Installing MicroG APKs...")
    for apk, label in apk_files:
        print(f"  Installing {label} ...")
        out, err, code = adb("install", "-r", "-d", apk)
        if code == 0 or "Success" in out:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}: {(err or out)[:200]}")

# ── Step 5: Permissions ───────────────────────────────────────────────────────
print("\n[5] Granting MicroG permissions...")
for p in ["android.permission.READ_PHONE_STATE", "android.permission.RECEIVE_SMS",
          "android.permission.READ_SMS", "android.permission.ACCESS_COARSE_LOCATION"]:
    adb("shell", "pm", "grant", "com.google.android.gms", p)
print("  ✓ Done")

# ── Step 6: Enable signature spoofing (required for MicroG to work) ───────────
print("\n[6] Enabling signature spoofing via device_config...")
adb("shell", "device_config", "set", "runtime_native", "core.allow_gms_signature_faking", "true")
adb("shell", "settings", "put", "global", "development_settings_enabled", "1")

# ── Step 7: Final reboot ──────────────────────────────────────────────────────
print("\n[7] Final reboot...")
adb("reboot")
time.sleep(15)
wait_boot()

print("""
============================================================
  ✓ MicroG setup complete
============================================================

Next steps:
  1. Open KukuTV on the emulator
  2. If still asks for Play Services → open MicroG app → enable all toggles
  3. Log in with phone number + OTP (proxy is OFF so it will work)
  4. After login:
       python scripts/fix_cert.py
       python scripts/login_mode.py on
  5. Browse KukuTV then:
       ./run.sh analyze
""")
