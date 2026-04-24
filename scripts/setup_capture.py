#!/usr/bin/env python3
"""
setup_capture.py — One-shot setup: mitmproxy cert + KukuTV installed on rootable emulator.

What this does:
  1. Starts KukuTV_Root emulator (google_apis, adb root works)
  2. Enables root + remounts system
  3. Installs mitmproxy CA as system certificate
  4. Pulls KukuTV APKs from Medium_Phone_API_36.1 (if running) OR from /tmp/kukutv_apks/
  5. Installs KukuTV on KukuTV_Root
  6. Tries adb backup/restore of app data so you don't need OTP again
  7. Starts mitmproxy + sets device proxy

Usage:
    python scripts/setup_capture.py

Run Medium_Phone emulator first (with KukuTV logged in), then run this script.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

ADB      = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
EMULATOR = os.path.expanduser("~/Library/Android/sdk/emulator/emulator")
PROJECT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE  = "com.vlv.aravali.reels"
ROOT_AVD = "KukuTV_Root"
CERT_PEM = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
APK_CACHE = "/tmp/kukutv_apks"


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def adb(serial, *args):
    return run([ADB, "-s", serial] + list(args))


def get_devices():
    out, _, _ = run([ADB, "devices"])
    return [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]


def wait_boot(serial, timeout=180):
    print(f"  Waiting for {serial} to boot", end="", flush=True)
    for _ in range(timeout // 5):
        boot, _, _ = adb(serial, "shell", "getprop sys.boot_completed")
        if boot.strip() == "1":
            print(" ✓")
            return True
        time.sleep(5)
        print(".", end="", flush=True)
    print(" TIMEOUT")
    return False


def find_emulator_serial(avd_name, devices):
    """Find which serial corresponds to a given AVD name."""
    for d in devices:
        out, _, _ = run([ADB, "-s", d, "emu", "avd", "name"])
        # emu avd name returns the AVD name on first line
        first_line = out.splitlines()[0].strip() if out else ""
        if first_line.lower() == avd_name.lower():
            return d
    return None


def start_avd(avd_name):
    log = os.path.join(PROJECT, "logs", "emulator.log")
    os.makedirs(os.path.join(PROJECT, "logs"), exist_ok=True)
    proc = subprocess.Popen(
        [EMULATOR, "-avd", avd_name, "-writable-system", "-no-snapshot-save", "-no-audio"],
        stdout=open(log, "w"), stderr=subprocess.STDOUT
    )
    return proc


def ensure_mitm_cert():
    if not os.path.isfile(CERT_PEM):
        print("  Generating mitmproxy cert (running mitmdump briefly)...")
        p = subprocess.Popen(
            ["mitmdump", "--listen-port", "8081"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(5)
        p.terminate()
    if not os.path.isfile(CERT_PEM):
        print("ERROR: Could not find mitmproxy-ca-cert.pem — run 'mitmdump' manually once.")
        sys.exit(1)
    return CERT_PEM


def install_system_cert(serial, cert_pem):
    r = subprocess.run(
        ["openssl", "x509", "-inform", "PEM", "-subject_hash_old", "-in", cert_pem],
        capture_output=True, text=True
    )
    cert_hash = r.stdout.strip().splitlines()[0]
    cert_name = f"{cert_hash}.0"
    print(f"  Cert hash: {cert_hash}")

    # Push cert to sdcard
    adb(serial, "push", cert_pem, f"/sdcard/{cert_name}")

    # Try system store (requires root + remount)
    adb(serial, "shell", f"cp /sdcard/{cert_name} /system/etc/security/cacerts/{cert_name}")
    adb(serial, "shell", f"chmod 644 /system/etc/security/cacerts/{cert_name}")

    out, _, _ = adb(serial, "shell", f"ls /system/etc/security/cacerts/{cert_name}")
    if cert_name in out:
        print(f"  ✓ System cert installed (system store)")
        return True

    # Fallback: user cert store via root — works on google_apis without remount
    print("  System store failed — trying user cert store via root...")
    user_cert_dir = "/data/misc/user/0/cacerts-added"
    adb(serial, "shell", f"mkdir -p {user_cert_dir}")
    adb(serial, "shell", f"cp /sdcard/{cert_name} {user_cert_dir}/{cert_name}")
    adb(serial, "shell", f"chmod 644 {user_cert_dir}/{cert_name}")
    adb(serial, "shell", f"chown root:root {user_cert_dir}/{cert_name}")

    out2, _, _ = adb(serial, "shell", f"ls {user_cert_dir}/{cert_name}")
    if cert_name in out2:
        print(f"  ✓ Cert installed in user cert store (trusted by all apps on rooted emulator)")
        return True

    print(f"  ✗ Cert install failed on both stores")
    return False


def pull_apks(serial):
    """Pull KukuTV APKs from device to APK_CACHE."""
    out, _, _ = adb(serial, "shell", f"pm path {PACKAGE}")
    paths = [l.split("package:")[-1].strip() for l in out.splitlines() if "package:" in l]
    if not paths:
        return []
    os.makedirs(APK_CACHE, exist_ok=True)
    # Clear old cache
    for f in glob.glob(os.path.join(APK_CACHE, "*.apk")):
        os.remove(f)
    pulled = []
    for p in paths:
        name = os.path.basename(p)
        dest = os.path.join(APK_CACHE, name)
        _, err, code = adb(serial, "pull", p, dest)
        if code == 0:
            pulled.append(dest)
            print(f"  Pulled: {name} ({os.path.getsize(dest)//1024}KB)")
        else:
            print(f"  [WARN] pull failed: {err}")
    return pulled


def install_apks(serial, apks):
    """Install APKs onto target device."""
    cmd = [ADB, "-s", serial, "install-multiple", "-r", "-d"] + apks
    out, err, code = run(cmd)
    if code == 0 or "Success" in out:
        return True
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in err:
        print("  Signature mismatch — uninstalling old version...")
        adb(serial, "uninstall", PACKAGE)
        out2, err2, code2 = run([ADB, "-s", serial, "install-multiple", "-d"] + apks)
        return code2 == 0 or "Success" in out2
    print(f"  Install error: {err[:300]}")
    return False


def backup_app_data(serial):
    """Skip backup - adb backup requires screen tap and KukuTV blocks it (allowBackup=false)."""
    print("  Skipping backup (KukuTV blocks adb backup) — one OTP login will be needed.")
    return None


def restore_app_data(serial, backup_path):
    print("  Restoring app data (tap 'Restore my data' on screen if prompted)...")
    cmd = [ADB, "-s", serial, "restore", backup_path]
    subprocess.run(cmd, timeout=30, capture_output=True, text=True)
    time.sleep(3)


def main():
    print("\n" + "="*60)
    print("  KukuTV Capture Setup — One Shot")
    print("="*60 + "\n")

    devices = get_devices()
    print(f"Connected devices: {devices or 'none'}")

    # ── Step 1: Pull APKs from Medium_Phone (if running) ────────────────
    source_serial = None
    for d in devices:
        out, _, _ = adb(d, "shell", f"pm list packages")
        if PACKAGE in out:
            source_serial = d
            print(f"\n[1] KukuTV found on {d} — pulling APKs...")
            apks = pull_apks(d)
            if apks:
                print(f"  ✓ {len(apks)} APK(s) cached to {APK_CACHE}")
            break

    if not source_serial:
        # Check cache
        cached = sorted(glob.glob(os.path.join(APK_CACHE, "*.apk")))
        if cached:
            print(f"\n[1] Using {len(cached)} cached APKs from {APK_CACHE}")
            apks = cached
        else:
            print("\n[1] ERROR: KukuTV not found on any device and no cached APKs.")
            print("    Start Medium_Phone emulator with KukuTV installed, then re-run.")
            sys.exit(1)

    # ── Step 2: Backup app data before switching emulators ───────────────
    backup_path = None
    if source_serial:
        print(f"\n[2] Backing up KukuTV data from {source_serial}...")
        backup_path = backup_app_data(source_serial)

    # ── Step 3: Start KukuTV_Root AVD ───────────────────────────────────
    root_serial = find_emulator_serial(ROOT_AVD, devices)
    if root_serial:
        print(f"\n[3] KukuTV_Root already running: {root_serial}")
    else:
        print(f"\n[3] Starting {ROOT_AVD} emulator...")
        start_avd(ROOT_AVD)
        time.sleep(10)
        # Wait for it to appear in adb devices
        for _ in range(24):
            devices = get_devices()
            new = [d for d in devices if d not in (source_serial or [])]
            if new:
                root_serial = new[-1]
                break
            time.sleep(5)
        if not root_serial:
            print("  ERROR: KukuTV_Root emulator not detected by adb.")
            sys.exit(1)
        print(f"  Detected: {root_serial}")

    wait_boot(root_serial)

    # ── Step 4: Enable root + remount ────────────────────────────────────
    print(f"\n[4] Enabling ADB root on {root_serial}...")
    out, err, code = adb(root_serial, "root")
    print(f"  {out or err}")
    time.sleep(5)  # must wait after root before remount
    out, err, code = adb(root_serial, "remount")
    print(f"  remount: {out or err}")
    if "remount failed" in (out + err).lower():
        # Try disable-verity first then remount
        print("  Trying disable-verity...")
        adb(root_serial, "disable-verity")
        time.sleep(2)
        adb(root_serial, "reboot")
        time.sleep(10)
        wait_boot(root_serial)
        adb(root_serial, "root")
        time.sleep(5)
        out2, err2, _ = adb(root_serial, "remount")
        print(f"  remount after disable-verity: {out2 or err2}")
    time.sleep(2)

    # ── Step 5: Install mitmproxy system cert ────────────────────────────
    print(f"\n[5] Installing mitmproxy CA as system certificate...")
    cert = ensure_mitm_cert()
    if not install_system_cert(root_serial, cert):
        print("  WARNING: Cert install failed — trying after reboot...")

    # ── Step 6: Install KukuTV APKs ─────────────────────────────────────
    print(f"\n[6] Installing KukuTV on {root_serial}...")
    if install_apks(root_serial, apks):
        print("  ✓ KukuTV installed")
    else:
        print("  ✗ Install failed — check APKs")
        sys.exit(1)

    # ── Step 7: Restore app data ─────────────────────────────────────────
    if backup_path:
        print(f"\n[7] Restoring KukuTV data (login session)...")
        restore_app_data(root_serial, backup_path)
        print("  Done — launch KukuTV to check if you're still logged in")
    else:
        print(f"\n[7] No backup available — you will need to log into KukuTV once more (OTP)")
        print("    This is a one-time step. After this, everything will work without changes.")

    # ── Step 8: Reboot to apply cert ────────────────────────────────────
    print(f"\n[8] Rebooting {root_serial} to apply system cert...")
    adb(root_serial, "reboot")
    time.sleep(15)
    wait_boot(root_serial)

    # ── Step 9: Set proxy ────────────────────────────────────────────────
    print(f"\n[9] Starting mitmproxy and setting device proxy...")
    # Get host IP without external connection
    import socket
    try:
        s = socket.socket(); s.settimeout(3); s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]; s.close()
    except Exception:
        # Fallback: use 10.0.2.2 which is the emulator's alias for the host machine
        host_ip = "10.0.2.2"
    adb(root_serial, "shell", "settings", "put", "global", "http_proxy", f"{host_ip}:8080")

    traffic_log = os.path.join(PROJECT, "metadata", "captured_apis", "api_traffic.jsonl")
    os.makedirs(os.path.dirname(traffic_log), exist_ok=True)
    # Clear old log
    open(traffic_log, "w").close()

    mitm_log = os.path.join(PROJECT, "logs", "mitm.log")
    subprocess.Popen(
        ["mitmdump", "-s", os.path.join(PROJECT, "mitm_addons", "mitm_addon.py"),
         "--listen-port", "8080", "--ssl-insecure"],
        stdout=open(mitm_log, "w"), stderr=subprocess.STDOUT
    )
    time.sleep(3)
    print(f"  ✓ Proxy set to {host_ip}:8080 — mitmproxy running")

    print("\n" + "="*60)
    print("  ✓ SETUP COMPLETE")
    print("="*60)
    print(f"""
Next steps:
  1. Open KukuTV on the emulator
  2. Browse: home screen → pick a show → play a video → browse episodes
  3. After 2-3 minutes run:  ./run.sh analyze
  4. Then run:               ./run.sh scrape

If KukuTV asks for login (OTP) — do it once, it's permanent on this emulator.
""")


if __name__ == "__main__":
    main()
