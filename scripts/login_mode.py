#!/usr/bin/env python3
"""
login_mode.py — Toggle proxy OFF for login, ON for capture.

Usage:
    python scripts/login_mode.py off   # disable proxy so OTP works
    python scripts/login_mode.py on    # re-enable proxy for capture
"""
import subprocess, sys, time, socket, os

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")

def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip()

mode = sys.argv[1].lower() if len(sys.argv) > 1 else "off"

if mode == "off":
    adb("shell", "settings", "put", "global", "http_proxy", ":0")
    adb("shell", "settings", "delete", "global", "http_proxy")
    val = adb("shell", "settings", "get", "global", "http_proxy")
    print(f"Proxy OFF. Current value: '{val}'")
    print("KukuTV OTP login will now work normally.")
    print("After logging in, run:  python scripts/login_mode.py on")

elif mode == "on":
    try:
        s = socket.socket(); s.settimeout(3)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
    except Exception:
        ip = "10.0.2.2"
    adb("shell", "settings", "put", "global", "http_proxy", f"{ip}:8080")
    val = adb("shell", "settings", "get", "global", "http_proxy")
    print(f"Proxy ON: {val}")
    print("mitmproxy must be running. Start it with:  ./run.sh proxy")
    print("Then browse KukuTV to capture API traffic.")
