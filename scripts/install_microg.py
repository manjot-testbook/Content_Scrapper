#!/usr/bin/env python3
"""
install_microg.py — Remove GMS signature from packages.xml, then install MicroG.
Uses proper XML parsing (not regex) to cleanly remove GMS package entries.

Run: python scripts/install_microg.py
"""
import os, subprocess, sys, urllib.request, tempfile, time, json
import xml.etree.ElementTree as ET

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
TARGETS = ["com.google.android.gms", "com.android.vending"]

def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def sh(cmd):
    out, err, _ = adb("shell", cmd)
    return (out + err).strip()

def wait_ready():
    print("  Waiting for package manager", end="", flush=True)
    for _ in range(40):
        out = sh("pm list packages 2>/dev/null | head -1")
        if "package:" in out: print(" ✓"); return
        time.sleep(4); print(".", end="", flush=True)
    print(" (continuing)")

def wait_boot():
    print("  Waiting for boot", end="", flush=True)
    for _ in range(48):
        out, _, _ = adb("shell", "getprop sys.boot_completed")
        if out.strip() == "1": print(" ✓"); return
        time.sleep(5); print(".", end="", flush=True)
    print(" (timed out)")

def download(url, dest):
    print(f"  Downloading {url.split('/')[-1]} ...", end="", flush=True)
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
            print(f"  MicroG release: {data.get('tag_name')}"); return urls
    except Exception as e:
        print(f"  GitHub API failed: {e}")
    tag = "v0.3.15.250932"
    base = f"https://github.com/microg/GmsCore/releases/download/{tag}"
    return {
        "GmsCore":   f"{base}/com.google.android.gms-250932030.apk",
        "FakeStore": f"{base}/com.android.vending-84022630.apk",
    }

print("\n=== MicroG Installer ===\n")

# 0. Root
print("[0] Enabling root...")
out, err, _ = adb("root")
print(f"  {out or err}")
if "cannot run as root" in (out + err):
    print("ERROR: Need KukuTV_Root emulator (google_apis, not google_apis_playstore)")
    sys.exit(1)
time.sleep(4)

# 1. Edit packages.xml with proper XML parser
print("\n[1] Editing /data/system/packages.xml to remove GMS signature entries...")
with tempfile.TemporaryDirectory() as tmp:
    xml_path = os.path.join(tmp, "packages.xml")
    out, err, code = adb("pull", "/data/system/packages.xml", xml_path)
    if code != 0:
        print(f"  ERROR: {err}"); sys.exit(1)
    print(f"  Pulled ({os.path.getsize(xml_path)//1024}KB)")

    # Parse and remove GMS entries
    ET.register_namespace("", "")
    tree = ET.parse(xml_path)
    root = tree.getroot()
    removed = 0
    for tag in ["package", "updated-package", "disabled-components", "preferred-activities"]:
        for elem in root.findall(f".//{tag}[@name]"):
            if elem.get("name") in TARGETS:
                root.remove(elem)
                removed += 1
                print(f"  Removed <{tag} name=\"{elem.get('name')}\">")
        # Also check direct children
        for elem in list(root):
            if elem.get("name") in TARGETS:
                root.remove(elem)
                removed += 1
                print(f"  Removed direct <{elem.tag} name=\"{elem.get('name')}\">")

    print(f"  Total entries removed: {removed}")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)

    # Also remove from packages.list (plain text, one line per package)
    list_path = os.path.join(tmp, "packages.list")
    adb("pull", "/data/system/packages.list", list_path)
    if os.path.isfile(list_path):
        lines = open(list_path).readlines()
        filtered = [l for l in lines if not any(t in l for t in TARGETS)]
        open(list_path, "w").writelines(filtered)
        adb("push", list_path, "/data/system/packages.list")
        print(f"  packages.list: removed {len(lines)-len(filtered)} entries")

    # Push packages.xml back
    adb("push", xml_path, "/data/system/packages.xml")
    sh("chmod 660 /data/system/packages.xml")
    sh("chown system:system /data/system/packages.xml")
    print("  ✓ packages.xml updated")

# 2. Also hide the APK dirs with tmpfs (belt + suspenders)
print("\n[2] Hiding GMS APK dirs with tmpfs...")
for pkg in TARGETS:
    out, _, _ = adb("shell", f"pm path {pkg}")
    for line in out.splitlines():
        if "package:" in line:
            d = os.path.dirname(line.split("package:")[-1].strip())
            result = sh(f"mount -t tmpfs tmpfs '{d}' && echo OK")
            print(f"  {'✓' if 'OK' in result else '!'} tmpfs on {d}")

# 3. Restart framework so it reads the new packages.xml
print("\n[3] Restarting Android framework...")
print("  Stopping...", end="", flush=True)
sh("stop"); time.sleep(8); print(" done")
print("  Starting...", end="", flush=True)
sh("start"); wait_ready()
time.sleep(5)

# Verify GMS is gone from package manager
for pkg in TARGETS:
    out = sh(f"pm list packages | grep '{pkg}$'")
    if out:
        print(f"  ! {pkg} still present: {out}")
    else:
        print(f"  ✓ {pkg} no longer registered")

# 4. Download + install MicroG
print("\n[4] Downloading MicroG...")
urls = get_urls()
with tempfile.TemporaryDirectory() as tmp:
    apks = []
    for label, url in urls.items():
        dest = os.path.join(tmp, url.split("/")[-1])
        if download(url, dest):
            apks.append((dest, label))

    print("\n[5] Installing MicroG...")
    success = 0
    for apk, label in apks:
        print(f"  {label}...", end="", flush=True)
        out, err, code = adb("install", "-r", "-d", apk)
        combined = out + err
        if code == 0 or "Success" in combined:
            print(" ✓"); success += 1
        else:
            print(f" ✗\n    {combined[:200]}")

# 5. Permissions
print("\n[6] Granting permissions...")
for p in ["android.permission.READ_PHONE_STATE","android.permission.RECEIVE_SMS",
          "android.permission.READ_SMS","android.permission.ACCESS_COARSE_LOCATION",
          "android.permission.ACCESS_FINE_LOCATION","android.permission.GET_ACCOUNTS"]:
    adb("shell", "pm", "grant", "com.google.android.gms", p)
print("  ✓ Done")

# 6. Reboot
print("\n[7] Rebooting...")
adb("reboot"); time.sleep(15); wait_boot()

# 7. Check result
print("\n[8] Post-reboot check...")
gms = sh("pm list packages | grep 'com.google.android.gms$'")
vend = sh("pm list packages | grep 'com.android.vending$'")
print(f"  GmsCore : {gms or 'not found'}")
print(f"  Vending : {vend or 'not found'}")

if "com.google.android.gms" in gms:
    print("\n  ✓ MicroG survived reboot!")
    print("""
============================================================
  DONE — Next steps:
  1. Open MicroG Settings on emulator → enable all toggles
  2. Open KukuTV — Play Services prompt should be gone
  3. Log in with phone + OTP  (proxy is OFF, it will work)
  4. After login:
       python scripts/fix_cert.py
       python scripts/login_mode.py on
  5. Browse KukuTV → ./run.sh analyze
============================================================
""")
else:
    print("""
  ✗ MicroG did not survive — the system image keeps restoring GMS.
  
  FINAL OPTION: Use Frida to bypass SSL pinning directly (no cert, no MicroG).
  KukuTV is installed on Medium_Phone (logged in). Run there:
  
    ./run.sh bypass
  
  This hooks SSL at runtime using Frida — works regardless of Play Services.
""")
