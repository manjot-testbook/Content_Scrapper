#!/usr/bin/env python3
"""
root_capture_avd.py  —  Fully automated one-time setup.
Just run it and walk away.

What it does:
  1. Deletes + recreates KukuCapture AVD  (google_apis — rootAVD patches ramdisk directly)
  2. Boots emulator
  3. Runs rootAVD (pipes "1" to auto-select stable Magisk — no interaction)
  4. Cold-reboots so patched ramdisk is loaded
  5. Auto-taps Magisk "Additional Setup" dialog via uiautomator
  6. Reboots again if Magisk requested it
  7. Verifies root (adb root on google_apis always works)
  8. Installs mitmproxy CA cert into /system/etc/security/cacerts/

After this runs, just:  python3 GO.py
"""

import os, sys, subprocess, shutil, time, urllib.request, stat
import xml.etree.ElementTree as ET
import re

# ── Config ────────────────────────────────────────────────────────────────────
SDK           = os.path.expanduser("~/Library/Android/sdk")
ADB           = os.path.join(SDK, "platform-tools", "adb")
EMULATOR_BIN  = os.path.join(SDK, "emulator", "emulator")
AVDMANAGER    = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
SDKMANAGER    = os.path.join(SDK, "cmdline-tools", "latest", "bin", "sdkmanager")

AVD_NAME      = "KukuCapture"
# google_apis: rootAVD patches ramdisk directly — no FAKEBOOTIMG, no UI interaction
AVD_IMAGE     = "system-images;android-33;google_apis;arm64-v8a"
AVD_DEVICE    = "pixel_6"
RAMDISK       = os.path.join(SDK, "system-images", "android-33",
                              "google_apis", "arm64-v8a", "ramdisk.img")

HERE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR     = os.path.join(HERE, "tools")
ROOT_AVD_DIR  = os.path.join(TOOLS_DIR, "rootAVD")
ROOT_AVD_SH   = os.path.join(ROOT_AVD_DIR, "rootAVD.sh")
LOGS_DIR      = os.path.join(HERE, "logs")

MITM_CERT_PEM  = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
SYSTEM_CACERTS = "/system/etc/security/cacerts"

ROOT_AVD_URL   = "https://gitlab.com/newbit/rootAVD/-/raw/master/rootAVD.sh"


# ── Helpers ───────────────────────────────────────────────────────────────────
def run(*cmd, timeout=120, inp=None):
    r = subprocess.run(list(cmd), capture_output=True, text=True,
                       timeout=timeout, input=inp)
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

def kill_emulator():
    devs, _, _ = adb("devices")
    for s in [l.split()[0] for l in devs.splitlines() if "emulator" in l]:
        run(ADB, "-s", s, "emu", "kill")
    subprocess.run(["pkill", "-f", "emulator"], capture_output=True)
    time.sleep(5)

def start_emulator():
    """Cold boot — always ignores snapshots."""
    log = open(os.path.join(LOGS_DIR, "rootavd_setup.log"), "a")
    subprocess.Popen(
        [EMULATOR_BIN, "-avd", AVD_NAME,
         "-no-snapshot-load", "-no-snapshot-save",
         "-no-audio", "-gpu", "swiftshader_indirect"],
        stdout=log, stderr=subprocess.STDOUT,
    )

def tap_text(text, timeout=40):
    """Find any UI node containing text and tap its centre. Returns True if tapped."""
    print(f"  Looking for '{text}' button", end="", flush=True)
    for _ in range(timeout // 4):
        time.sleep(4)
        adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", timeout=15)
        o, _, rc = adb("shell", "cat", "/sdcard/ui.xml", timeout=10)
        if rc != 0 or not o:
            print(".", end="", flush=True)
            continue
        try:
            for node in ET.fromstring(o).iter("node"):
                node_text = (node.get("text") or node.get("content-desc") or "").strip()
                if text.lower() in node_text.lower():
                    m = re.findall(r'\d+', node.get("bounds", ""))
                    if len(m) >= 4:
                        x = (int(m[0]) + int(m[2])) // 2
                        y = (int(m[1]) + int(m[3])) // 2
                        adb("shell", "input", "tap", str(x), str(y), timeout=5)
                        print(f" → tapped ({x},{y}) ✓")
                        return True
        except Exception:
            pass
        print(".", end="", flush=True)
    print(" — not found (ok)")
    return False

def get_cert_hash(pem_path):
    o, e, rc = run("openssl", "x509", "-inform", "PEM",
                   "-subject_hash_old", "-in", pem_path, timeout=10)
    if rc != 0:
        print(f"  ERROR: {e}"); sys.exit(1)
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
    print(f"  ✓ {MITM_CERT_PEM}")

def step(n, msg):
    print(f"\n[{n}] {msg}")


# ══════════════════════════════════════════════════════════════════════════════
print("\n==================================================")
print("  KukuCapture — automated root setup")
print("==================================================")
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(TOOLS_DIR, exist_ok=True)

step(0, "Stopping any running emulators")
kill_emulator()
print("  ✓")

step(1, f"Creating {AVD_NAME} AVD  ({AVD_IMAGE})")
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

step(2, "Downloading rootAVD (if needed)")
if not os.path.isfile(ROOT_AVD_SH):
    os.makedirs(ROOT_AVD_DIR, exist_ok=True)
    urllib.request.urlretrieve(ROOT_AVD_URL, ROOT_AVD_SH)
    os.chmod(ROOT_AVD_SH, os.stat(ROOT_AVD_SH).st_mode | stat.S_IEXEC)
    print(f"  ✓ Downloaded to {ROOT_AVD_SH}")
else:
    print(f"  ✓ Already present")

step(3, "First boot (pre-root)")
start_emulator()
if not wait_for_boot():
    print("  ERROR: emulator did not boot. Check logs/rootavd_setup.log"); sys.exit(1)
time.sleep(5)

step(4, "Patching ramdisk with Magisk (rootAVD)")
print(f"  Ramdisk : {RAMDISK}")
print("  Piping '1' → auto-selects stable Magisk, no menu wait")
proc = subprocess.Popen(
    ["bash", ROOT_AVD_SH, RAMDISK],
    cwd=SDK,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
try:
    out, _ = proc.communicate(input="1\n", timeout=300)
    for line in out.splitlines()[-20:]:
        print(f"    {line}")
    if proc.returncode not in (0, 1):
        print(f"  WARNING: rootAVD exited {proc.returncode}")
except subprocess.TimeoutExpired:
    proc.kill()
    print("  WARNING: rootAVD timed out — continuing")
print("  ✓ rootAVD done")

step(5, "Cold reboot (loads patched ramdisk)")
print("  Killing emulator and cold-booting...")
kill_emulator()
time.sleep(2)
start_emulator()
if not wait_for_boot():
    print("  ERROR: failed to boot after rootAVD"); sys.exit(1)
time.sleep(10)  # let Magisk init settle

step(6, "Magisk first-run setup (automated)")
# Open Magisk — try both known package names (official + HuskyDG fork)
for pkg in ("io.github.huskydg.magisk", "com.topjohnwu.magisk"):
    adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1", timeout=8)
    time.sleep(2)

# Auto-tap the "Requires Additional Setup → OK" dialog if it appears
tapped = tap_text("OK", timeout=24)

if tapped:
    print("  Magisk triggered reboot — waiting...")
    time.sleep(10)
    kill_emulator()
    time.sleep(2)
    start_emulator()
    if not wait_for_boot():
        print("  ERROR: failed to boot after Magisk setup"); sys.exit(1)
    time.sleep(8)
else:
    print("  No setup dialog appeared — Magisk already active")

step(7, "Verifying root")
# On google_apis, adb root always works (ro.debuggable=1)
adb("root", timeout=15)
time.sleep(3)
o, _, rc = adb("shell", "id", timeout=10)
if "uid=0" not in o:
    print(f"  WARNING: adb root returned unexpected: {o!r}")
else:
    print(f"  ✓ {o.strip()}")

# Remount /system writable
adb("remount", timeout=20)
print("  ✓ /system remounted writable")

step(8, "Installing mitmproxy cert into system trust store")
ensure_mitm_cert()
cert_hash   = get_cert_hash(MITM_CERT_PEM)
remote_cert = f"{SYSTEM_CACERTS}/{cert_hash}.0"
print(f"  Hash   : {cert_hash}")
print(f"  Target : {remote_cert}")

_, err, rc = adb("push", MITM_CERT_PEM, remote_cert, timeout=15)
if rc == 0:
    adb("shell", "chmod", "644", remote_cert, timeout=10)
    print(f"  ✓ Cert installed")
else:
    print(f"  ERROR pushing cert: {err}"); sys.exit(1)

# ── Done ─────────────────────────────────────────────────────────────────────
print("""
==================================================
  ✓ Setup complete — KukuCapture is ready
==================================================

  Now run:
      python3 GO.py

==================================================
""")

