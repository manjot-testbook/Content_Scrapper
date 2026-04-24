#!/usr/bin/env python3
"""
patch_apk_noroot.py — Patch KukuTV APK to trust mitmproxy CA cert.
NO ROOT REQUIRED. Uses only Python zipfile + aapt2 (from Android SDK).

This patches base.apk to add a network_security_config.xml that trusts
user-installed CA certificates, bypassing Android 7+ restrictions.

Usage:
    python scripts/patch_apk_noroot.py
"""

import glob
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from rich.console import Console

console = Console()

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
SDK_ROOT = os.path.expanduser("~/Library/Android/sdk")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "com.vlv.aravali.reels"

# Find aapt2 and apksigner in SDK
def find_sdk_tool(name):
    pattern = os.path.join(SDK_ROOT, "build-tools", "*", name)
    matches = sorted(glob.glob(pattern), reverse=True)
    return matches[0] if matches else shutil.which(name)

AAPT2 = find_sdk_tool("aapt2")
APKSIGNER = find_sdk_tool("apksigner")

NETWORK_SECURITY_CONFIG_XML = b"""\
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


def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def pull_base_apk(dest_dir: str) -> str:
    """Pull base.apk from connected device."""
    console.print(f"[cyan]Pulling base.apk from device ({PACKAGE})...[/cyan]")
    out, _, _ = adb("shell", f"pm path {PACKAGE}")
    paths = [l.split("package:")[-1].strip() for l in out.splitlines() if "package:" in l]
    base = next((p for p in paths if "base.apk" in p), paths[0] if paths else None)
    if not base:
        console.print("[red]App not found on device.[/red]")
        sys.exit(1)
    dest = os.path.join(dest_dir, "base.apk")
    _, err, code = adb("pull", base, dest)
    if code != 0:
        console.print(f"[red]Pull failed: {err}[/red]")
        sys.exit(1)
    console.print(f"[green]✓ Pulled base.apk ({os.path.getsize(dest)//1024//1024} MB)[/green]")
    return dest


def compile_nsc_binary(xml_bytes: bytes, tmp_dir: str) -> bytes:
    """Compile the NSC XML using aapt2 into binary XML format."""
    if not AAPT2:
        console.print("[red]aapt2 not found in Android SDK.[/red]")
        sys.exit(1)

    # Write XML
    xml_path = os.path.join(tmp_dir, "network_security_config.xml")
    with open(xml_path, "wb") as f:
        f.write(xml_bytes)

    # Compile via aapt2
    flat_dir = os.path.join(tmp_dir, "compiled")
    os.makedirs(flat_dir, exist_ok=True)
    r = subprocess.run(
        [AAPT2, "compile", "--legacy", xml_path, "-o", flat_dir],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        console.print(f"[yellow]aapt2 compile warning: {r.stderr}[/yellow]")

    # The compiled flat file
    flat_files = glob.glob(os.path.join(flat_dir, "*.flat"))
    if flat_files:
        # Extract binary XML from the flat container (skip 4-byte header)
        with open(flat_files[0], "rb") as f:
            data = f.read()
        # Find the binary XML marker (0x00080003)
        idx = data.find(b'\x03\x00\x08\x00')
        if idx != -1:
            return data[idx:]
        return data[8:]  # skip flat header

    # Fallback: return raw XML (won't work as binary but try)
    return xml_bytes


def patch_apk(input_apk: str, output_apk: str):
    """Patch the APK to trust user CAs."""
    console.print(f"\n[cyan]Patching APK...[/cyan]")

    with tempfile.TemporaryDirectory() as tmp:
        # Read existing APK
        with zipfile.ZipFile(input_apk, "r") as zin:
            names = zin.namelist()

            # Find AndroidManifest.xml (binary XML)
            manifest_data = zin.read("AndroidManifest.xml")

            # Check if networkSecurityConfig already referenced
            nsc_ref_exists = b'network_security_config' in manifest_data

            # Write patched APK
            with zipfile.ZipFile(output_apk, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                for item in names:
                    data = zin.read(item)

                    if item == "AndroidManifest.xml" and not nsc_ref_exists:
                        # We need to inject networkSecurityConfig attribute into <application>
                        # In binary XML, find the application tag and inject the attribute
                        # This is complex — use a simpler approach: patch via aapt2 link
                        console.print(f"  [dim]Keeping original manifest (will use aapt2 link)[/dim]")

                    zout.writestr(item, data)

                # Add network_security_config.xml as text (aapt2 will compile it)
                nsc_xml = NETWORK_SECURITY_CONFIG_XML
                nsc_path = "res/xml/network_security_config.xml"
                if nsc_path not in names:
                    zout.writestr(nsc_path, nsc_xml)
                    console.print(f"  [green]Added {nsc_path}[/green]")

    console.print(f"[dim]Note: For full patching, apktool is recommended.[/dim]")


def patch_with_aapt2_link(base_apk: str, output_apk: str, tmp_dir: str) -> bool:
    """
    Use aapt2's link command to rebuild APK with our resources overlaid.
    This is the aapt2-native approach that doesn't need apktool.
    """
    if not AAPT2:
        return False

    console.print("\n[cyan]Attempting aapt2-based patch...[/cyan]")

    android_jar = sorted(glob.glob(os.path.join(SDK_ROOT, "platforms", "android-*", "android.jar")), reverse=True)
    if not android_jar:
        console.print("[red]android.jar not found in SDK platforms.[/red]")
        return False
    android_jar = android_jar[0]

    # Extract APK
    extract_dir = os.path.join(tmp_dir, "extracted")
    os.makedirs(extract_dir)
    with zipfile.ZipFile(base_apk) as zf:
        zf.extractall(extract_dir)

    # Write network_security_config.xml
    nsc_dir = os.path.join(extract_dir, "res", "xml")
    os.makedirs(nsc_dir, exist_ok=True)
    nsc_path = os.path.join(nsc_dir, "network_security_config.xml")
    with open(nsc_path, "wb") as f:
        f.write(NETWORK_SECURITY_CONFIG_XML)

    # Patch manifest (binary XML) — inject networkSecurityConfig reference
    manifest_path = os.path.join(extract_dir, "AndroidManifest.xml")
    with open(manifest_path, "rb") as f:
        manifest = bytearray(f.read())

    # If nsc not already referenced, we need apktool. Fall back.
    if b'network_security_config' not in bytes(manifest):
        console.print("[yellow]Binary manifest patching requires apktool. Trying alternative...[/yellow]")
        return False

    # Repack APK
    repacked = os.path.join(tmp_dir, "repacked.apk")
    with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as zout:
        for root, dirs, files in os.walk(extract_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, extract_dir)
                zout.write(fpath, arcname)

    shutil.copy(repacked, output_apk)
    return True


def sign_apk(apk_path: str) -> str:
    """Sign APK with debug keystore."""
    debug_ks = os.path.expanduser("~/.android/debug.keystore")

    # Generate debug keystore if missing
    if not os.path.isfile(debug_ks):
        console.print("[cyan]Generating debug keystore...[/cyan]")
        subprocess.run([
            "keytool", "-genkeypair", "-v",
            "-keystore", debug_ks, "-alias", "androiddebugkey",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=Android Debug,O=Android,C=US",
            "-storepass", "android", "-keypass", "android"
        ], capture_output=True)

    signed = apk_path.replace(".apk", "_signed.apk")

    if APKSIGNER:
        r = subprocess.run([
            APKSIGNER, "sign",
            "--ks", debug_ks, "--ks-pass", "pass:android", "--key-pass", "pass:android",
            "--out", signed, apk_path
        ], capture_output=True, text=True)
        if r.returncode == 0:
            console.print(f"[green]✓ Signed: {signed}[/green]")
            return signed
        console.print(f"[yellow]apksigner error: {r.stderr[:200]}[/yellow]")

    # Fallback: jarsigner
    shutil.copy(apk_path, signed)
    r = subprocess.run([
        "jarsigner", "-sigalg", "SHA1withRSA", "-digestalg", "SHA1",
        "-keystore", debug_ks, "-storepass", "android", signed, "androiddebugkey"
    ], capture_output=True, text=True)
    if r.returncode == 0:
        console.print(f"[green]✓ Signed with jarsigner: {signed}[/green]")
        return signed

    console.print("[yellow]Signing failed — installing unsigned (may fail)[/yellow]")
    return apk_path


def install_apk(apk_path: str):
    """Install APK on device."""
    console.print(f"\n[cyan]Installing: {apk_path}[/cyan]")
    out, err, code = adb("install", "-r", "-d", apk_path)
    if code == 0 or "Success" in out:
        console.print("[green bold]✓ Installed![/green bold]")
    else:
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in err:
            console.print("[red]Signature mismatch. Uninstalling first...[/red]")
            adb("uninstall", PACKAGE)
            console.print("[yellow]⚠ App data cleared. You'll need to log in again.[/yellow]")
            out2, err2, code2 = adb("install", apk_path)
            if code2 == 0 or "Success" in out2:
                console.print("[green bold]✓ Installed after uninstall![/green bold]")
                return
        console.print(f"[red]Install failed: {err}[/red]")
        console.print(f"\n[yellow]Manual install:[/yellow] adb install -r -d {apk_path}")


def main():
    console.print("\n[bold]KukuTV APK Patcher (no-root) — Trust mitmproxy CA[/bold]")
    console.print(f"[dim]aapt2: {AAPT2}[/dim]")
    console.print(f"[dim]apksigner: {APKSIGNER}[/dim]\n")

    # Check for apktool first (preferred)
    apktool = shutil.which("apktool")
    if apktool:
        console.print("[green]apktool found — using full patch mode[/green]")
        os.execv(sys.executable, [sys.executable,
            os.path.join(PROJECT, "scripts", "patch_apk.py")] + sys.argv[1:])
        return

    console.print("[yellow]apktool not found — using zip-only patch (limited)[/yellow]")
    console.print("[dim]For best results: brew install apktool[/dim]\n")

    with tempfile.TemporaryDirectory() as tmp:
        base_apk = pull_base_apk(tmp)
        output = os.path.join(PROJECT, "apkm", f"{PACKAGE}_patched.apk")
        os.makedirs(os.path.dirname(output), exist_ok=True)

        success = patch_with_aapt2_link(base_apk, output, tmp)
        if not success:
            console.print("\n[red bold]Automatic APK patching requires apktool.[/red bold]")
            console.print("\n[yellow]Install it now:[/yellow]")
            console.print("  brew install apktool")
            console.print("  python scripts/patch_apk.py")
            console.print("\n[yellow]OR use Frida-based bypass (no APK repackaging):[/yellow]")
            console.print("  pip install objection")
            console.print("  objection patchapk -s apkm/base.apk")
            sys.exit(1)

        signed = sign_apk(output)
        install_apk(signed)

        console.print("\n[bold green]✓ Done! Now run:[/bold green]")
        console.print("  ./run.sh capture")


if __name__ == "__main__":
    main()
