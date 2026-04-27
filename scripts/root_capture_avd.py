#!/usr/bin/env python3
"""
root_capture_avd.py — One-time AVD setup. Run once, then use GO.py forever.

Creates the KukuCapture AVD (google_apis — adb root works natively).
No rootAVD, no Magisk, no dialogs. Just creates the AVD and verifies root.

Usage:
    python3 scripts/root_capture_avd.py
"""
import os, sys, subprocess, time

SDK        = os.path.expanduser("~/Library/Android/sdk")
ADB        = os.path.join(SDK, "platform-tools", "adb")
EMULATOR   = os.path.join(SDK, "emulator", "emulator")
AVDMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
SDKMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "sdkmanager")

AVD_NAME   = "KukuCapture"
AVD_IMAGE  = "system-images;android-33;google_apis;arm64-v8a"
AVD_DEVICE = "pixel_6"

HERE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR   = os.path.join(HERE, "logs")
MITM_CERT  = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")


def run(*cmd, timeout=120, inp=None):
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, input=inp)
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


print("\n==================================================")
print("  KukuCapture AVD — one-time setup")
print("==================================================\n")
os.makedirs(LOGS_DIR, exist_ok=True)

# ── Kill any running emulator ─────────────────────────────────
print("[0] Stopping any running emulators...")
devs, _, _ = adb("devices")
for s in [l.split()[0] for l in devs.splitlines() if "emulator" in l]:
    run(ADB, "-s", s, "emu", "kill")
subprocess.run(["pkill", "-f", "emulator"], capture_output=True)
time.sleep(5)
print("  ✓")

# ── Create AVD ────────────────────────────────────────────────
print(f"\n[1] Creating {AVD_NAME} AVD  ({AVD_IMAGE})...")
o, _, _ = run(AVDMANAGER, "list", "avd")
if AVD_NAME in o:
    run(AVDMANAGER, "delete", "avd", "--name", AVD_NAME)
    print(f"  Deleted existing '{AVD_NAME}'")

subprocess.run([SDKMANAGER, "--install", AVD_IMAGE], check=True, timeout=600)
r = subprocess.run(
    [AVDMANAGER, "create", "avd",
     "--name", AVD_NAME, "--package", AVD_IMAGE,
     "--device", AVD_DEVICE, "--force"],
    input="no\n", text=True, capture_output=True, timeout=60,
)
if r.returncode != 0:
    print(f"  ERROR: {r.stderr}"); sys.exit(1)
print(f"  ✓ '{AVD_NAME}' created")

# ── Boot with -writable-system ────────────────────────────────
print(f"\n[2] Booting {AVD_NAME} with -writable-system...")
log = open(os.path.join(LOGS_DIR, "rootavd_setup.log"), "w")
subprocess.Popen(
    [EMULATOR, "-avd", AVD_NAME,
     "-writable-system",
     "-no-snapshot-load", "-no-snapshot-save",
     "-no-audio", "-gpu", "swiftshader_indirect"],
    stdout=log, stderr=subprocess.STDOUT,
)
if not wait_for_boot():
    print("  ERROR: emulator did not boot"); sys.exit(1)
time.sleep(5)

# ── Root + remount ────────────────────────────────────────────
print("\n[3] Gaining root...")
adb("root", timeout=15); time.sleep(3)
adb("remount", timeout=30)
o, _, rc = adb("shell", "id", timeout=10)
if "uid=0" not in o:
    print(f"  ERROR: adb root failed: {o}"); sys.exit(1)
print(f"  ✓ {o.strip()}")

# ── Install mitmproxy cert ────────────────────────────────────
print("\n[4] Installing mitmproxy system cert...")
if not os.path.isfile(MITM_CERT):
    print("  Generating cert...")
    p = subprocess.Popen(["mitmdump", "--listen-port", "8080"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3); p.terminate(); time.sleep(1)

o, e, rc = run("openssl", "x509", "-inform", "PEM",
               "-subject_hash_old", "-in", MITM_CERT, timeout=10)
cert_hash = o.splitlines()[0].strip()
remote = f"/system/etc/security/cacerts/{cert_hash}.0"
_, err, rc = adb("push", MITM_CERT, remote, timeout=15)
if rc != 0:
    print(f"  ERROR: {err}"); sys.exit(1)
adb("shell", "chmod", "644", remote)
print(f"  ✓ Cert hash {cert_hash} installed")

print("""
==================================================
  ✓ Setup complete
==================================================
  Now run:  python3 GO.py
==================================================
""")
