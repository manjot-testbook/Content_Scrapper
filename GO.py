#!/usr/bin/env python3
"""
GO.py - One script that does EVERYTHING:
1. Starts emulator (Medium_Phone - has Play Services)
2. Waits for boot
3. Pulls KukuTV APKs from device OR uses cached ones
4. Patches base.apk to trust mitmproxy (network security config)
5. Resigns all APKs with debug key
6. Installs patched KukuTV
7. Starts mitmproxy
8. Turns proxy OFF so you can log in with OTP

Run this in a terminal: python3 GO.py
"""
import os, sys, subprocess, shutil, time, zipfile

# ── Config ────────────────────────────────────────────────────
ADB      = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
EMULATOR = os.path.expanduser("~/Library/Android/sdk/emulator/emulator")
BUILD    = os.path.expanduser("~/Library/Android/sdk/build-tools")
PACKAGE  = "com.vlv.aravali.reels"
APK_DIR  = "/tmp/kukutv_apks"
HERE     = os.path.dirname(os.path.abspath(__file__))
KEYSTORE = os.path.expanduser("~/.android/debug.keystore")
SIGNED   = "/tmp/kuku_base_signed.apk"
SPLITS_D = "/tmp/kuku_splits_signed"

def run(*cmd, timeout=120):
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args, timeout=60):
    return run(ADB, *args, timeout=timeout)

def find_tool(name):
    for d in sorted(os.listdir(BUILD), reverse=True):
        p = os.path.join(BUILD, d, name)
        if os.path.isfile(p): return p

def sign(src, dst):
    t = find_tool("apksigner")
    if t:
        _, _, c = run(t, "sign", "--ks", KEYSTORE, "--ks-pass", "pass:android",
            "--ks-key-alias", "androiddebugkey", "--key-pass", "pass:android",
            "--in", src, "--out", dst)
        if c == 0 and os.path.isfile(dst): return
    shutil.copy(src, dst)
    run("jarsigner", "-keystore", KEYSTORE, "-storepass", "android",
        "-keypass", "android", "-signedjar", dst, src, "androiddebugkey")

print("\n==================================================")
print("  KukuTV Setup + Capture")
print("==================================================\n")

# 1. Emulator check
print("[1] Checking emulator...")
o, _, _ = adb("devices", timeout=5)
if "emulator" in o and "device" in o:
    print("  ✓ Running")
else:
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    subprocess.Popen([EMULATOR, "-avd", "Medium_Phone_API_36.1", "-no-snapshot-save", "-no-audio"],
        stdout=open(os.path.join(HERE, "logs", "emulator.log"), "w"), stderr=subprocess.STDOUT)
    print("  Waiting for boot", end="", flush=True)
    for _ in range(40):
        time.sleep(5)
        try:
            o2, _, _ = adb("shell", "getprop sys.boot_completed", timeout=8)
            if o2.strip() == "1": print(" ✓"); break
        except: pass
        print(".", end="", flush=True)

# 2. APKs
print("\n[2] APKs...")
os.makedirs(APK_DIR, exist_ok=True)
if "base.apk" not in os.listdir(APK_DIR):
    o, _, _ = adb("shell", f"pm path {PACKAGE}", timeout=15)
    paths = [l.split("package:")[-1].strip() for l in o.splitlines() if "package:" in l]
    if not paths: print("  ERROR: KukuTV not on device. Install from Play Store."); sys.exit(1)
    for p in paths:
        adb("pull", p, os.path.join(APK_DIR, os.path.basename(p)))
        print(f"  Pulled {os.path.basename(p)}")
else:
    print(f"  Using cached ({len([f for f in os.listdir(APK_DIR) if f.endswith('.apk')])} APKs)")

# 3. Compile NSC to binary XML using aapt2
print("\n[3] Compiling network_security_config with aapt2...")
aapt2 = find_tool("aapt2")
if not aapt2: print("  ERROR: aapt2 not found"); sys.exit(1)

nsc_xml = "/tmp/nsc_xml/xml/network_security_config.xml"
os.makedirs(os.path.dirname(nsc_xml), exist_ok=True)
open(nsc_xml, "w").write(
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<network-security-config>\n'
    '  <base-config cleartextTrafficPermitted="true">\n'
    '    <trust-anchors>\n'
    '      <certificates src="system"/>\n'
    '      <certificates src="user"/>\n'
    '    </trust-anchors>\n'
    '  </base-config>\n'
    '</network-security-config>\n'
)

flat_out = "/tmp/nsc_flat"
os.makedirs(flat_out, exist_ok=True)
o, e, c = run(aapt2, "compile", nsc_xml, "-o", flat_out)
flat_file = os.path.join(flat_out, "xml_network_security_config.xml.flat")
if c != 0 or not os.path.isfile(flat_file):
    print(f"  ERROR: {e}"); sys.exit(1)

# Extract binary XML from flat file (aapt2 flat = header + binary XML)
flat = open(flat_file, "rb").read()
# Find binary XML magic (chunk type RES_XML_TYPE = 0x0003)
nsc_bin = None
for i in range(min(128, len(flat)-4)):
    if flat[i:i+2] == b'\x03\x00' and flat[i+2:i+4] in (b'\x08\x00', b'\x1c\x00'):
        nsc_bin = flat[i:]
        break
if nsc_bin is None:
    nsc_bin = flat[8:]  # fallback
print(f"  ✓ NSC binary: {len(nsc_bin)} bytes")

# 4. Inject NSC directly into APK zip (NO recompile — avoids all manifest issues)
print("\n[4] Injecting NSC into APK...")
base_apk  = os.path.join(APK_DIR, "base.apk")
patched   = "/tmp/kuku_patched.apk"
nsc_path  = "res/xml/network_security_config.xml"

with zipfile.ZipFile(base_apk, "r") as zin, \
     zipfile.ZipFile(patched, "w", zipfile.ZIP_DEFLATED) as zout:
    found = False
    for item in zin.infolist():
        if item.filename == nsc_path:
            zout.writestr(item.filename, nsc_bin)
            found = True
            print(f"  ✓ Replaced {nsc_path}")
        else:
            zout.writestr(item, zin.read(item.filename))
    if not found:
        zout.writestr(nsc_path, nsc_bin)
        print(f"  ✓ Added {nsc_path}")

# 5. Keystore
if not os.path.isfile(KEYSTORE):
    run("keytool", "-genkeypair", "-keystore", KEYSTORE, "-alias", "androiddebugkey",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-storepass", "android", "-keypass", "android",
        "-dname", "CN=Android Debug,O=Android,C=US")

# 6. Zipalign + sign base
print("\n[5] Signing base APK...")
aligned = "/tmp/kuku_aligned.apk"
zt = find_tool("zipalign")
if zt: run(zt, "-f", "4", patched, aligned)
else: shutil.copy(patched, aligned)
sign(aligned, SIGNED)
print(f"  ✓ {os.path.getsize(SIGNED)//1024}KB")

# 7. Resign splits
print("\n[6] Resigning splits...")
shutil.rmtree(SPLITS_D, ignore_errors=True)
os.makedirs(SPLITS_D)
splits = []
for f in sorted(os.listdir(APK_DIR)):
    if not (f.startswith("split_") and f.endswith(".apk")): continue
    dst = os.path.join(SPLITS_D, f)
    sign(os.path.join(APK_DIR, f), dst)
    splits.append(dst)
    print(f"  ✓ {f}")

# 8. Install
print("\n[7] Installing...")
adb("uninstall", PACKAGE, timeout=30)
time.sleep(2)
r = subprocess.run([ADB, "install-multiple", "-d", SIGNED] + splits,
                   capture_output=True, text=True, timeout=180)
out = r.stdout + r.stderr
if r.returncode == 0 or "Success" in out:
    print("  ✓ KukuTV installed!")
else:
    print(f"  ✗ {out[:400]}"); sys.exit(1)

# 9. mitmproxy
print("\n[8] Starting mitmproxy...")
subprocess.run(["pkill", "-f", "mitmdump"], capture_output=True); time.sleep(1)
traffic = os.path.join(HERE, "metadata", "captured_apis", "api_traffic.jsonl")
os.makedirs(os.path.dirname(traffic), exist_ok=True)
open(traffic, "w").close()
os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
subprocess.Popen(
    ["mitmdump", "-s", os.path.join(HERE, "mitm_addons", "mitm_addon.py"),
     "--listen-port", "8080", "--ssl-insecure"],
    stdout=open(os.path.join(HERE, "logs", "mitm.log"), "w"), stderr=subprocess.STDOUT)
time.sleep(3)

# Proxy OFF for login
adb("shell", "settings", "put", "global", "http_proxy", ":0")
adb("shell", "settings", "delete", "global", "http_proxy")

print("""
==================================================
  ✓ DONE
==================================================
 1. Open KukuTV → log in with OTP  (proxy is OFF)
 2. After login run:
    ~/Library/Android/sdk/platform-tools/adb shell settings put global http_proxy 10.0.2.2:8080
 3. Browse KukuTV: home → show → play video
 4. python3 scripts/analyze.py
==================================================
""")
