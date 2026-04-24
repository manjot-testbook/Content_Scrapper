#!/usr/bin/env python3
"""Quick script to install patched base.apk + splits from APKM.
Re-signs ALL APKs (base + splits) with the same debug key so signatures match."""
import glob, os, subprocess, tempfile, zipfile, shutil

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Find apksigner
APKSIGNER = sorted(glob.glob(os.path.expanduser(
    "~/Library/Android/sdk/build-tools/*/apksigner")), reverse=True)
APKSIGNER = APKSIGNER[0] if APKSIGNER else shutil.which("apksigner")
DEBUG_KS = os.path.expanduser("~/.android/debug.keystore")

apkm_files = glob.glob(os.path.join(PROJECT, "apkm", "*.apkm"))
patched_base = os.path.join(PROJECT, "apkm", "com.vlv.aravali.reels_patched.apk")

if not apkm_files:
    print("ERROR: No .apkm found"); exit(1)
if not os.path.isfile(patched_base):
    print("ERROR: Patched APK not found — run: python scripts/patch_apk.py --no-install"); exit(1)

apkm = apkm_files[0]
print(f"APKM:         {apkm}")
print(f"Patched base: {patched_base}")
print(f"apksigner:    {APKSIGNER}")


def sign_apk(src, dest):
    """Sign an APK with the debug key using apksigner."""
    r = subprocess.run([
        APKSIGNER, "sign",
        "--ks", DEBUG_KS,
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--out", dest,
        src
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [WARN] apksigner failed for {os.path.basename(src)}: {r.stderr[:100]}")
        shutil.copy(src, dest)
    return dest


with tempfile.TemporaryDirectory() as tmp:
    signed_dir = os.path.join(tmp, "signed")
    os.makedirs(signed_dir)

    # Sign patched base
    print("\nRe-signing all APKs with debug key...")
    signed_base = os.path.join(signed_dir, "base.apk")
    sign_apk(patched_base, signed_base)
    print(f"  ✓ base.apk (patched)")

    # Extract and re-sign splits
    signed_splits = []
    with zipfile.ZipFile(apkm) as zf:
        for name in zf.namelist():
            if name.endswith(".apk") and "base" not in name.lower():
                extracted = os.path.join(tmp, name)
                zf.extract(name, tmp)
                signed_split = os.path.join(signed_dir, name)
                sign_apk(extracted, signed_split)
                signed_splits.append(signed_split)
                print(f"  ✓ {name}")

    all_apks = [signed_base] + signed_splits
    print(f"\nInstalling {len(all_apks)} signed APKs...")
    r = subprocess.run([ADB, "install-multiple", "-r", "-d"] + all_apks,
                       capture_output=True, text=True)
    print("STDOUT:", r.stdout.strip())
    print("STDERR:", r.stderr.strip())

    if r.returncode == 0 or "Success" in r.stdout:
        print("\n✓ Patched KukuTV installed! mitmproxy CA is now trusted.")
        print("  Next: ./run.sh capture")
    elif "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in r.stderr:
        print("\nSignature mismatch — uninstalling and retrying (app data will be cleared)...")
        subprocess.run([ADB, "uninstall", "com.vlv.aravali.reels"])
        r2 = subprocess.run([ADB, "install-multiple", "-r"] + all_apks,
                            capture_output=True, text=True)
        print("STDOUT:", r2.stdout.strip())
        print("STDERR:", r2.stderr.strip())
        if r2.returncode == 0 or "Success" in r2.stdout:
            print("\n✓ Installed! (App data cleared — log in again)")
        else:
            print("\n✗ Install failed:", r2.stderr[-300:])
    else:
        print("\n✗ Install failed:", r.stderr[-300:])


ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

apkm_files = glob.glob(os.path.join(PROJECT, "apkm", "*.apkm"))
patched_base = os.path.join(PROJECT, "apkm", "com.vlv.aravali.reels_patched.apk")

if not apkm_files:
    print("ERROR: No .apkm found"); exit(1)
if not os.path.isfile(patched_base):
    print("ERROR: Patched APK not found — run: python scripts/patch_apk.py --no-install"); exit(1)

apkm = apkm_files[0]
print(f"APKM:         {apkm}")
print(f"Patched base: {patched_base}")

with tempfile.TemporaryDirectory() as tmp:
    splits = []
    with zipfile.ZipFile(apkm) as zf:
        for name in zf.namelist():
            if name.endswith(".apk") and "base" not in name.lower():
                zf.extract(name, tmp)
                splits.append(os.path.join(tmp, name))
                print(f"  Split: {name}")

    all_apks = [patched_base] + splits
    print(f"\nInstalling {len(all_apks)} APKs via adb install-multiple ...")
    r = subprocess.run([ADB, "install-multiple", "-r", "-d"] + all_apks,
                       capture_output=True, text=True)
    print("STDOUT:", r.stdout.strip())
    print("STDERR:", r.stderr.strip())

    if r.returncode == 0 or "Success" in r.stdout:
        print("\n✓ Patched KukuTV installed! mitmproxy CA is now trusted.")
        print("  Next: ./run.sh capture")
    elif "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in r.stderr:
        print("\nSignature mismatch — uninstalling and retrying...")
        subprocess.run([ADB, "uninstall", "com.vlv.aravali.reels"])
        r2 = subprocess.run([ADB, "install-multiple", "-r"] + all_apks,
                            capture_output=True, text=True)
        print("STDOUT:", r2.stdout.strip())
        print("STDERR:", r2.stderr.strip())
        if r2.returncode == 0 or "Success" in r2.stdout:
            print("\n✓ Installed! (App data cleared — log in again)")
        else:
            print("\n✗ Install failed:", r2.stderr)
    else:
        print("\n✗ Install failed")
