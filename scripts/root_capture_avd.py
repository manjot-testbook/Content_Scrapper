#!/usr/bin/env python3
"""
root_capture_avd.py  —  ONE-TIME setup for the KukuCapture AVD

Run this ONCE before using GO.py. It:
  1. Creates KukuCapture AVD  (google_apis_playstore — has full Play Services)
  2. Boots it
  3. Downloads rootAVD and patches Magisk into the ramdisk  ← gives root
  4. Walks you through Magisk first-boot setup (interactive)
  5. Pre-authorises the ADB shell in Magisk so GO.py can use root silently
  6. Installs the mitmproxy CA cert as a SYSTEM cert (via Magisk module)

After this script completes, run:
    python3 GO.py

Subsequent GO.py runs reuse the rooted AVD snapshot — no re-rooting needed.
"""

import os, sys, subprocess, shutil, time, urllib.request, zipfile, stat

# ── Config ────────────────────────────────────────────────────────────────────
SDK           = os.path.expanduser("~/Library/Android/sdk")
ADB           = os.path.join(SDK, "platform-tools", "adb")
EMULATOR_BIN  = os.path.join(SDK, "emulator", "emulator")
AVDMANAGER    = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
SDKMANAGER    = os.path.join(SDK, "cmdline-tools", "latest", "bin", "sdkmanager")

AVD_NAME      = "KukuCapture"
# google_apis_playstore → full Play Services → KukuTV GMS checks pass
AVD_IMAGE     = "system-images;android-33;google_apis_playstore;arm64-v8a"
AVD_DEVICE    = "pixel_6"
RAMDISK       = os.path.join(SDK, "system-images", "android-33",
                              "google_apis_playstore", "arm64-v8a", "ramdisk.img")

HERE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR     = os.path.join(HERE, "tools")
ROOT_AVD_DIR  = os.path.join(TOOLS_DIR, "rootAVD")
ROOT_AVD_SH   = os.path.join(ROOT_AVD_DIR, "rootAVD.sh")
LOGS_DIR      = os.path.join(HERE, "logs")

MITM_CERT_PEM = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

# rootAVD — single-file script from GitLab
ROOT_AVD_URL  = "https://gitlab.com/newbit/rootAVD/-/raw/master/rootAVD.sh"


# ── Helpers ───────────────────────────────────────────────────────────────────
def run(*cmd, timeout=120, input=None):
    r = subprocess.run(list(cmd), capture_output=True, text=True,
                       timeout=timeout, input=input)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args, timeout=60):
    return run(ADB, *args, timeout=timeout)

def adb_su(*cmd, timeout=30):
    """Run a command via Magisk su. Returns (stdout, stderr, rc)."""
    return run(ADB, "shell", "su", "0", "-c", " ".join(cmd), timeout=timeout)

def wait_for_boot(label="emulator"):
    print(f"  Waiting for {label} to boot", end="", flush=True)
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

def avd_exists(name):
    o, _, _ = run(AVDMANAGER, "list", "avd")
    return name in o

def banner(msg):
    print(f"\n{'='*54}\n  {msg}\n{'='*54}")

def get_cert_hash(pem_path):
    o, e, rc = run("openssl", "x509", "-inform", "PEM",
                   "-subject_hash_old", "-in", pem_path, timeout=10)
    if rc != 0:
        print(f"  ERROR: {e}"); sys.exit(1)
    return o.splitlines()[0].strip()

def ensure_mitm_cert():
    if os.path.isfile(MITM_CERT_PEM):
        return
    print("  Generating mitmproxy CA cert...")
    p = subprocess.Popen(["mitmdump", "--listen-port", "8080"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3); p.terminate(); time.sleep(1)
    if not os.path.isfile(MITM_CERT_PEM):
        print(f"  ERROR: cert not at {MITM_CERT_PEM}"); sys.exit(1)
    print(f"  ✓ {MITM_CERT_PEM}")


# ══════════════════════════════════════════════════════════════════════════════
banner("KukuCapture — one-time root setup")
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(TOOLS_DIR, exist_ok=True)

# ── Step 1: AVD ───────────────────────────────────────────────────────────────
banner("Step 1 — KukuCapture AVD")

if avd_exists(AVD_NAME):
    ans = input(f"  AVD '{AVD_NAME}' already exists. Recreate? [y/N] ").strip().lower()
    if ans == "y":
        run(AVDMANAGER, "delete", "avd", "--name", AVD_NAME)
        print("  ✓ Deleted")
    else:
        print("  Keeping existing AVD")

if not avd_exists(AVD_NAME):
    print(f"  Installing {AVD_IMAGE} ...")
    subprocess.run([SDKMANAGER, "--install", AVD_IMAGE], check=True, timeout=600)
    result = subprocess.run(
        [AVDMANAGER, "create", "avd",
         "--name", AVD_NAME, "--package", AVD_IMAGE,
         "--device", AVD_DEVICE, "--force"],
        input="no\n", text=True, capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}"); sys.exit(1)
    print(f"  ✓ AVD '{AVD_NAME}' created  ({AVD_IMAGE})")

# ── Step 2: Download rootAVD ─────────────────────────────────────────────────
banner("Step 2 — rootAVD")

if not os.path.isfile(ROOT_AVD_SH):
    os.makedirs(ROOT_AVD_DIR, exist_ok=True)
    print(f"  Downloading rootAVD.sh from GitLab...")
    urllib.request.urlretrieve(ROOT_AVD_URL, ROOT_AVD_SH)
    os.chmod(ROOT_AVD_SH, os.stat(ROOT_AVD_SH).st_mode | stat.S_IEXEC)
    print(f"  ✓ Saved to {ROOT_AVD_SH}")
else:
    print(f"  ✓ rootAVD already at {ROOT_AVD_SH}")

# ── Step 3: Boot emulator ────────────────────────────────────────────────────
banner("Step 3 — Boot emulator (pre-root)")

# Kill any running emulator first
devs, _, _ = adb("devices")
for s in [l.split()[0] for l in devs.splitlines() if "emulator" in l]:
    run(ADB, "-s", s, "emu", "kill")
subprocess.run(["pkill", "-f", "emulator"], capture_output=True)
time.sleep(4)

print(f"  Starting '{AVD_NAME}'...")
log_path = os.path.join(LOGS_DIR, "rootavd_setup.log")
subprocess.Popen(
    [EMULATOR_BIN, "-avd", AVD_NAME,
     "-no-snapshot-save", "-no-audio",
     "-gpu", "swiftshader_indirect"],
    stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
)

if not wait_for_boot(AVD_NAME):
    print("  ERROR: Emulator did not boot. Check logs/rootavd_setup.log")
    sys.exit(1)
time.sleep(5)

# ── Step 4: Run rootAVD ───────────────────────────────────────────────────────
banner("Step 4 — Patch Magisk into ramdisk (rootAVD)")
print(f"""
  rootAVD will:
    1. Download Magisk APK from GitHub
    2. Patch {RAMDISK}
    3. Install Magisk.apk on the running emulator
    4. The emulator will reboot automatically

  This may take 1-2 minutes...
""")

result = subprocess.run(
    ["bash", ROOT_AVD_SH, RAMDISK],
    cwd=SDK,   # rootAVD uses SDK root as working dir
    timeout=300,
)
if result.returncode != 0:
    print("  WARNING: rootAVD exited non-zero — this is sometimes normal.")
    print("  Check if the emulator rebooted and Magisk is installed.")

print("\n  rootAVD finished. Waiting for emulator to reboot...")
time.sleep(10)
if not wait_for_boot("post-rootAVD"):
    print("  ERROR: Emulator did not come back after rootAVD.")
    sys.exit(1)
time.sleep(5)

# ── Step 5: Magisk first-boot setup (interactive) ─────────────────────────────
banner("Step 5 — Complete Magisk setup (manual)")
print("""
  On the EMULATOR:
    1. Find and open the  Magisk  app
    2. Tap  "OK"  on the "Requires Additional Setup" dialog
    3. The emulator will REBOOT — wait for it to fully boot

  Come back here after the reboot completes.
""")
input("  Press ENTER once the emulator has rebooted and you see the home screen ▶  ")

if not wait_for_boot("post-Magisk-setup"):
    print("  Emulator not fully booted yet, waiting longer...")
    time.sleep(15)

# ── Step 6: Pre-authorise ADB shell in Magisk ────────────────────────────────
banner("Step 6 — Pre-authorise ADB shell for silent su")
print("""
  We need to grant the ADB shell permanent root access in Magisk so
  GO.py can install certs silently on future runs.

  On the EMULATOR a "Superuser Request" popup WILL appear.
  → Tap  "Grant"
  → Tap  "Remember choice"  (toggle it ON)
""")
print("  Sending test su command (watch the emulator screen)...")
time.sleep(3)

# First su call — triggers the Magisk grant popup
o, e, rc = run(ADB, "shell", "su", "0", "-c", "echo ROOT_OK", timeout=15)
if "ROOT_OK" not in o:
    print("""
  Did not get root confirmation.
  Please:
    1. Open Magisk app on emulator → Superuser tab
    2. Make sure 'Shell' is listed and set to 'Allow'
    3. Press ENTER below and we will retry
""")
    input("  Press ENTER to retry ▶  ")
    o, e, rc = run(ADB, "shell", "su", "0", "-c", "echo ROOT_OK", timeout=15)

if "ROOT_OK" not in o:
    print(f"  ERROR: Cannot obtain root via su. Output: {o!r}  {e!r}")
    sys.exit(1)
print("  ✓ Root confirmed via Magisk su")

# Write permanent policy into Magisk DB (prevents future popups)
print("  Writing permanent shell→root policy to Magisk DB...")
adb_su(
    "sqlite3 /data/adb/magisk.db",
    '"INSERT OR REPLACE INTO policies'
    " (uid, policy, until, logging, notification)"
    ' VALUES (2000, 2, 0, 1, 0);"',
    timeout=10,
)
print("  ✓ ADB shell permanently authorised")

# ── Step 7: Install mitmproxy cert as system cert ────────────────────────────
banner("Step 7 — Install mitmproxy cert as SYSTEM cert (via Magisk module)")
ensure_mitm_cert()

cert_hash = get_cert_hash(MITM_CERT_PEM)
print(f"  Cert hash : {cert_hash}")

# Push cert to sdcard, then copy to system via su
remote_staging = f"/sdcard/{cert_hash}.0"
remote_cert    = f"/system/etc/security/cacerts/{cert_hash}.0"

adb("push", MITM_CERT_PEM, remote_staging, timeout=15)
adb_su(f"cp {remote_staging} {remote_cert}", timeout=10)
adb_su(f"chmod 644 {remote_cert}", timeout=10)
adb_su(f"rm {remote_staging}", timeout=10)

# Verify
o, _, _ = adb_su(f"ls -la {remote_cert}", timeout=10)
if cert_hash in o:
    print(f"  ✓ System cert installed: {remote_cert}")
else:
    print(f"  WARNING: cert may not have been written correctly. Output: {o!r}")

# ── Done ─────────────────────────────────────────────────────────────────────
banner("Done ✓")
print(f"""
  KukuCapture is now rooted and ready.

  What was set up:
    • AVD:       {AVD_NAME}  ({AVD_IMAGE})
    • Root:      Magisk (via rootAVD)
    • ADB shell: permanently authorised for root
    • Cert:      {remote_cert}

  Next steps:
    python3 GO.py

  GO.py will:
    • Start this AVD
    • Install original KukuTV APKs
    • Start mitmproxy
    • Guide you through login + capture
""")

