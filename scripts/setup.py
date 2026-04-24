#!/usr/bin/env python3
"""
setup.py - Complete fresh setup for KukuTV API capture.

What this does (in order):
  1. Creates a new rootable AVD (google_apis, API 33)
  2. Starts it with -writable-system so /system is writable
  3. adb root + adb remount
  4. Pushes MicroG directly into /system/priv-app/ (replaces GMS, no signature conflict)
  5. Installs mitmproxy CA cert as system cert
  6. Reboots
  7. Installs KukuTV APKs
  8. Starts mitmproxy + sets proxy

Run: python scripts/setup.py
"""
import os, subprocess, sys, time, json, lzma, shutil, urllib.request, tempfile

# ── Config ────────────────────────────────────────────────────────────────────
ADB      = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
EMULATOR = os.path.expanduser("~/Library/Android/sdk/emulator/emulator")
PROJECT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AVD_NAME = "KukuCapture"
PACKAGE  = "com.vlv.aravali.reels"
APK_CACHE= "/tmp/kukutv_apks"
CERT_PEM = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

# ── Helpers ───────────────────────────────────────────────────────────────────
def run(*cmd, check=False):
    r = subprocess.run(list(cmd), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args):
    return run(ADB, *args)

def sh(cmd):
    out, err, _ = adb("shell", cmd)
    return (out + err).strip()

def wait_boot(label="device"):
    print(f"  Waiting for boot", end="", flush=True)
    for _ in range(60):
        out, _, _ = adb("shell", "getprop sys.boot_completed")
        if out.strip() == "1":
            print(" ✓")
            return True
        time.sleep(5); print(".", end="", flush=True)
    print(" timed out")
    return False

def download(url, dest):
    print(f"  Downloading {url.split('/')[-1]}...", end="", flush=True)
    urllib.request.urlretrieve(url, dest)
    print(f" {os.path.getsize(dest)//1024}KB ✓")

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("  KukuTV Capture Setup — Fresh Start")
print("="*60 + "\n")

# ── Step 1: Create AVD manually (no avdmanager/Java needed) ──
print("[1] Creating AVD manually (no avdmanager needed)...")
SDK = os.path.expanduser("~/Library/Android/sdk")
SYS_IMG = "system-images/android-33/google_apis/arm64-v8a"
AVD_DIR = os.path.expanduser(f"~/.android/avd/{AVD_NAME}.avd")
AVD_INI = os.path.expanduser(f"~/.android/avd/{AVD_NAME}.ini")

if os.path.exists(AVD_DIR): shutil.rmtree(AVD_DIR)
if os.path.exists(AVD_INI): os.remove(AVD_INI)
os.makedirs(AVD_DIR)

open(AVD_INI,"w").write(
    f"avd.ini.encoding=UTF-8\n"
    f"path={AVD_DIR}\n"
    f"path.rel=avd/{AVD_NAME}.avd\n"
    f"target=android-33\n"
)
open(os.path.join(AVD_DIR,"config.ini"),"w").write(
    f"AvdId={AVD_NAME}\n"
    f"avd.ini.displayname={AVD_NAME}\n"
    f"hw.cpu.arch=arm64\n"
    f"hw.cpu.ncore=4\n"
    f"hw.ramSize=3072\n"
    f"hw.lcd.width=1080\n"
    f"hw.lcd.height=2400\n"
    f"hw.lcd.density=420\n"
    f"hw.keyboard=yes\n"
    f"hw.gpu.enabled=yes\n"
    f"hw.gpu.mode=auto\n"
    f"hw.sdCard=yes\n"
    f"sdcard.size=512M\n"
    f"image.sysdir.1={SYS_IMG}/\n"
    f"tag.id=google_apis\n"
    f"tag.display=Google APIs\n"
    f"hw.device.name=pixel_6\n"
    f"showDeviceFrame=yes\n"
    f"PlayStore.enabled=false\n"
)
print(f"  ✓ AVD '{AVD_NAME}' created (android-33 google_apis)")

# ── Step 2: Start emulator ────────────────────────────────────
print("\n[2] Starting emulator with -writable-system...")
os.makedirs(os.path.join(PROJECT,"logs"), exist_ok=True)
log = open(os.path.join(PROJECT,"logs","emulator.log"),"w")
subprocess.Popen(
    [EMULATOR, "-avd", AVD_NAME, "-writable-system",
     "-no-snapshot-save", "-no-audio", "-gpu", "swiftshader_indirect"],
    stdout=log, stderr=log
)
time.sleep(12)
wait_boot()

# ── Step 3: Root + remount ────────────────────────────────────
print("\n[3] Root + remount...")
o,e,_ = adb("root"); print(f"  root: {o or e}"); time.sleep(5)
o,e,_ = adb("remount"); print(f"  remount: {o or e}")
if "failed" in (o+e).lower():
    print("  Trying disable-verity...")
    adb("disable-verity"); adb("reboot"); time.sleep(15)
    wait_boot(); adb("root"); time.sleep(5)
    o,e,_ = adb("remount"); print(f"  remount: {o or e}")
time.sleep(2)

# ── Step 4: Push MicroG into /system/priv-app/ ───────────────
print("\n[4] Installing MicroG into /system/priv-app/...")
try:
    req = urllib.request.Request("https://api.github.com/repos/microg/GmsCore/releases/latest",headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    gms_url  = next(a["browser_download_url"] for a in data["assets"] if "com.google.android.gms" in a["name"] and a["name"].endswith(".apk"))
    vend_url = next(a["browser_download_url"] for a in data["assets"] if "com.android.vending" in a["name"] and a["name"].endswith(".apk"))
    print(f"  MicroG: {data.get('tag_name')}")
except Exception as e:
    print(f"  Fallback ({e})")
    tag="v0.3.15.250932"; base=f"https://github.com/microg/GmsCore/releases/download/{tag}"
    gms_url=f"{base}/com.google.android.gms-250932030.apk"; vend_url=f"{base}/com.android.vending-84022630.apk"

with tempfile.TemporaryDirectory() as tmp:
    gms=os.path.join(tmp,"GmsCore.apk"); vend=os.path.join(tmp,"FakeStore.apk")
    download(gms_url,gms); download(vend_url,vend)
    for pkg in ["com.google.android.gms","com.android.vending"]:
        o,_,_ = adb("shell",f"pm path {pkg}")
        for line in o.splitlines():
            if "package:" in line:
                d=os.path.dirname(line.split("package:")[-1].strip())
                print(f"  Removing existing: {d}"); sh(f"rm -rf '{d}'")
    for apk,name in [(gms,"GmsCore"),(vend,"FakeStore")]:
        d=f"/system/priv-app/{name}"; sh(f"mkdir -p {d}")
        adb("push",apk,f"{d}/{name}.apk"); sh(f"chmod 644 {d}/{name}.apk"); sh(f"chown root:root {d}/{name}.apk")
        print(f"  ✓ {name} → {d}/")

# ── Step 5: System cert ───────────────────────────────────────
print("\n[5] Installing mitmproxy cert as system cert...")
if not os.path.isfile(CERT_PEM):
    p=subprocess.Popen(["mitmdump","--listen-port","8081"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    time.sleep(5); p.terminate()
r=subprocess.run(["openssl","x509","-inform","PEM","-subject_hash_old","-in",CERT_PEM],capture_output=True,text=True)
ch=r.stdout.strip().splitlines()[0]; cf=f"{ch}.0"
adb("push",CERT_PEM,f"/system/etc/security/cacerts/{cf}")
sh(f"chmod 644 /system/etc/security/cacerts/{cf}")
o,_,_=adb("shell",f"ls /system/etc/security/cacerts/{cf}")
print(f"  {'✓' if cf in o else '✗'} Cert {cf}")

# ── Step 6: Reboot ────────────────────────────────────────────
print("\n[6] Rebooting...")
adb("reboot"); time.sleep(15); wait_boot()
adb("root"); time.sleep(3)
for p in ["android.permission.READ_PHONE_STATE","android.permission.RECEIVE_SMS",
          "android.permission.READ_SMS","android.permission.ACCESS_COARSE_LOCATION","android.permission.GET_ACCOUNTS"]:
    adb("shell","pm","grant","com.google.android.gms",p)
print("  ✓ MicroG permissions granted")

# ── Step 7: Install KukuTV ────────────────────────────────────
print("\n[7] Installing KukuTV...")
apks=sorted([os.path.join(APK_CACHE,f) for f in os.listdir(APK_CACHE) if f.endswith(".apk")]) if os.path.isdir(APK_CACHE) else []
if not apks: print(f"  ERROR: No APKs in {APK_CACHE}"); sys.exit(1)
r2=subprocess.run([ADB,"install-multiple","-r","-d"]+apks,capture_output=True,text=True)
if r2.returncode==0 or "Success" in (r2.stdout+r2.stderr): print("  ✓ KukuTV installed")
else: print(f"  ✗ {(r2.stderr or r2.stdout)[:200]}")

# ── Step 8: Proxy ─────────────────────────────────────────────
print("\n[8] Starting mitmproxy...")
subprocess.run(["pkill","-f","mitmdump"],capture_output=True); time.sleep(1)
os.makedirs(os.path.join(PROJECT,"metadata","captured_apis"),exist_ok=True)
open(os.path.join(PROJECT,"metadata","captured_apis","api_traffic.jsonl"),"w").close()
subprocess.Popen(
    ["mitmdump","-s",os.path.join(PROJECT,"mitm_addons","mitm_addon.py"),"--listen-port","8080","--ssl-insecure"],
    stdout=open(os.path.join(PROJECT,"logs","mitm.log"),"w"), stderr=subprocess.STDOUT
)
time.sleep(3)
adb("shell","settings","put","global","http_proxy","10.0.2.2:8080")
print("  ✓ Proxy → 10.0.2.2:8080")

print(f"""
{'='*55}
  ✓ DONE
{'='*55}
  1. Open KukuTV on the emulator
  2. Log in (phone + OTP) — MicroG handles Play Services
  3. Browse: home → show → play video (2-3 min)
  4. python3 scripts/analyze.py
{'='*55}
""")
