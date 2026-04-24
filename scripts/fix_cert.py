#!/usr/bin/env python3
"""
fix_cert.py — Force install mitmproxy CA into Android system cert store.

"End of input at character 0" = KukuTV getting SSL error = cert not trusted.
This script fixes it by properly installing the cert at the system level.

Run: python scripts/fix_cert.py
"""
import os, subprocess, sys, time

ADB  = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
CERT = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

def run(*args, check=False):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    return out, r.returncode

def wait_boot(label="device"):
    print(f"  Waiting for {label} to boot", end="", flush=True)
    for _ in range(60):
        out, _ = run("shell", "getprop sys.boot_completed")
        if out.strip() == "1":
            print(" ✓")
            return
        time.sleep(5); print(".", end="", flush=True)
    print(" (timed out — continuing anyway)")

# ── 0. Check device ──────────────────────────────────────────────────────────
out, _ = run("devices")
print("Devices:", out)
if "emulator" not in out and "device" not in out.split("\n",1)[-1]:
    print("ERROR: No device connected. Start the emulator first.")
    sys.exit(1)

# ── 1. Check cert file ───────────────────────────────────────────────────────
if not os.path.isfile(CERT):
    print("Generating mitmproxy cert...")
    p = subprocess.Popen(["mitmdump","--listen-port","8081"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5); p.terminate()

if not os.path.isfile(CERT):
    print(f"ERROR: {CERT} not found. Run 'mitmdump' once to generate it.")
    sys.exit(1)

# ── 2. Get cert hash ─────────────────────────────────────────────────────────
r = subprocess.run(["openssl","x509","-inform","PEM","-subject_hash_old","-in",CERT],
                   capture_output=True, text=True)
cert_hash = r.stdout.strip().splitlines()[0]
cert_name = f"{cert_hash}.0"
print(f"Cert: {CERT}")
print(f"Hash: {cert_hash}")

# ── 3. Check if already installed ────────────────────────────────────────────
out, _ = run("shell", f"ls /system/etc/security/cacerts/{cert_name} 2>/dev/null")
if cert_name in out:
    print(f"✓ Cert already in system store: {out}")
    print("  If app still shows error, reboot the emulator and try again.")
    sys.exit(0)

# ── 4. Enable root ───────────────────────────────────────────────────────────
print("\n[1] Enabling root...")
out, code = run("root")
print(f"  {out}")
if "cannot run as root" in out:
    print("ERROR: This emulator does not support adb root (production build).")
    print("You are on the wrong emulator. Need KukuTV_Root (google_apis), not Medium_Phone (google_apis_playstore).")
    sys.exit(1)
time.sleep(4)

# ── 5. Try disable-verity + reboot to allow remount ─────────────────────────
print("\n[2] Disabling verity to allow system remount...")
out, _ = run("disable-verity")
print(f"  {out}")

if "disabled" in out.lower() or "already" in out.lower():
    print("  Rebooting to apply disable-verity...")
    run("reboot")
    time.sleep(12)
    wait_boot()
    out, _ = run("root")
    print(f"  root: {out}")
    time.sleep(4)

# ── 6. Remount ───────────────────────────────────────────────────────────────
print("\n[3] Remounting system as writable...")
out, _ = run("remount")
print(f"  {out}")

# ── 7. Push cert ─────────────────────────────────────────────────────────────
print("\n[4] Pushing cert to system store...")
run("push", CERT, f"/sdcard/{cert_name}")
out, _ = run("shell", f"cp /sdcard/{cert_name} /system/etc/security/cacerts/{cert_name}")
run("shell", f"chmod 644 /system/etc/security/cacerts/{cert_name}")
run("shell", f"chown root:root /system/etc/security/cacerts/{cert_name}")

# ── 8. Verify ────────────────────────────────────────────────────────────────
out, _ = run("shell", f"ls -la /system/etc/security/cacerts/{cert_name} 2>/dev/null")
if cert_name in out:
    print(f"  ✓ Cert installed: {out}")
else:
    # Last resort: mount tmpfs over cacerts (works even without remount)
    print("  Standard copy failed — trying tmpfs overlay method...")
    run("shell", f"""
        mkdir -p /data/local/tmp/cacerts
        cp /system/etc/security/cacerts/* /data/local/tmp/cacerts/
        cp /sdcard/{cert_name} /data/local/tmp/cacerts/{cert_name}
        chmod 644 /data/local/tmp/cacerts/{cert_name}
        mount -t tmpfs tmpfs /system/etc/security/cacerts
        cp /data/local/tmp/cacerts/* /system/etc/security/cacerts/
        chown root:root /system/etc/security/cacerts/*
        chmod 644 /system/etc/security/cacerts/*
    """.strip())
    out2, _ = run("shell", f"ls /system/etc/security/cacerts/{cert_name} 2>/dev/null")
    if cert_name in out2:
        print(f"  ✓ Cert installed via tmpfs overlay (no reboot needed)")
    else:
        print("  ✗ All methods failed.")
        sys.exit(1)

# ── 9. Reboot to apply cert ──────────────────────────────────────────────────
print("\n[5] Rebooting to apply cert...")
run("reboot")
time.sleep(12)
wait_boot()

# ── 10. Verify after reboot ───────────────────────────────────────────────────
print("\n[6] Verifying cert after reboot...")
run("root"); time.sleep(3)
out, _ = run("shell", f"ls /system/etc/security/cacerts/{cert_name} 2>/dev/null")
if cert_name in out:
    print(f"  ✓ Cert persisted after reboot!")
else:
    print("  ✗ Cert not in system store after reboot — tmpfs overlay was lost.")
    print("  Re-applying tmpfs overlay (this needs to be done after each reboot)...")
    run("shell", f"""
        mkdir -p /data/local/tmp/cacerts
        cp /system/etc/security/cacerts/* /data/local/tmp/cacerts/ 2>/dev/null; true
        cp /sdcard/{cert_name} /data/local/tmp/cacerts/{cert_name}
        chmod 644 /data/local/tmp/cacerts/{cert_name}
        mount -t tmpfs tmpfs /system/etc/security/cacerts
        cp /data/local/tmp/cacerts/* /system/etc/security/cacerts/
        chmod 644 /system/etc/security/cacerts/*
    """.strip())
    out3, _ = run("shell", f"ls /system/etc/security/cacerts/{cert_name} 2>/dev/null")
    if cert_name in out3:
        print(f"  ✓ Cert applied via tmpfs overlay")
    else:
        print("  ✗ Failed. Check emulator type with: adb shell getprop ro.product.name")
        sys.exit(1)

print("""
============================================================
  ✓ mitmproxy CA cert is now trusted as SYSTEM certificate
============================================================

Next:
  1. Open KukuTV on the emulator — 'End of input' error should be gone
  2. Run: ./run.sh proxy
  3. Browse the app for 2-3 mins (home, pick a show, play a video)
  4. Run: ./run.sh analyze
""")
