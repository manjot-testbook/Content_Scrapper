#!/usr/bin/env python3
"""Pull KukuTV APKs from a running emulator where it's installed."""
import os, subprocess

ADB       = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
PACKAGE   = "com.vlv.aravali.reels"
APK_CACHE = "/tmp/kukutv_apks"

def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

os.makedirs(APK_CACHE, exist_ok=True)
for f in os.listdir(APK_CACHE):
    os.remove(os.path.join(APK_CACHE, f))

out, _, _ = adb("shell", f"pm path {PACKAGE}")
paths = [l.split("package:")[-1].strip() for l in out.splitlines() if "package:" in l]
if not paths:
    print(f"ERROR: {PACKAGE} not installed on connected device")
    raise SystemExit(1)

print(f"Found {len(paths)} APKs — pulling...")
for p in paths:
    name = os.path.basename(p)
    dest = os.path.join(APK_CACHE, name)
    _, err, code = adb("pull", p, dest)
    if code == 0:
        print(f"  ✓ {name} ({os.path.getsize(dest)//1024}KB)")
    else:
        print(f"  ✗ {name}: {err}")

print(f"\nAPKs saved to {APK_CACHE}")
print("Now run: python scripts/setup.py")
