#!/usr/bin/env python3
"""
GO.py - One script that does EVERYTHING:
1. Starts KukuCapture emulator (google_apis/arm64-v8a, -writable-system)
2. Waits for boot
3. Gains root (adb root + adb remount)
4. Installs mitmproxy CA cert into /system/etc/security/cacerts/  ← key step
5. Installs ORIGINAL KukuTV APKs (no patching, no resigning — Pairip stays happy)
6. Starts mitmproxy
7. Turns proxy OFF so you can log in with OTP, then enable after login

Usage:
    python3 GO.py            # normal run (reuse existing KukuCapture AVD)
    python3 GO.py --scratch  # kill emulator, delete + recreate KukuCapture, then run

NOTE: For fresh APKs first run:  python3 scripts/setup_apk_downloader_avd.py

WHY this approach:
  - Patching the APK (NSC inject + resign) triggers Pairip anti-tamper → app crashes
  - Installing mitmproxy cert as a USER cert is ignored by KukuTV's NSC (src="system" only)
  - Solution: root emulator → push cert to SYSTEM store → install original APK untouched
"""
import os, sys, subprocess, shutil, time, argparse

# ── Args ──────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--scratch", action="store_true",
                    help="Kill emulator, delete & recreate KukuCapture AVD from scratch")
ARGS = parser.parse_args()

# ── Config ────────────────────────────────────────────────────
SDK        = os.path.expanduser("~/Library/Android/sdk")
ADB        = os.path.join(SDK, "platform-tools", "adb")
EMULATOR   = os.path.join(SDK, "emulator", "emulator")
AVDMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
SDKMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "sdkmanager")

PACKAGE    = "com.vlv.aravali.reels"
HERE       = os.path.dirname(os.path.abspath(__file__))

# KukuCapture AVD — google_apis_playstore (full GMS → KukuTV Play Services checks pass)
# Root is provided by Magisk (installed once via scripts/root_capture_avd.py)
AVD_NAME   = "KukuCapture"
AVD_IMAGE  = "system-images;android-33;google_apis_playstore;arm64-v8a"
AVD_DEVICE = "pixel_6"

# Directories (all inside codebase, no /tmp/)
APK_DIR    = os.path.join(HERE, "apks")
BUILD_DIR  = os.path.join(HERE, "build")
LOGS_DIR   = os.path.join(HERE, "logs")

# mitmproxy cert paths
MITM_CERT_PEM = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
SYSTEM_CACERTS = "/system/etc/security/cacerts"


# ── Helpers ───────────────────────────────────────────────────
def run(*cmd, timeout=120):
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args, timeout=60):
    return run(ADB, *args, timeout=timeout)

def adb_su(*cmd, timeout=30):
    """Run a shell command via Magisk su (requires root_capture_avd.py to have been run)."""
    return run(ADB, "shell", "su", "0", "-c", " ".join(cmd), timeout=timeout)

def magisk_installed():
    """Return True if Magisk is present on the device."""
    for pkg in ("io.github.huskydg.magisk", "com.topjohnwu.magisk"):
        o, _, rc = adb("shell", "pm", "path", pkg, timeout=10)
        if rc == 0 and "package:" in o:
            return True
    return False

def wait_for_boot():
    print("  Waiting for boot", end="", flush=True)
    for _ in range(90):
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
    print("  Killing running emulators...")
    devs, _, _ = adb("devices")
    serials = [l.split()[0] for l in devs.splitlines()
               if "emulator" in l and "offline" not in l]
    for s in serials:
        run(ADB, "-s", s, "emu", "kill")
        print(f"    Sent kill to {s}")
    subprocess.run(["pkill", "-f", "qemu-system"], capture_output=True)
    subprocess.run(["pkill", "-f", "emulator"], capture_output=True)
    time.sleep(4)
    print("  ✓ Emulators stopped")

def avd_exists(name):
    o, _, _ = run(AVDMANAGER, "list", "avd")
    return name in o

def create_kuku_avd():
    print(f"  Installing system image: {AVD_IMAGE} ...")
    subprocess.run([SDKMANAGER, "--install", AVD_IMAGE], check=True, timeout=600)
    result = subprocess.run(
        [AVDMANAGER, "create", "avd",
         "--name", AVD_NAME, "--package", AVD_IMAGE,
         "--device", AVD_DEVICE, "--force"],
        input="no\n", text=True, capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        sys.exit(1)
    print(f"  ✓ AVD '{AVD_NAME}' created")

def get_cert_hash(pem_path):
    """Compute the OpenSSL subject_hash_old of a PEM cert (Android naming convention)."""
    o, e, rc = run("openssl", "x509", "-inform", "PEM",
                   "-subject_hash_old", "-in", pem_path, timeout=10)
    if rc != 0:
        print(f"  ERROR computing cert hash: {e}")
        sys.exit(1)
    return o.splitlines()[0].strip()

def ensure_mitm_cert():
    """Run mitmdump briefly to generate the mitmproxy CA cert if it doesn't exist."""
    if os.path.isfile(MITM_CERT_PEM):
        return
    print("  Generating mitmproxy CA cert (first run)...")
    p = subprocess.Popen(
        ["mitmdump", "--listen-port", "8080"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(3)
    p.terminate()
    time.sleep(1)
    if not os.path.isfile(MITM_CERT_PEM):
        print(f"  ERROR: cert not generated at {MITM_CERT_PEM}")
        sys.exit(1)
    print(f"  ✓ Cert generated: {MITM_CERT_PEM}")


# ══════════════════════════════════════════════════════════════
print("\n==================================================")
print("  KukuTV Setup + Capture")
if ARGS.scratch:
    print("  MODE: --scratch (fresh AVD)")
print("==================================================\n")

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(BUILD_DIR, exist_ok=True)

# ── Step 0 (--scratch): kill + recreate AVD ───────────────────
if ARGS.scratch:
    print("[0] Scratch — resetting KukuCapture AVD...")
    kill_running_emulators()
    if avd_exists(AVD_NAME):
        run(AVDMANAGER, "delete", "avd", "--name", AVD_NAME)
        print(f"  ✓ Deleted existing '{AVD_NAME}'")
    create_kuku_avd()
    print("""
  AVD recreated. You MUST now run the one-time root setup:

      python3 scripts/root_capture_avd.py

  Then run GO.py normally (without --scratch).
""")
    sys.exit(0)

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
        [EMULATOR, "-avd", AVD_NAME,
         "-no-snapshot-save",
         "-no-audio",
         "-gpu", "swiftshader_indirect"],
        stdout=open(os.path.join(LOGS_DIR, "emulator.log"), "w"),
        stderr=subprocess.STDOUT,
    )
    if not wait_for_boot():
        print("  ERROR: Emulator did not boot. Check logs/emulator.log")
        sys.exit(1)
    time.sleep(3)

# ── Step 2: Verify Magisk root ───────────────────────────────
print("\n[2] Verifying Magisk root...")
if not magisk_installed():
    print("""
  ERROR: Magisk is not installed on this AVD.
  Run the one-time setup first:

      python3 scripts/root_capture_avd.py

  Then re-run GO.py.
""")
    sys.exit(1)

o, _, rc = adb_su("echo ROOT_OK", timeout=15)
if "ROOT_OK" not in o:
    print("""
  ERROR: Cannot get root via Magisk su.
  Make sure ADB shell is authorised in Magisk Superuser settings,
  or re-run:  python3 scripts/root_capture_avd.py
""")
    sys.exit(1)
print("  ✓ Magisk root confirmed")

# ── Step 3: Install mitmproxy cert as SYSTEM cert ─────────────
print("\n[3] Installing mitmproxy cert into system trust store...")
ensure_mitm_cert()

cert_hash   = get_cert_hash(MITM_CERT_PEM)
staging     = f"/sdcard/{cert_hash}.0"
remote_cert = f"{SYSTEM_CACERTS}/{cert_hash}.0"
print(f"  Cert hash : {cert_hash}")

# Check if cert is already installed
o, _, _ = adb_su(f"ls {remote_cert}", timeout=10)
if cert_hash in o or remote_cert in o:
    print("  ✓ System cert already installed")
else:
    adb("push", MITM_CERT_PEM, staging, timeout=15)
    adb_su(f"cp {staging} {remote_cert}", timeout=10)
    adb_su(f"chmod 644 {remote_cert}", timeout=10)
    adb_su(f"rm {staging}", timeout=10)
    print(f"  ✓ System cert installed: {remote_cert}")

# ── Step 4: Install ORIGINAL KukuTV APKs (NO patching) ────────
print("\n[4] APKs...")
_apks_in_dir = lambda d: [f for f in os.listdir(d) if f.endswith(".apk")] if os.path.isdir(d) else []

if _apks_in_dir(APK_DIR) and "base.apk" in _apks_in_dir(APK_DIR):
    print(f"  ✓ Using apks/ folder ({len(_apks_in_dir(APK_DIR))} APKs)")
    src_dir = APK_DIR
else:
    print("  apks/ folder empty — pulling live from emulator...")
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
    src_dir = live_dir

print("\n[5] Installing original APKs (untouched — Pairip stays happy)...")
base_apk = os.path.join(src_dir, "base.apk")
splits   = [os.path.join(src_dir, f) for f in sorted(os.listdir(src_dir))
            if f.startswith("split_") and f.endswith(".apk")]

adb("uninstall", PACKAGE, timeout=30)
time.sleep(2)

r = subprocess.run(
    [ADB, "install-multiple", "-r", "-d", base_apk] + splits,
    capture_output=True, text=True, timeout=180
)
out = r.stdout + r.stderr
if r.returncode == 0 or "Success" in out:
    print("  ✓ KukuTV installed (original signature)")
else:
    print(f"  ✗ Install failed:\n{out[:600]}")
    sys.exit(1)

# ── Step 6: mitmproxy ─────────────────────────────────────────
print("\n[6] Starting mitmproxy...")
subprocess.run(["pkill", "-f", "mitmdump"], capture_output=True)
time.sleep(1)

traffic = os.path.join(HERE, "metadata", "captured_apis", "api_traffic.jsonl")
os.makedirs(os.path.dirname(traffic), exist_ok=True)
open(traffic, "w").close()

subprocess.Popen(
    ["mitmdump", "-s", os.path.join(HERE, "mitm_addons", "mitm_addon.py"),
     "--listen-port", "8080", "--ssl-insecure"],
    stdout=open(os.path.join(LOGS_DIR, "mitm.log"), "w"),
    stderr=subprocess.STDOUT,
)
time.sleep(3)
print("  ✓ mitmproxy listening on :8080")

# Proxy OFF — let you log in with OTP (Play Integrity check needs direct internet)
adb("shell", "settings", "put", "global", "http_proxy", ":0")
adb("shell", "settings", "delete", "global", "http_proxy")

print("""
==================================================
  ✓ DONE — follow the steps below
==================================================

 1. Open KukuTV on the emulator
    → Log in with your phone number + OTP
    (proxy is OFF so OTP/Play Integrity works)

 2. After login, ENABLE the proxy:
    """ + ADB + """ shell settings put global http_proxy 10.0.2.2:8080

 3. Browse KukuTV:
    → Home feed → tap a show → tap an episode → play video

 4. Analyse captured traffic:
    python3 scripts/analyze.py

==================================================
""")
