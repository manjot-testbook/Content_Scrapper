#!/usr/bin/env python3
"""
transfer_app.py — Pull KukuTV APKs from one emulator and install on the current one.

Usage:
    python scripts/transfer_app.py
    python scripts/transfer_app.py --source emulator-5554 --target emulator-5556
"""
import argparse
import os
import subprocess
import sys
import tempfile

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
PACKAGE = "com.vlv.aravali.reels"


def adb(device, *args):
    cmd = [ADB, "-s", device] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def get_devices():
    r = subprocess.run([ADB, "devices"], capture_output=True, text=True)
    devices = []
    for line in r.stdout.splitlines()[1:]:
        if "\tdevice" in line:
            devices.append(line.split("\t")[0])
    return devices


def get_apk_paths(device, package):
    out, _, _ = adb(device, "shell", f"pm path {package}")
    paths = [l.split("package:")[-1].strip() for l in out.splitlines() if "package:" in l]
    return paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="Source device serial (has KukuTV installed)")
    parser.add_argument("--target", help="Target device serial (to install onto)")
    args = parser.parse_args()

    devices = get_devices()
    print(f"Connected devices: {devices}")

    if len(devices) == 0:
        print("ERROR: No devices connected.")
        sys.exit(1)

    if len(devices) == 1:
        # Only one device — pull APKs and save locally for manual install
        source = devices[0]
        target = devices[0]
        print(f"Only one device connected: {source}")
        print("Will pull APKs to /tmp/kukutv_apks/ for later install.")
    else:
        source = args.source or devices[0]
        target = args.target or devices[1]
        print(f"Source: {source}  →  Target: {target}")

    # Check app on source
    paths = get_apk_paths(source, PACKAGE)
    if not paths:
        print(f"ERROR: {PACKAGE} not found on {source}")
        sys.exit(1)
    print(f"Found {len(paths)} APK(s) on source: {source}")

    with tempfile.TemporaryDirectory() as tmp:
        pulled = []
        for p in paths:
            name = os.path.basename(p)
            dest = os.path.join(tmp, name)
            print(f"  Pulling {p} ...")
            _, err, code = adb(source, "pull", p, dest)
            if code != 0:
                print(f"  [WARN] {err}")
                continue
            pulled.append(dest)
            print(f"  Saved: {name} ({os.path.getsize(dest)//1024}KB)")

        if not pulled:
            print("ERROR: No APKs pulled.")
            sys.exit(1)

        if source == target:
            # Save to a permanent location
            out_dir = "/tmp/kukutv_apks"
            os.makedirs(out_dir, exist_ok=True)
            import shutil
            for f in pulled:
                shutil.copy(f, out_dir)
            print(f"\nAPKs saved to {out_dir}")
            print("To install on KukuTV_Root once it's running:")
            files = " ".join([os.path.join(out_dir, os.path.basename(f)) for f in pulled])
            print(f"  {ADB} install-multiple -r -d {files}")
            return

        print(f"\nInstalling {len(pulled)} APKs on target: {target} ...")
        cmd = [ADB, "-s", target, "install-multiple", "-r", "-d"] + pulled
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(f"  stdout: {r.stdout.strip()}")
        print(f"  stderr: {r.stderr.strip()[:300]}")

        if r.returncode == 0 or "Success" in r.stdout:
            print("\n✓ KukuTV installed on target! Log in, then: ./run.sh capture")
        elif "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in r.stderr:
            print("Signature mismatch — uninstalling from target first...")
            adb(target, "uninstall", PACKAGE)
            r2 = subprocess.run(
                [ADB, "-s", target, "install-multiple", "-d"] + pulled,
                capture_output=True, text=True
            )
            if r2.returncode == 0 or "Success" in r2.stdout:
                print("✓ Installed. Log into KukuTV, then: ./run.sh capture")
            else:
                print(f"✗ Failed: {r2.stderr.strip()[:300]}")
        else:
            print(f"✗ Install failed: {r.stderr.strip()[:300]}")


if __name__ == "__main__":
    main()
