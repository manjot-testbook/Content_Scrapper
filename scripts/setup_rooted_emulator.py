"""
setup_rooted_emulator.py — Create a rootable Android emulator (Google APIs, not Play Store),
install system CA cert for mitmproxy, and prepare for SSL pinning bypass.
"""

import subprocess
import sys
import os
import time

ANDROID_HOME = os.path.expanduser("~/Library/Android/sdk")
SDKMANAGER = os.path.join(ANDROID_HOME, "cmdline-tools", "latest", "bin", "sdkmanager")
AVDMANAGER = os.path.join(ANDROID_HOME, "cmdline-tools", "latest", "bin", "avdmanager")
EMULATOR = os.path.join(ANDROID_HOME, "emulator", "emulator")
ADB = os.path.join(ANDROID_HOME, "platform-tools", "adb")

# Use API 33 (Android 13) with Google APIs (rootable, no Play Store)
SYSTEM_IMAGE = "system-images;android-33;google_apis;arm64-v8a"
AVD_NAME = "KukuTV_Root"


def run(cmd, check=True):
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout.strip():
        print(f"    {r.stdout.strip()[:200]}")
    if r.returncode != 0 and check:
        print(f"    STDERR: {r.stderr.strip()[:300]}")
    return r


def main():
    print("=== Setting up Rootable Emulator for KukuTV ===\n")

    # 1. Check/install SDK tools
    if not os.path.isfile(SDKMANAGER):
        # Try alternate path
        alt = os.path.join(ANDROID_HOME, "tools", "bin", "sdkmanager")
        if os.path.isfile(alt):
            globals()["SDKMANAGER"] = alt
        else:
            print("ERROR: sdkmanager not found. Install Android SDK command-line tools.")
            print(f"  Expected at: {SDKMANAGER}")
            sys.exit(1)

    # 2. Download system image
    print("1. Downloading system image (Google APIs, rootable)...")
    run([SDKMANAGER, "--install", SYSTEM_IMAGE], check=False)

    # 3. Create AVD
    print(f"\n2. Creating AVD: {AVD_NAME}...")
    # Delete if exists
    run([AVDMANAGER, "delete", "avd", "-n", AVD_NAME], check=False)
    r = run([AVDMANAGER, "create", "avd", "-n", AVD_NAME, "-k", SYSTEM_IMAGE,
             "--device", "pixel_6", "--force"], check=False)
    if r.returncode != 0:
        print("  Trying with stdin 'no' for custom hardware...")
        r = subprocess.run(
            [AVDMANAGER, "create", "avd", "-n", AVD_NAME, "-k", SYSTEM_IMAGE,
             "--device", "pixel_6", "--force"],
            input="no\n", capture_output=True, text=True
        )
        print(f"    {r.stdout.strip()[:200]}")

    # 4. Verify
    r = run([EMULATOR, "-list-avds"])
    if AVD_NAME not in r.stdout:
        print(f"ERROR: AVD {AVD_NAME} not created")
        sys.exit(1)

    print(f"\n=== AVD '{AVD_NAME}' created! ===")
    print(f"\nTo start (with writable system for cert install):")
    print(f"  {EMULATOR} -avd {AVD_NAME} -writable-system &")
    print(f"\nThen run this script again with --install-cert to push mitmproxy CA:")
    print(f"  python3 {__file__} --install-cert")


def install_cert():
    """Install mitmproxy CA cert as system cert on a rooted emulator."""
    print("=== Installing mitmproxy CA as System Cert ===\n")

    cert_pem = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
    if not os.path.isfile(cert_pem):
        print("ERROR: mitmproxy cert not found. Run 'mitmdump' once first.")
        sys.exit(1)

    # Get cert hash for Android system store filename
    r = subprocess.run(["openssl", "x509", "-inform", "PEM", "-subject_hash_old", "-in", cert_pem],
                       capture_output=True, text=True)
    cert_hash = r.stdout.strip().splitlines()[0]
    cert_filename = f"{cert_hash}.0"
    tmp_cert = f"/tmp/{cert_filename}"

    # Copy cert with proper name
    subprocess.run(["cp", cert_pem, tmp_cert])
    print(f"Cert hash: {cert_hash}, filename: {cert_filename}")

    # Wait for device
    print("Waiting for device...")
    run([ADB, "wait-for-device"])

    # Root and remount
    print("Getting root access...")
    run([ADB, "root"])
    time.sleep(3)
    run([ADB, "wait-for-device"])

    print("Remounting system partition...")
    run([ADB, "remount"])
    time.sleep(2)

    # Push cert
    print("Pushing CA cert to system store...")
    run([ADB, "push", tmp_cert, f"/system/etc/security/cacerts/{cert_filename}"])
    run([ADB, "shell", "chmod", "644", f"/system/etc/security/cacerts/{cert_filename}"])

    # Verify
    r = run([ADB, "shell", "ls", "-la", f"/system/etc/security/cacerts/{cert_filename}"])

    # Reboot to apply
    print("\nRebooting device to apply cert...")
    run([ADB, "reboot"])
    time.sleep(10)
    run([ADB, "wait-for-device"])
    time.sleep(10)

    print("\n=== CA cert installed as system cert! ===")
    print("mitmproxy will now be trusted for all HTTPS traffic.")
    print("\nNext: Install KukuTV app and start proxy capture.")


def install_app():
    """Install KukuTV from the APKM file."""
    import zipfile, tempfile, glob

    apkm_files = glob.glob(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "apkm", "*.apkm"
    ))
    if not apkm_files:
        print("No .apkm file found in apkm/ directory")
        sys.exit(1)

    apkm = apkm_files[0]
    print(f"Installing from: {os.path.basename(apkm)}")

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(apkm) as zf:
            apks = [n for n in zf.namelist() if n.endswith(".apk")]
            for a in apks:
                zf.extract(a, tmp)
                print(f"  Extracted: {a}")

        apk_paths = [os.path.join(tmp, a) for a in apks]
        r = run([ADB, "install-multiple", "-r"] + apk_paths)
        if r.returncode == 0:
            print("\n✓ App installed!")
        else:
            print(f"\n✗ Install failed. Try installing from Play Store on this emulator.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-cert", action="store_true")
    parser.add_argument("--install-app", action="store_true")
    args = parser.parse_args()

    if args.install_cert:
        install_cert()
    elif args.install_app:
        install_app()
    else:
        main()
