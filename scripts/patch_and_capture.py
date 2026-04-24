#!/usr/bin/env python3
"""
patch_apk.py - Patch KukuTV base.apk to trust user/mitmproxy certificates.

How it works:
  1. Decompile base.apk with apktool
  2. Add/replace network_security_config.xml to trust ALL certs (user + system)
  3. Update AndroidManifest.xml to reference our config
  4. Recompile + sign with debug key
  5. Install patched APK + all split APKs on connected emulator

No root needed. Works on any emulator including google_apis_playstore.
"""
import os, subprocess, sys, shutil, time

ADB       = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
BUILD     = os.path.expanduser("~/Library/Android/sdk/build-tools")
APKTOOL   = "/opt/homebrew/bin/apktool"
BASE_APK  = "/tmp/kukutv_apks/base.apk"
WORK_DIR  = "/tmp/kuku_patch"
OUT_APK   = "/tmp/kuku_patched.apk"
SIGNED_APK= "/tmp/kuku_signed.apk"

def run(*cmd):
    r = subprocess.run(list(cmd), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args):
    return run(ADB, *args)

def find_build_tool(name):
    for d in sorted(os.listdir(BUILD), reverse=True):
        p = os.path.join(BUILD, d, name)
        if os.path.isfile(p):
            return p
    return None

print("\n=== KukuTV APK Patcher ===\n")

# ── 1. Clean work dir ─────────────────────────────────────────
print("[1] Preparing work directory...")
if os.path.exists(WORK_DIR): shutil.rmtree(WORK_DIR)
os.makedirs(WORK_DIR)

# ── 2. Decompile base.apk ─────────────────────────────────────
print("[2] Decompiling base.apk with apktool...")
o, e, code = run(APKTOOL, "d", BASE_APK, "-o", WORK_DIR, "-f", "--no-src")
if code != 0:
    # Try with --only-main-classes fallback
    o, e, code = run(APKTOOL, "d", BASE_APK, "-o", WORK_DIR, "-f")
if code != 0:
    print(f"  ERROR: {e[:300]}")
    sys.exit(1)
print(f"  ✓ Decompiled to {WORK_DIR}")

# ── 3. Write network security config ──────────────────────────
print("[3] Writing network_security_config.xml (trust all certs)...")
res_xml = os.path.join(WORK_DIR, "res", "xml")
os.makedirs(res_xml, exist_ok=True)

nsc_path = os.path.join(res_xml, "network_security_config.xml")
open(nsc_path, "w").write("""<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>
    <debug-overrides>
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </debug-overrides>
</network-security-config>
""")
print("  ✓ Written")

# ── 4. Patch AndroidManifest.xml ──────────────────────────────
print("[4] Patching AndroidManifest.xml...")
manifest = os.path.join(WORK_DIR, "AndroidManifest.xml")
content  = open(manifest).read()

# Add networkSecurityConfig if not present
if "networkSecurityConfig" not in content:
    content = content.replace(
        'android:label=',
        'android:networkSecurityConfig="@xml/network_security_config"\n        android:label='
    )
    # If that didn't work try another pattern
    if "networkSecurityConfig" not in content:
        content = content.replace(
            '<application ',
            '<application android:networkSecurityConfig="@xml/network_security_config" '
        )
else:
    # Replace existing reference
    import re
    content = re.sub(
        r'android:networkSecurityConfig="[^"]*"',
        'android:networkSecurityConfig="@xml/network_security_config"',
        content
    )

# Also make app debuggable to allow Frida/proxy
if 'android:debuggable' not in content:
    content = content.replace(
        '<application ',
        '<application android:debuggable="true" '
    )

open(manifest, "w").write(content)
print("  ✓ Manifest patched")

# ── 5. Recompile ──────────────────────────────────────────────
print("[5] Recompiling with apktool...")
o, e, code = run(APKTOOL, "b", WORK_DIR, "-o", OUT_APK)
if code != 0:
    print(f"  ERROR: {e[:300]}")
    sys.exit(1)
print(f"  ✓ Compiled: {OUT_APK}")

# ── 6. Sign with debug keystore ───────────────────────────────
print("[6] Signing APK...")
keystore = os.path.expanduser("~/.android/debug.keystore")
if not os.path.isfile(keystore):
    run("keytool", "-genkeypair", "-v", "-keystore", keystore,
        "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048",
        "-validity", "10000", "-storepass", "android", "-keypass", "android",
        "-dname", "CN=Android Debug,O=Android,C=US")

apksigner = find_build_tool("apksigner")
if apksigner:
    o, e, code = run(apksigner, "sign",
        "--ks", keystore, "--ks-pass", "pass:android",
        "--ks-key-alias", "androiddebugkey", "--key-pass", "pass:android",
        "--out", SIGNED_APK, OUT_APK)
    if code != 0:
        print(f"  apksigner error: {e[:200]} — trying jarsigner...")
        apksigner = None

if not apksigner:
    shutil.copy(OUT_APK, SIGNED_APK)
    o, e, code = run("jarsigner", "-verbose", "-keystore", keystore,
        "-storepass", "android", "-keypass", "android",
        "-signedjar", SIGNED_APK, OUT_APK, "androiddebugkey")

if os.path.isfile(SIGNED_APK) and os.path.getsize(SIGNED_APK) > 1000:
    print(f"  ✓ Signed: {SIGNED_APK} ({os.path.getsize(SIGNED_APK)//1024}KB)")
else:
    print("  ✗ Signing failed — using unsigned APK")
    shutil.copy(OUT_APK, SIGNED_APK)

# ── 7. Check device ───────────────────────────────────────────
print("\n[7] Checking for connected device...")
o, _, _ = adb("devices")
print(f"  {o}")
if "emulator" not in o and len(o.splitlines()) < 2:
    print("  No device connected — install manually with:")
    print(f"  adb install-multiple -r -d {SIGNED_APK} /tmp/kukutv_apks/split_config.arm64_v8a.apk ...")
    sys.exit(0)

# ── 8. Uninstall old + install patched ────────────────────────
print("[8] Installing patched KukuTV...")
PACKAGE = "com.vlv.aravali.reels"

# Uninstall existing
adb("uninstall", PACKAGE)
time.sleep(2)

# Install patched base + all splits
splits = sorted([
    os.path.join("/tmp/kukutv_apks", f)
    for f in os.listdir("/tmp/kukutv_apks")
    if f.startswith("split_") and f.endswith(".apk")
])
cmd = [ADB, "install-multiple", "-r", "-d", SIGNED_APK] + splits
r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
if r.returncode == 0 or "Success" in (r.stdout + r.stderr):
    print("  ✓ KukuTV patched version installed!")
else:
    print(f"  ✗ {(r.stderr or r.stdout)[:300]}")

# ── 9. Start mitmproxy ────────────────────────────────────────
print("\n[9] Starting mitmproxy...")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
subprocess.run(["pkill", "-f", "mitmdump"], capture_output=True)
time.sleep(1)
os.makedirs(f"{PROJECT}/metadata/captured_apis", exist_ok=True)
open(f"{PROJECT}/metadata/captured_apis/api_traffic.jsonl", "w").close()
subprocess.Popen(
    ["mitmdump", "-s", f"{PROJECT}/mitm_addons/mitm_addon.py",
     "--listen-port", "8080", "--ssl-insecure"],
    stdout=open(f"{PROJECT}/logs/mitm.log", "w"), stderr=subprocess.STDOUT
)
time.sleep(3)

# Set proxy
adb("shell", "settings", "put", "global", "http_proxy", "10.0.2.2:8080")
print("  ✓ Proxy → 10.0.2.2:8080")

print(f"""
{'='*55}
  ✓ DONE — KukuTV patched APK installed!
{'='*55}

The patched app trusts ALL certificates including mitmproxy.
No root needed. No system cert needed.

1. Open KukuTV on the emulator
2. Log in with phone + OTP (should work normally)
3. Browse: home → show → play a video
4. Run: python3 scripts/analyze.py

Note: If login fails, disable proxy first:
  adb shell settings put global http_proxy :0
  (log in, then re-enable proxy)
{'='*55}
""")
