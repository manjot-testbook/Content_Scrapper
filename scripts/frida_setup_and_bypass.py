#!/usr/bin/env python3
"""
frida_setup_and_bypass.py — Attach Frida SSL bypass to a running KukuTV process.

Steps:
  1. Finds the emulator where KukuTV is installed
  2. Pushes + starts frida-server (works on emulators even without root due to permissive SELinux)
  3. Starts mitmproxy + sets proxy
  4. Waits for you to open KukuTV manually
  5. Attaches Frida SSL bypass to the running process

Run: python scripts/frida_setup_and_bypass.py
"""
import os, subprocess, sys, urllib.request, lzma, shutil, time, socket

ADB     = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "com.vlv.aravali.reels"
FRIDA_DEVICE_PATH = "/data/local/tmp/frida-server"
FRIDA_VERSION = "17.9.1"

def adb(*args, serial=None):
    cmd = [ADB]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def sh(cmd, serial=None):
    out, err, _ = adb("shell", cmd, serial=serial)
    return (out + err).strip()

def get_devices():
    out, _, _ = adb("devices")
    return [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]

def find_kuku_device(devices):
    for d in devices:
        out, _, _ = adb("shell", f"pm list packages | grep {PACKAGE}", serial=d)
        if PACKAGE in out:
            return d
    return None

def wait_for_process(serial, timeout=120):
    print(f"  Waiting for KukuTV to be opened (up to {timeout}s)...", end="", flush=True)
    for _ in range(timeout // 3):
        out = sh(f"pidof {PACKAGE} 2>/dev/null", serial=serial)
        if out.strip():
            print(f" found PID {out.strip()} ✓")
            return out.strip()
        time.sleep(3); print(".", end="", flush=True)
    print(" TIMEOUT")
    return None

print("\n=== Frida SSL Bypass (Attach Mode) ===\n")

# ── 0. Find the right device ──────────────────────────────────────────────────
devices = get_devices()
print(f"[0] Connected devices: {devices}")

serial = find_kuku_device(devices)
if not serial:
    print(f"ERROR: KukuTV ({PACKAGE}) not found on any connected device.")
    print("Make sure Medium_Phone emulator is running with KukuTV installed.")
    sys.exit(1)

print(f"    ✓ KukuTV found on: {serial}")
avd_name = sh("getprop ro.kernel.qemu.avd_name 2>/dev/null || getprop ro.boot.qemu.avd_name 2>/dev/null", serial=serial)
print(f"    AVD: {avd_name or 'unknown'}")

# ── 1. Get device arch + download frida-server ────────────────────────────────
arch = sh("getprop ro.product.cpu.abi", serial=serial)
frida_arch = {"arm64-v8a": "arm64", "armeabi-v7a": "arm", "x86_64": "x86_64", "x86": "x86"}.get(arch, "arm64")
print(f"\n[1] Device arch: {arch} → frida: {frida_arch}")

frida_local = f"/tmp/frida-server-{FRIDA_VERSION}-{frida_arch}"
if os.path.isfile(frida_local) and os.path.getsize(frida_local) > 1_000_000:
    print(f"    Using cached: {frida_local}")
else:
    url = f"https://github.com/frida/frida/releases/download/{FRIDA_VERSION}/frida-server-{FRIDA_VERSION}-android-{frida_arch}.xz"
    print(f"    Downloading frida-server...")
    xz = frida_local + ".xz"
    urllib.request.urlretrieve(url, xz)
    with lzma.open(xz) as xf, open(frida_local, "wb") as of:
        shutil.copyfileobj(xf, of)
    os.remove(xz)
    print(f"    ✓ {os.path.getsize(frida_local)//1024}KB")

# ── 2. Push + start frida-server ─────────────────────────────────────────────
print("\n[2] Starting frida-server on device...")
sh(f"pkill -f frida-server 2>/dev/null; true", serial=serial)
time.sleep(1)

adb("push", frida_local, FRIDA_DEVICE_PATH, serial=serial)
sh(f"chmod 755 {FRIDA_DEVICE_PATH}", serial=serial)

# Try root first, then shell user (emulators have permissive SELinux)
root_out, _, _ = adb("root", serial=serial)
time.sleep(3)
is_rooted = "cannot run as root" not in root_out
print(f"    Root: {'✓' if is_rooted else '✗ (will try shell user)'}")

# Start frida-server as background process
p = subprocess.Popen(
    [ADB, "-s", serial, "shell", f"{FRIDA_DEVICE_PATH}"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(4)

# Verify
running = sh(f"ps -A 2>/dev/null | grep frida-server | grep -v grep", serial=serial)
if running:
    print(f"    ✓ frida-server running")
else:
    print(f"    ! frida-server not confirmed — will try anyway (permissive SELinux on emulators usually allows it)")

# ── 3. Proxy off, start mitmproxy, proxy on ───────────────────────────────────
print("\n[3] Starting mitmproxy + setting proxy...")
subprocess.run(["pkill", "-f", "mitmdump"], capture_output=True)
time.sleep(1)

traffic_log = os.path.join(PROJECT, "metadata", "captured_apis", "api_traffic.jsonl")
os.makedirs(os.path.dirname(traffic_log), exist_ok=True)
open(traffic_log, "w").close()

mitm_log = os.path.join(PROJECT, "logs", "mitm.log")
subprocess.Popen(
    ["mitmdump", "-s", os.path.join(PROJECT, "mitm_addons", "mitm_addon.py"),
     "--listen-port", "8080", "--ssl-insecure"],
    stdout=open(mitm_log, "w"), stderr=subprocess.STDOUT
)
time.sleep(3)

try:
    s = socket.socket(); s.settimeout(3); s.connect(("8.8.8.8", 80))
    host_ip = s.getsockname()[0]; s.close()
except Exception:
    host_ip = "10.0.2.2"

adb("shell", "settings", "put", "global", "http_proxy", f"{host_ip}:8080", serial=serial)
print(f"    ✓ mitmproxy running, proxy → {host_ip}:8080")

# ── 4. Wait for KukuTV to be open ─────────────────────────────────────────────
print(f"\n[4] Open KukuTV on the emulator NOW (don't use Frida spawn — open it manually)")
print(f"    Waiting for the app to start...")
pid = wait_for_process(serial)

if not pid:
    print("  KukuTV not detected. Trying to launch it...")
    sh(f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1 2>/dev/null", serial=serial)
    time.sleep(5)
    pid = wait_for_process(serial, timeout=60)

if not pid:
    print("ERROR: KukuTV process not found. Open it manually and re-run.")
    sys.exit(1)

# ── 5. Attach Frida bypass to running process ─────────────────────────────────
print(f"\n[5] Attaching Frida SSL bypass to PID {pid}...")

js_path = os.path.join(PROJECT, "mitm_addons", "frida_ssl_bypass.js")
if not os.path.isfile(js_path):
    js_path = "/tmp/ssl_bypass.js"
    open(js_path, "w").write("""
Java.perform(function() {
    try {
        var TrustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
        TrustManagerImpl.verifyChain.implementation = function(a,b,c,d,e,f) { return a; };
    } catch(e) {}
    try {
        var CertificatePinner = Java.use('okhttp3.CertificatePinner');
        CertificatePinner.check.overload('java.lang.String','java.util.List').implementation = function() {};
        CertificatePinner.check.overload('java.lang.String','java.security.cert.Certificate[]').implementation = function() {};
    } catch(e) {}
    try {
        var NetworkSecurityTrustManager = Java.use('android.security.net.config.NetworkSecurityTrustManager');
        NetworkSecurityTrustManager.checkServerTrusted.implementation = function() {};
    } catch(e) {}
    try {
        var TrustManagerImpl2 = Java.use('com.android.org.conscrypt.TrustManagerImpl');
        TrustManagerImpl2.checkTrusted.implementation = function() { return []; };
    } catch(e) {}
    console.log('[+] SSL bypass injected!');
});
""")

# Attach to running process (not spawn)
frida_cmd = [
    "frida",
    "-U",                    # USB/emulator
    "-s", serial,            # specific device
    "-p", pid,               # attach to PID
    "-l", js_path,           # inject script
]
print(f"    {' '.join(frida_cmd)}")
print("""
============================================================
  Frida attached! SSL bypass is active.
  Browse KukuTV: home → show → play video → back → more shows
  Press Ctrl+C when done (2-3 minutes of browsing is enough)
============================================================
""")

try:
    subprocess.run(frida_cmd)
except KeyboardInterrupt:
    print("\nStopping...")

# ── 6. Results ────────────────────────────────────────────────────────────────
adb("shell", "settings", "put", "global", "http_proxy", ":0", serial=serial)
adb("shell", "settings", "delete", "global", "http_proxy", serial=serial)

lines = open(traffic_log).readlines() if os.path.isfile(traffic_log) else []
kuku  = [l for l in lines if '"is_kukutv": true' in l]
print(f"\n=== Results ===")
print(f"Total captured : {len(lines)}")
print(f"KukuTV API hits: {len(kuku)}")
if kuku:
    print("\n✓ Success! Run:\n  ./run.sh analyze\n  ./run.sh scrape")
else:
    print("\nNo KukuTV traffic. Check logs/mitm.log for errors.")
