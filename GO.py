#!/usr/bin/env python3
"""
GO.py - One script that does EVERYTHING:
1. Starts KukuCapture emulator (google_apis/arm64-v8a — rootable)
2. Waits for boot
3. Uses APKs from apks/ folder (run scripts/setup_apk_downloader_avd.py first)
   OR pulls KukuTV APKs live from the running emulator as a fallback
4. Patches base.apk to trust mitmproxy (network security config)
5. Resigns all APKs with debug key
6. Installs patched KukuTV
7. Starts mitmproxy
8. Turns proxy OFF so you can log in with OTP

Usage:
    python3 GO.py            # normal run (reuse existing KukuCapture AVD)
    python3 GO.py --scratch  # kill emulator, delete + recreate KukuCapture, then run

NOTE: For fresh APKs first run:  python3 scripts/setup_apk_downloader_avd.py
"""
import os, sys, subprocess, shutil, time, zipfile, argparse

# ── Args ──────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--scratch", action="store_true",
                    help="Kill emulator, delete & recreate KukuCapture AVD from scratch")
ARGS = parser.parse_args()

# ── Config ────────────────────────────────────────────────────
SDK        = os.path.expanduser("~/Library/Android/sdk")
ADB        = os.path.join(SDK, "platform-tools", "adb")
EMULATOR   = os.path.join(SDK, "emulator", "emulator")
BUILD_TOOLS= os.path.join(SDK, "build-tools")
AVDMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
SDKMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "sdkmanager")

PACKAGE    = "com.vlv.aravali.reels"
HERE       = os.path.dirname(os.path.abspath(__file__))

# KukuCapture AVD — google_apis (rootable, no Play Store needed)
AVD_NAME     = "KukuCapture"
AVD_IMAGE    = "system-images;android-33;google_apis;arm64-v8a"
AVD_DEVICE   = "pixel_6"

# All working dirs inside the codebase (no /tmp/)
APK_DIR      = os.path.join(HERE, "apks")           # pre-pulled APKs from setup script
BUILD_DIR    = os.path.join(HERE, "build")           # working dir for patching/signing
NSC_XML_DIR  = os.path.join(BUILD_DIR, "nsc_xml", "xml")
NSC_FLAT_DIR = os.path.join(BUILD_DIR, "nsc_flat")
PATCHED_APK  = os.path.join(BUILD_DIR, "kuku_patched.apk")
ALIGNED_APK  = os.path.join(BUILD_DIR, "kuku_aligned.apk")
SIGNED_APK   = os.path.join(BUILD_DIR, "kuku_base_signed.apk")
SPLITS_DIR   = os.path.join(BUILD_DIR, "splits_signed")
KEYSTORE     = os.path.expanduser("~/.android/debug.keystore")
LOGS_DIR     = os.path.join(HERE, "logs")


def run(*cmd, timeout=120):
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args, timeout=60):
    return run(ADB, *args, timeout=timeout)

def find_tool(name):
    for d in sorted(os.listdir(BUILD_TOOLS), reverse=True):
        p = os.path.join(BUILD_TOOLS, d, name)
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

def wait_for_boot(label="emulator"):
    print(f"  Waiting for {label} to boot", end="", flush=True)
    for _ in range(90):     # up to 7.5 min
        time.sleep(5)
        try:
            o, _, _ = adb("shell", "getprop", "sys.boot_completed", timeout=8)
            if o.strip() == "1":
                print(" ✓")
                return True
        except Exception:
            pass
        print(".", end="", flush=True)
    print()
    return False

def kill_running_emulators():
    """Kill every running Android emulator and wait for adb to lose them."""
    print("  Killing running emulators...")
    devs, _, _ = adb("devices")
    serials = [l.split()[0] for l in devs.splitlines() if "emulator" in l and "offline" not in l]
    for s in serials:
        run(ADB, "-s", s, "emu", "kill")
        print(f"    Sent kill to {s}")
    # Also hard-kill via pkill as a safety net
    subprocess.run(["pkill", "-f", "qemu-system"], capture_output=True)
    subprocess.run(["pkill", "-f", "emulator"], capture_output=True)
    time.sleep(4)
    print("  ✓ Emulators stopped")

def avd_exists(name):
    o, _, _ = run(AVDMANAGER, "list", "avd")
    return name in o

def create_kuku_avd():
    """Install image if needed and create a fresh KukuCapture AVD."""
    print(f"  Installing system image: {AVD_IMAGE} ...")
    subprocess.run([SDKMANAGER, "--install", AVD_IMAGE], check=True, timeout=600)
    result = subprocess.run(
        [AVDMANAGER, "create", "avd",
         "--name", AVD_NAME,
         "--package", AVD_IMAGE,
         "--device", AVD_DEVICE,
         "--force"],
        input="no\n", text=True, capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  ERROR creating AVD: {result.stderr}")
        sys.exit(1)
    print(f"  ✓ AVD '{AVD_NAME}' created")


# ══════════════════════════════════════════════════════════════
print("\n==================================================")
print("  KukuTV Setup + Capture")
if ARGS.scratch:
    print("  MODE: --scratch (fresh AVD)")
print("==================================================\n")

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(BUILD_DIR, exist_ok=True)

# ── Step 0 (--scratch only): kill emulator + recreate AVD ─────
if ARGS.scratch:
    print("[0] Scratch mode — resetting KukuCapture AVD...")
    kill_running_emulators()

    if avd_exists(AVD_NAME):
        print(f"  Deleting existing '{AVD_NAME}' AVD...")
        run(AVDMANAGER, "delete", "avd", "--name", AVD_NAME)
        print(f"  ✓ Deleted")

    create_kuku_avd()

# ── Step 1: Emulator ──────────────────────────────────────────
print("[1] Checking emulator...")
o, _, _ = adb("devices", timeout=5)
if "emulator" in o and "device" in o:
    print("  ✓ Already running")
else:
    if not avd_exists(AVD_NAME):
        print(f"  AVD '{AVD_NAME}' not found — creating it...")
        create_kuku_avd()

    print(f"  Starting '{AVD_NAME}'...")
    subprocess.Popen(
        [EMULATOR, "-avd", AVD_NAME, "-no-snapshot-save", "-no-audio",
         "-gpu", "swiftshader_indirect"],
        stdout=open(os.path.join(LOGS_DIR, "emulator.log"), "w"),
        stderr=subprocess.STDOUT,
    )
    if not wait_for_boot(AVD_NAME):
        print("  ERROR: Emulator did not boot. Check logs/emulator.log")
        sys.exit(1)
    time.sleep(3)

# ── Step 2: APKs ─────────────────────────────────────────────
print("\n[2] APKs...")
_apks_in_dir = lambda d: [f for f in os.listdir(d) if f.endswith(".apk")] if os.path.isdir(d) else []

if _apks_in_dir(APK_DIR) and "base.apk" in _apks_in_dir(APK_DIR):
    print(f"  ✓ Using apks/ folder ({len(_apks_in_dir(APK_DIR))} APKs)")
else:
    print("  apks/ folder is empty or missing.")
    print("  TIP: Run  python3 scripts/setup_apk_downloader_avd.py  for a clean pull.")
    print("  Falling back to live pull from emulator...")
    live_dir = os.path.join(BUILD_DIR, "live_apks")
    if os.path.isdir(live_dir):
        shutil.rmtree(live_dir)
    os.makedirs(live_dir)
    o, _, _ = adb("shell", "pm", "path", PACKAGE, timeout=15)
    paths = [l.split("package:")[-1].strip() for l in o.splitlines() if "package:" in l]
    if not paths:
        print("  ERROR: KukuTV not on device and apks/ folder is empty.")
        print("  Run:  python3 scripts/setup_apk_downloader_avd.py")
        sys.exit(1)
    for p in paths:
        adb("pull", p, os.path.join(live_dir, os.path.basename(p)))
        print(f"  Pulled {os.path.basename(p)}")
    APK_DIR = live_dir

# ── Step 3: Compile NSC to binary XML ────────────────────────
print("\n[3] Compiling network_security_config with aapt2...")
aapt2 = find_tool("aapt2")
if not aapt2: print("  ERROR: aapt2 not found"); sys.exit(1)

nsc_xml = os.path.join(NSC_XML_DIR, "network_security_config.xml")
os.makedirs(NSC_XML_DIR, exist_ok=True)
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

os.makedirs(NSC_FLAT_DIR, exist_ok=True)
o, e, c = run(aapt2, "compile", nsc_xml, "-o", NSC_FLAT_DIR)
flat_file = os.path.join(NSC_FLAT_DIR, "xml_network_security_config.xml.flat")
if c != 0 or not os.path.isfile(flat_file):
    print(f"  ERROR: {e}"); sys.exit(1)

flat = open(flat_file, "rb").read()
nsc_bin = None
for i in range(min(128, len(flat) - 4)):
    if flat[i:i+2] == b'\x03\x00' and flat[i+2:i+4] in (b'\x08\x00', b'\x1c\x00'):
        nsc_bin = flat[i:]
        break
if nsc_bin is None:
    nsc_bin = flat[8:]
print(f"  ✓ NSC binary: {len(nsc_bin)} bytes")

# ── Step 4: Inject NSC into APK ──────────────────────────────
print("\n[4] Injecting NSC into APK...")
base_apk = os.path.join(APK_DIR, "base.apk")
nsc_path = "res/xml/network_security_config.xml"

with zipfile.ZipFile(base_apk, "r") as zin, \
     zipfile.ZipFile(PATCHED_APK, "w", zipfile.ZIP_DEFLATED) as zout:
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

# ── Step 5: Keystore + sign base ─────────────────────────────
if not os.path.isfile(KEYSTORE):
    run("keytool", "-genkeypair", "-keystore", KEYSTORE, "-alias", "androiddebugkey",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-storepass", "android", "-keypass", "android",
        "-dname", "CN=Android Debug,O=Android,C=US")

print("\n[5] Signing base APK...")
zt = find_tool("zipalign")
if zt: run(zt, "-f", "4", PATCHED_APK, ALIGNED_APK)
else:  shutil.copy(PATCHED_APK, ALIGNED_APK)
sign(ALIGNED_APK, SIGNED_APK)
print(f"  ✓ {os.path.getsize(SIGNED_APK)//1024} KB")

# ── Step 6: Resign splits ─────────────────────────────────────
print("\n[6] Resigning splits...")
shutil.rmtree(SPLITS_DIR, ignore_errors=True)
os.makedirs(SPLITS_DIR)
splits = []
for f in sorted(os.listdir(APK_DIR)):
    if not (f.startswith("split_") and f.endswith(".apk")): continue
    dst = os.path.join(SPLITS_DIR, f)
    sign(os.path.join(APK_DIR, f), dst)
    splits.append(dst)
    print(f"  ✓ {f}")

# ── Step 7: Install ───────────────────────────────────────────
print("\n[7] Installing...")
adb("uninstall", PACKAGE, timeout=30)
time.sleep(2)
r = subprocess.run([ADB, "install-multiple", "-d", SIGNED_APK] + splits,
                   capture_output=True, text=True, timeout=180)
out = r.stdout + r.stderr
if r.returncode == 0 or "Success" in out:
    print("  ✓ KukuTV installed!")
else:
    print(f"  ✗ {out[:400]}"); sys.exit(1)

# ── Step 8: mitmproxy ─────────────────────────────────────────
print("\n[8] Starting mitmproxy...")
subprocess.run(["pkill", "-f", "mitmdump"], capture_output=True); time.sleep(1)
traffic = os.path.join(HERE, "metadata", "captured_apis", "api_traffic.jsonl")
os.makedirs(os.path.dirname(traffic), exist_ok=True)
open(traffic, "w").close()
subprocess.Popen(
    ["mitmdump", "-s", os.path.join(HERE, "mitm_addons", "mitm_addon.py"),
     "--listen-port", "8080", "--ssl-insecure"],
    stdout=open(os.path.join(LOGS_DIR, "mitm.log"), "w"), stderr=subprocess.STDOUT)
time.sleep(3)

# Proxy OFF for OTP login
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
