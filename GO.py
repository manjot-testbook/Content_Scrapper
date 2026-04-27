#!/usr/bin/env python3
"""
GO.py — KukuTV capture pipeline

DEFAULT (python3 GO.py):
  • Starts the KukuCapture emulator (reuses existing state — login session preserved)
  • Starts mitmproxy (appends to api_traffic.jsonl — no wipe)
  • Enables proxy immediately (already logged in)
  • Ready to capture — no OTP needed

SCRATCH (python3 GO.py --scratch):
  • Kills emulator, deletes + recreates KukuCapture AVD
  • Runs full setup: root, cert, APK install, mitmproxy
  • Wipes logs and api_traffic.jsonl
  • Asks for OTP login, then enables proxy
  • Use this when: first-time setup, AVD is broken, or you want a clean slate
"""
import os, sys, subprocess, shutil, time, argparse

# ── Args ──────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--scratch", action="store_true",
                    help="Full reset: kill emulator, recreate AVD, reinstall app, wipe logs")
ARGS = parser.parse_args()

# ── Config ────────────────────────────────────────────────────
SDK        = os.path.expanduser("~/Library/Android/sdk")
ADB        = os.path.join(SDK, "platform-tools", "adb")
EMULATOR   = os.path.join(SDK, "emulator", "emulator")
AVDMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
SDKMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "sdkmanager")

PACKAGE    = "com.vlv.aravali.reels"
HERE       = os.path.dirname(os.path.abspath(__file__))

AVD_NAME   = "KukuCapture"
AVD_IMAGE  = "system-images;android-33;google_apis;arm64-v8a"
AVD_DEVICE = "pixel_6"

APK_DIR        = os.path.join(HERE, "apks")
BUILD_DIR      = os.path.join(HERE, "build")
LOGS_DIR       = os.path.join(HERE, "logs")
TRAFFIC_FILE   = os.path.join(HERE, "metadata", "captured_apis", "api_traffic.jsonl")
MITM_CERT_PEM  = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
SYSTEM_CACERTS = "/system/etc/security/cacerts"


# ── Helpers ───────────────────────────────────────────────────
def run(*cmd, timeout=120):
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args, timeout=60):
    return run(ADB, *args, timeout=timeout)

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
    devs, _, _ = adb("devices")
    serials = [l.split()[0] for l in devs.splitlines()
               if "emulator" in l and "offline" not in l]
    for s in serials:
        run(ADB, "-s", s, "emu", "kill")
    subprocess.run(["pkill", "-f", "qemu-system"], capture_output=True)
    subprocess.run(["pkill", "-f", "emulator"], capture_output=True)
    time.sleep(4)

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
        print(f"  ERROR: {result.stderr}"); sys.exit(1)
    print(f"  ✓ AVD '{AVD_NAME}' created")

def get_cert_hash(pem_path):
    o, e, rc = run("openssl", "x509", "-inform", "PEM",
                   "-subject_hash_old", "-in", pem_path, timeout=10)
    if rc != 0:
        print(f"  ERROR computing cert hash: {e}"); sys.exit(1)
    return o.splitlines()[0].strip()

def ensure_mitm_cert():
    if os.path.isfile(MITM_CERT_PEM):
        return
    print("  Generating mitmproxy CA cert (first run)...")
    p = subprocess.Popen(["mitmdump", "--listen-port", "8080"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3); p.terminate(); time.sleep(1)
    if not os.path.isfile(MITM_CERT_PEM):
        print(f"  ERROR: cert not generated at {MITM_CERT_PEM}"); sys.exit(1)
    print(f"  ✓ Cert generated: {MITM_CERT_PEM}")

def emulator_running():
    o, _, _ = adb("devices", timeout=5)
    return "emulator" in o and "device" in o

def start_emulator():
    if not avd_exists(AVD_NAME):
        print(f"  AVD '{AVD_NAME}' not found.")
        print("  Run:  python3 scripts/root_capture_avd.py")
        sys.exit(1)
    print(f"  Starting '{AVD_NAME}'...")
    subprocess.Popen(
        [EMULATOR, "-avd", AVD_NAME,
         "-writable-system",
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

def start_mitmproxy(wipe_traffic=False):
    subprocess.run(["pkill", "-f", "mitmdump"], capture_output=True)
    time.sleep(1)
    os.makedirs(os.path.dirname(TRAFFIC_FILE), exist_ok=True)
    if wipe_traffic:
        open(TRAFFIC_FILE, "w").close()
        print("  ✓ api_traffic.jsonl cleared")
    else:
        lines = sum(1 for _ in open(TRAFFIC_FILE)) if os.path.isfile(TRAFFIC_FILE) else 0
        print(f"  ✓ Appending to api_traffic.jsonl  (existing: {lines} lines)")
    subprocess.Popen(
        ["mitmdump", "-s", os.path.join(HERE, "mitm_addons", "mitm_addon.py"),
         "--listen-port", "8080", "--ssl-insecure"],
        stdout=open(os.path.join(LOGS_DIR, "mitm.log"), "a"),   # append, not overwrite
        stderr=subprocess.STDOUT,
    )
    time.sleep(3)
    print("  ✓ mitmproxy listening on :8080")


# ══════════════════════════════════════════════════════════════
mode = "--scratch (full reset)" if ARGS.scratch else "normal (reuse session)"
print(f"\n==================================================")
print(f"  KukuTV Capture  [{mode}]")
print(f"==================================================\n")

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(BUILD_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# SCRATCH MODE — full reset, reinstall, OTP login required
# ─────────────────────────────────────────────────────────────
if ARGS.scratch:
    print("[0] Killing emulator + resetting AVD...")
    kill_running_emulators()
    if avd_exists(AVD_NAME):
        run(AVDMANAGER, "delete", "avd", "--name", AVD_NAME)
        print(f"  ✓ Deleted '{AVD_NAME}'")
    create_kuku_avd()
    print("""
  AVD recreated. Run the one-time root setup next:

      python3 scripts/root_capture_avd.py

  Then run:  python3 GO.py
""")
    sys.exit(0)


# ─────────────────────────────────────────────────────────────
# NORMAL MODE — reuse existing AVD + login session
# ─────────────────────────────────────────────────────────────

# Step 1: Emulator
print("[1] Emulator...")
if emulator_running():
    print("  ✓ Already running")
else:
    start_emulator()

# Step 2: Root + remount (needed each boot to write /system)
print("\n[2] Root + remount...")
adb("root", timeout=15); time.sleep(3)
adb("remount", timeout=20)
o, _, _ = adb("shell", "id", timeout=10)
print(f"  ✓ {o.strip()}")

# Step 3: Cert (idempotent — skips if already installed)
print("\n[3] System cert...")
ensure_mitm_cert()
cert_hash   = get_cert_hash(MITM_CERT_PEM)
remote_cert = f"{SYSTEM_CACERTS}/{cert_hash}.0"
o, _, _     = adb("shell", "ls", remote_cert, timeout=10)
if cert_hash in o or ".0" in o:
    print(f"  ✓ Already installed ({cert_hash})")
else:
    _, err, rc = adb("push", MITM_CERT_PEM, remote_cert, timeout=15)
    if rc != 0:
        print(f"  ERROR: {err}"); sys.exit(1)
    adb("shell", "chmod", "644", remote_cert, timeout=10)
    print(f"  ✓ Cert installed")

# Step 4: APK install — only in scratch mode (skipped here)
# In normal mode KukuTV is already installed with login session intact
print("\n[4] KukuTV...")
o, _, _ = adb("shell", "pm", "path", PACKAGE, timeout=10)
if "package:" in o:
    print(f"  ✓ Already installed (login session preserved)")
else:
    # Not installed — install from apks/ folder
    print("  Not installed — installing from apks/...")
    _apks = [f for f in os.listdir(APK_DIR) if f.endswith(".apk")] if os.path.isdir(APK_DIR) else []
    if not _apks or "base.apk" not in _apks:
        print("  ERROR: apks/ folder empty. Run: python3 scripts/setup_apk_downloader_avd.py")
        sys.exit(1)
    base_apk = os.path.join(APK_DIR, "base.apk")
    splits = [os.path.join(APK_DIR, f) for f in sorted(_apks)
              if f.startswith("split_")]
    r = subprocess.run([ADB, "install-multiple", "-r", "-d", base_apk] + splits,
                       capture_output=True, text=True, timeout=180)
    out = r.stdout + r.stderr
    if r.returncode == 0 or "Success" in out:
        print("  ✓ KukuTV installed")
        # New install = no login session, need OTP
        adb("shell", "settings", "delete", "global", "http_proxy")
        print("\n  ⚠  Fresh install — OTP login required.")
        input("  ▶  Log in to KukuTV, then press ENTER: ")
    else:
        print(f"  ✗ Install failed:\n{out[:400]}"); sys.exit(1)

# Step 5: mitmproxy (append mode — don't wipe existing captures)
print("\n[5] mitmproxy...")
start_mitmproxy(wipe_traffic=False)

# Step 6: Enable proxy + restart KukuTV
print("\n[6] Enabling proxy...")
adb("shell", "settings", "put", "global", "http_proxy", "10.0.2.2:8080")
adb("shell", "am", "force-stop", PACKAGE, timeout=10)
time.sleep(2)
adb("shell", "am", "start", "-n",
    f"{PACKAGE}/com.vlv.aravali.splash.ui.SplashActivity", timeout=10)

print("""
==================================================
  ✓ Capturing — no login needed
==================================================

  KukuTV is open with proxy ON.
  Browse to capture API calls:
    → Home feed → tap a show → play an episode

  Watch live:
    tail -f metadata/captured_apis/api_traffic.jsonl

  Analyse:
    python3 scripts/analyze.py

==================================================
""")
