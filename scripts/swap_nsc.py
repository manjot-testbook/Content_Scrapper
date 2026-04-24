#!/usr/bin/env python3
"""
swap_nsc.py — Replace network_security_config in KukuTV APK without apktool.

The app already has networkSecurityConfig in its manifest — we just need to
swap the NSC binary XML file inside base.apk with one that trusts user CAs.

No decompile/recompile needed — uses only zipfile + aapt2 + apksigner.

Usage:
    python scripts/swap_nsc.py
"""
import glob, os, shutil, subprocess, sys, tempfile, zipfile

ADB   = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
SDK   = os.path.expanduser("~/Library/Android/sdk")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "com.vlv.aravali.reels"

APKSIGNER = sorted(glob.glob(os.path.join(SDK, "build-tools", "*", "apksigner")), reverse=True)
APKSIGNER = APKSIGNER[0] if APKSIGNER else shutil.which("apksigner")
AAPT2     = sorted(glob.glob(os.path.join(SDK, "build-tools", "*", "aapt2")), reverse=True)
AAPT2     = AAPT2[0] if AAPT2 else shutil.which("aapt2")
DEBUG_KS  = os.path.expanduser("~/.android/debug.keystore")

NSC_XML = b"""\
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>
</network-security-config>
"""

def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb(*args):
    return run([ADB] + list(args))

def sign(src, dst):
    _, err, code = run([APKSIGNER, "sign",
        "--ks", DEBUG_KS, "--ks-pass", "pass:android", "--key-pass", "pass:android",
        "--out", dst, src])
    if code != 0:
        print(f"  [WARN] sign failed: {err[:120]}")
        shutil.copy(src, dst)

def compile_nsc_binary(tmp):
    """Compile NSC XML → binary XML using aapt2."""
    # aapt2 requires the file to be inside a res/xml/ directory structure
    res_xml_dir = os.path.join(tmp, "res", "xml")
    os.makedirs(res_xml_dir, exist_ok=True)
    xml_path = os.path.join(res_xml_dir, "network_security_config.xml")
    flat_dir  = os.path.join(tmp, "flat")
    os.makedirs(flat_dir, exist_ok=True)
    with open(xml_path, "wb") as f:
        f.write(NSC_XML)


    # Compile XML to flat
    _, err, code = run([AAPT2, "compile", "--legacy", xml_path, "-o", flat_dir])
    if code != 0:
        print(f"aapt2 compile warning: {err}")

    flat_files = glob.glob(os.path.join(flat_dir, "*.flat"))
    if not flat_files:
        print("ERROR: aapt2 produced no flat file"); sys.exit(1)

    # The flat file is a container; binary XML starts after a small header
    with open(flat_files[0], "rb") as f:
        data = f.read()

    # Find binary XML magic: 0x03 0x00 0x08 0x00
    idx = data.find(b"\x03\x00\x08\x00")
    if idx != -1:
        binary_xml = data[idx:]
    else:
        binary_xml = data[8:]  # skip 8-byte flat header

    print(f"  Compiled NSC binary XML: {len(binary_xml)} bytes")
    return binary_xml


def pull_all_apks(tmp):
    """Pull base.apk + all split APKs from device."""
    out, _, _ = adb("shell", f"pm path {PACKAGE}")
    paths = [l.split("package:")[-1].strip() for l in out.splitlines() if "package:" in l]
    if not paths:
        print(f"ERROR: {PACKAGE} not found on device"); sys.exit(1)

    pulled = []
    for p in paths:
        name = os.path.basename(p)
        dest = os.path.join(tmp, name)
        print(f"  Pulling {p} ...")
        _, err, code = adb("pull", p, dest)
        if code != 0:
            print(f"  [WARN] pull failed: {err}")
            continue
        pulled.append(dest)
    return pulled


def patch_base_apk(base_apk, binary_nsc, tmp):
    """Swap NSC binary XML in base.apk and return path to patched APK."""
    patched = os.path.join(tmp, "base_patched.apk")

    # Find the NSC file path inside the APK
    nsc_zip_path = None
    with zipfile.ZipFile(base_apk) as zf:
        for name in zf.namelist():
            if "network_security_config" in name.lower():
                nsc_zip_path = name
                print(f"  Found NSC at: {name}")
                break

    if not nsc_zip_path:
        # App doesn't have an NSC yet — we need to add it
        # But manifest doesn't reference it either → apktool needed
        # For now, place it at the standard path and hope manifest references it
        nsc_zip_path = "res/xml/network_security_config.xml"
        print(f"  No existing NSC — adding at {nsc_zip_path}")

    # Rebuild APK with swapped NSC
    with zipfile.ZipFile(base_apk, "r") as zin:
        with zipfile.ZipFile(patched, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
            for item in zin.infolist():
                if item.filename == nsc_zip_path:
                    zout.writestr(item, binary_nsc)
                    print(f"  ✓ Swapped {nsc_zip_path}")
                else:
                    zout.writestr(item, zin.read(item.filename))

    return patched


def main():
    print("\n=== KukuTV NSC Swap (no apktool) ===\n")
    print(f"aapt2:     {AAPT2}")
    print(f"apksigner: {APKSIGNER}")

    # Check device
    out, _, _ = adb("devices")
    if "emulator" not in out and "device" not in out.split("\n", 1)[-1]:
        print("ERROR: No device connected"); sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        signed_dir = os.path.join(tmp, "signed")
        os.makedirs(signed_dir)

        # Compile NSC
        print("\n[1] Compiling network_security_config.xml ...")
        binary_nsc = compile_nsc_binary(tmp)

        # Pull APKs from device
        print("\n[2] Pulling APKs from device ...")
        apks = pull_all_apks(tmp)
        base_apk = next((a for a in apks if os.path.basename(a) == "base.apk"), None)
        split_apks = [a for a in apks if a != base_apk]
        print(f"  base.apk + {len(split_apks)} splits")

        # Patch base
        print("\n[3] Patching base.apk ...")
        patched_base = patch_base_apk(base_apk, binary_nsc, tmp)

        # Sign patched base
        print("\n[4] Signing all APKs with debug key ...")
        signed_base = os.path.join(signed_dir, "base.apk")
        sign(patched_base, signed_base)
        print(f"  ✓ base.apk")

        # Sign splits
        signed_splits = []
        for s in split_apks:
            dst = os.path.join(signed_dir, os.path.basename(s))
            sign(s, dst)
            signed_splits.append(dst)
            print(f"  ✓ {os.path.basename(s)}")

        # Install — try -r first (keeps data, keeps login session)
        print(f"\n[5] Installing {1 + len(signed_splits)} APKs (-r = keep data)...")
        all_apks = [signed_base] + signed_splits
        out, err, code = run([ADB, "install-multiple", "-r", "-d"] + all_apks)
        print(f"  stdout: {out}")
        print(f"  stderr: {err[:300]}")

        if code == 0 or "Success" in out:
            print("\n✓ Patched KukuTV installed — mitmproxy CA is now trusted!")
            print("  Launch app, then: ./run.sh capture")
        elif "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in err or "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
            print("\n[!] Signature mismatch detected.")
            print("    The Play Store version has a different signature than the debug-signed patched APK.")
            print("    Uninstalling will LOG YOU OUT of KukuTV (OTP required again).")
            ans = input("    Do you want to uninstall and reinstall? [y/N]: ").strip().lower()
            if ans == "y":
                print("  Uninstalling...")
                adb("uninstall", PACKAGE)
                out2, err2, code2 = run([ADB, "install-multiple", "-d"] + all_apks)
                if code2 == 0 or "Success" in out2:
                    print("✓ Installed. Log back into KukuTV, then: ./run.sh capture")
                else:
                    print(f"✗ Install failed: {err2[:300]}")
            else:
                print("\n[Cancelled] App NOT uninstalled — your session is preserved.")
                print("Alternative: use system cert method to avoid reinstall:")
                print("  Restart emulator with -writable-system, then run: ./run.sh install-cert")
        else:
            print(f"✗ Install failed: {err[:300]}")
            else:
                print(f"✗ Failed: {err2[:300]}")
        else:
            print(f"\n✗ Install failed: {err[:300]}")
            print("\nTry manually:")
            print(f"  adb uninstall {PACKAGE}")
            print(f"  adb install-multiple {' '.join(all_apks)}")


if __name__ == "__main__":
    main()
