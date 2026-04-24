#!/usr/bin/env python3
"""
frida_setup_and_bypass.py — One shot: download frida-server, push to device,
start it, launch KukuTV, inject SSL bypass, then run mitmproxy.

Run: python scripts/frida_setup_and_bypass.py
"""
import os, subprocess, sys, urllib.request, lzma, shutil, time, socket, json

ADB     = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "com.vlv.aravali.reels"
FRIDA_SERVER_DEVICE_PATH = "/data/local/tmp/frida-server"
FRIDA_VERSION = "17.9.1"

def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def sh(cmd):
    out, err, _ = adb("shell", cmd)
    return (out + err).strip()

# ── 0. Check device ───────────────────────────────────────────────────────────
print("\n=== Frida SSL Bypass Setup ===\n")
out, _, _ = adb("devices")
if "emulator" not in out and "device" not in out.split("\n",1)[-1]:
    print("ERROR: No emulator connected."); sys.exit(1)

# Get device arch
arch = sh("getprop ro.product.cpu.abi").strip()
# Map to frida arch name
frida_arch = {"arm64-v8a": "arm64", "armeabi-v7a": "arm", "x86_64": "x86_64", "x86": "x86"}.get(arch, "arm64")
print(f"Device arch: {arch} → frida arch: {frida_arch}")

# ── 1. Download frida-server ──────────────────────────────────────────────────
frida_local = f"/tmp/frida-server-{FRIDA_VERSION}-{frida_arch}"
if os.path.isfile(frida_local) and os.path.getsize(frida_local) > 1_000_000:
    print(f"[1] Using cached frida-server: {frida_local}")
else:
    url = f"https://github.com/frida/frida/releases/download/{FRIDA_VERSION}/frida-server-{FRIDA_VERSION}-android-{frida_arch}.xz"
    xz_path = frida_local + ".xz"
    print(f"[1] Downloading frida-server {FRIDA_VERSION} ({frida_arch})...")
    print(f"    {url}")
    try:
        urllib.request.urlretrieve(url, xz_path)
        print(f"    Downloaded {os.path.getsize(xz_path)//1024}KB — decompressing...")
        with lzma.open(xz_path) as xz_f, open(frida_local, "wb") as out_f:
            shutil.copyfileobj(xz_f, out_f)
        os.remove(xz_path)
        print(f"    Extracted: {os.path.getsize(frida_local)//1024}KB")
    except Exception as e:
        print(f"    FAILED: {e}"); sys.exit(1)

# ── 2. Push frida-server to device ───────────────────────────────────────────
print("\n[2] Pushing frida-server to device...")
adb("push", frida_local, FRIDA_SERVER_DEVICE_PATH)
sh(f"chmod 755 {FRIDA_SERVER_DEVICE_PATH}")
print("    ✓ Pushed and made executable")

# ── 3. Kill any existing frida-server, start fresh ───────────────────────────
print("\n[3] Starting frida-server on device...")
sh("pkill -f frida-server 2>/dev/null; true")
time.sleep(1)
# Start in background
subprocess.Popen(
    [ADB, "shell", f"{FRIDA_SERVER_DEVICE_PATH} &"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(3)

# Verify it's running
out = sh("ps aux 2>/dev/null | grep frida-server | grep -v grep")
if not out:
    # Try with adb shell nohup
    sh(f"nohup {FRIDA_SERVER_DEVICE_PATH} > /dev/null 2>&1 &")
    time.sleep(3)
    out = sh("ps aux 2>/dev/null | grep frida-server | grep -v grep")

if out:
    print(f"    ✓ frida-server running: {out[:80]}")
else:
    print("    ! frida-server may not be running — continuing anyway")

# ── 4. Start mitmproxy ───────────────────────────────────────────────────────
print("\n[4] Starting mitmproxy...")
# Kill existing
subprocess.run(["pkill", "-f", "mitmdump"], capture_output=True)
time.sleep(1)
# Clear old traffic log
traffic_log = os.path.join(PROJECT, "metadata", "captured_apis", "api_traffic.jsonl")
os.makedirs(os.path.dirname(traffic_log), exist_ok=True)
open(traffic_log, "w").close()

mitm_log = os.path.join(PROJECT, "logs", "mitm.log")
mitm_proc = subprocess.Popen(
    ["mitmdump", "-s", os.path.join(PROJECT, "mitm_addons", "mitm_addon.py"),
     "--listen-port", "8080", "--ssl-insecure", "--set", "flow_detail=0"],
    stdout=open(mitm_log, "w"), stderr=subprocess.STDOUT
)
time.sleep(3)
print(f"    ✓ mitmproxy PID {mitm_proc.pid}")

# ── 5. Set device proxy ───────────────────────────────────────────────────────
print("\n[5] Setting device proxy...")
try:
    s = socket.socket(); s.settimeout(3); s.connect(("8.8.8.8", 80))
    host_ip = s.getsockname()[0]; s.close()
except Exception:
    host_ip = "10.0.2.2"
adb("shell", "settings", "put", "global", "http_proxy", f"{host_ip}:8080")
print(f"    ✓ Proxy set to {host_ip}:8080")

# ── 6. Launch KukuTV + inject Frida bypass ────────────────────────────────────
print(f"\n[6] Launching {PACKAGE} with Frida SSL bypass...")

# Read JS bypass script
js_path = os.path.join(PROJECT, "mitm_addons", "frida_ssl_bypass.js")
if not os.path.isfile(js_path):
    # Use universal SSL bypass script
    js_path = "/tmp/ssl_bypass.js"
    ssl_bypass_js = r"""
Java.perform(function() {
    // Disable TrustManager
    try {
        var TrustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
        TrustManagerImpl.verifyChain.implementation = function(untrustedChain, trustAnchorChain, host, clientAuth, ocspData, tlsSctData) {
            return untrustedChain;
        };
    } catch(e) {}

    // Bypass OkHttp CertificatePinner
    try {
        var CertificatePinner = Java.use('okhttp3.CertificatePinner');
        CertificatePinner.check.overload('java.lang.String', 'java.util.List').implementation = function() {};
        CertificatePinner.check.overload('java.lang.String', 'java.security.cert.Certificate[]').implementation = function() {};
    } catch(e) {}

    // Bypass X509TrustManager
    try {
        var X509TrustManager = Java.use('javax.net.ssl.X509TrustManager');
        var SSLContext = Java.use('javax.net.ssl.SSLContext');
        var TrustManager = Java.registerClass({
            name: 'com.custom.TrustManager',
            implements: [X509TrustManager],
            methods: {
                checkClientTrusted: function(chain, authType) {},
                checkServerTrusted: function(chain, authType) {},
                getAcceptedIssuers: function() { return []; }
            }
        });
        var TrustManagers = [TrustManager.$new()];
        var SSLContextObj = SSLContext.getInstance('TLS');
        SSLContextObj.init(null, TrustManagers, null);
        var defaultSSLContext = SSLContext.getDefault;
        SSLContext.getDefault.implementation = function() { return SSLContextObj; };
    } catch(e) {}

    // Bypass HttpsURLConnection
    try {
        var HttpsURLConnection = Java.use('javax.net.ssl.HttpsURLConnection');
        HttpsURLConnection.setDefaultHostnameVerifier.implementation = function(verifier) {};
        HttpsURLConnection.setSSLSocketFactory.implementation = function(factory) {};
        HttpsURLConnection.setHostnameVerifier.implementation = function(verifier) {};
    } catch(e) {}

    // Disable network security config checks
    try {
        var NetworkSecurityTrustManager = Java.use('android.security.net.config.NetworkSecurityTrustManager');
        NetworkSecurityTrustManager.checkServerTrusted.implementation = function() {};
    } catch(e) {}

    console.log('[+] SSL bypass injected successfully');
});
"""
    open(js_path, "w").write(ssl_bypass_js)

print(f"    Using bypass script: {js_path}")

# Spawn with Frida
frida_cmd = ["frida", "-U", "-f", PACKAGE, "-l", js_path, "--no-pause"]
print(f"    Running: {' '.join(frida_cmd)}")
print("\n" + "="*60)
print("  KukuTV is launching with SSL bypass active.")
print("  Browse the app: home screen → pick a show → play a video")
print("  Press Ctrl+C when done browsing to stop capture.")
print("="*60 + "\n")

try:
    subprocess.run(frida_cmd)
except KeyboardInterrupt:
    pass

# ── 7. Show results ───────────────────────────────────────────────────────────
print("\n\n=== Capture Results ===")
count = 0
kuku = 0
if os.path.isfile(traffic_log):
    with open(traffic_log) as f:
        lines = f.readlines()
    count = len(lines)
    kuku = sum(1 for l in lines if '"is_kukutv": true' in l)
print(f"Total requests: {count}")
print(f"KukuTV requests: {kuku}")

if kuku > 0:
    print("\n✓ KukuTV API traffic captured! Now run:")
    print("  ./run.sh analyze")
    print("  ./run.sh scrape")
else:
    print("\nNo KukuTV traffic captured yet.")
    print("Make sure you browsed the app while Frida was running.")

# Clear proxy
adb("shell", "settings", "put", "global", "http_proxy", ":0")
adb("shell", "settings", "delete", "global", "http_proxy")
print("\nProxy cleared.")
