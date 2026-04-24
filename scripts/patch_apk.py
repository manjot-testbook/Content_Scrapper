#!/usr/bin/env python3
"""
patch_apk.py — Patch KukuTV APK to trust user-installed CAs (including mitmproxy).

This is the NO-ROOT solution for Android 7+ HTTPS interception.
Adds a network_security_config.xml that allows user CA trust + cleartext,
then re-signs the APK with a debug key.

Requirements:
    brew install apktool
    pip install rich
    Java must be installed (for apktool)

Usage:
    python scripts/patch_apk.py
    python scripts/patch_apk.py --apkm apkm/com.vlv.aravali.reels_*.apkm
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

from rich.console import Console

console = Console()

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
BUILD_TOOLS = sorted(glob.glob(os.path.expanduser("~/Library/Android/sdk/build-tools/*/apksigner")), reverse=True)
APKSIGNER = BUILD_TOOLS[0] if BUILD_TOOLS else "apksigner"
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NETWORK_SECURITY_CONFIG = """\
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <!-- Trust system CAs -->
            <certificates src="system" />
            <!-- Trust user-installed CAs (includes mitmproxy) -->
            <certificates src="user" />
        </trust-anchors>
    </base-config>
    <!-- Override for KukuTV API domains -->
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">kukufm.com</domain>
        <domain includeSubdomains="true">kuku.fm</domain>
        <domain includeSubdomains="true">kukutv.com</domain>
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </domain-config>
</network-security-config>
"""


def check_tool(name: str) -> str | None:
    result = subprocess.run(["which", name], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def run(cmd: list, cwd=None) -> tuple[str, str, int]:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.stdout, r.stderr, r.returncode


def adb(*args) -> tuple[str, str, int]:
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def extract_base_apk(apkm_path: str, dest_dir: str) -> str:
    """Extract base.apk from APKM archive."""
    with zipfile.ZipFile(apkm_path, "r") as zf:
        if "base.apk" in zf.namelist():
            zf.extract("base.apk", dest_dir)
            return os.path.join(dest_dir, "base.apk")
        # Try pulling from device instead
    return None


def pull_apk_from_device(package: str, dest: str) -> str | None:
    """Pull the installed APK from the device."""
    out, err, code = adb("shell", f"pm path {package}")
    if code != 0:
        return None
    # pm path returns: package:/data/app/.../base.apk
    paths = [line.split("package:", 1)[-1].strip() for line in out.splitlines() if "package:" in line]
    if not paths:
        return None
    # Pull base.apk (first entry)
    base_path = next((p for p in paths if "base.apk" in p), paths[0])
    console.print(f"  Pulling {base_path} from device...")
    out2, err2, code2 = adb("pull", base_path, dest)
    if code2 == 0:
        pulled = os.path.join(dest, os.path.basename(base_path))
        return pulled
    console.print(f"[red]Pull failed: {err2}[/red]")
    return None


def patch_with_apktool(base_apk: str, output_apk: str) -> bool:
    """Decompile APK, inject network_security_config, recompile."""
    apktool = check_tool("apktool")
    if not apktool:
        console.print("[red]apktool not found. Install with: brew install apktool[/red]")
        return False

    with tempfile.TemporaryDirectory() as work_dir:
        decoded_dir = os.path.join(work_dir, "decoded")

        # Decompile
        console.print("  Decompiling APK with apktool...")
        out, err, code = run([apktool, "d", base_apk, "-o", decoded_dir, "--force"], cwd=work_dir)
        if code != 0:
            console.print(f"[red]apktool decompile failed:\n{err}[/red]")
            return False

        # Write network_security_config.xml
        nsc_dir = os.path.join(decoded_dir, "res", "xml")
        os.makedirs(nsc_dir, exist_ok=True)
        nsc_path = os.path.join(nsc_dir, "network_security_config.xml")
        with open(nsc_path, "w") as f:
            f.write(NETWORK_SECURITY_CONFIG)
        console.print("  Injected network_security_config.xml")

        # Update AndroidManifest.xml to reference it
        manifest_path = os.path.join(decoded_dir, "AndroidManifest.xml")
        with open(manifest_path, "r") as f:
            manifest = f.read()

        if "networkSecurityConfig" not in manifest:
            manifest = manifest.replace(
                "<application",
                '<application android:networkSecurityConfig="@xml/network_security_config"',
                1
            )
            with open(manifest_path, "w") as f:
                f.write(manifest)
            console.print("  Patched AndroidManifest.xml")
        else:
            console.print("  AndroidManifest.xml already has networkSecurityConfig — overwriting value")
            import re
            manifest = re.sub(
                r'android:networkSecurityConfig="[^"]*"',
                'android:networkSecurityConfig="@xml/network_security_config"',
                manifest
            )
            with open(manifest_path, "w") as f:
                f.write(manifest)

        # Recompile
        console.print("  Recompiling...")
        unsigned_apk = os.path.join(work_dir, "unsigned.apk")
        out, err, code = run([apktool, "b", decoded_dir, "-o", unsigned_apk, "--use-aapt2"], cwd=work_dir)
        if code != 0:
            console.print(f"[red]apktool build failed:\n{err[-500:]}[/red]")
            return False

        # Sign with debug key
        console.print("  Signing with debug key...")
        debug_ks = os.path.expanduser("~/.android/debug.keystore")
        if not os.path.isfile(debug_ks):
            # Generate debug keystore
            subprocess.run([
                "keytool", "-genkeypair", "-v",
                "-keystore", debug_ks,
                "-alias", "androiddebugkey",
                "-keyalg", "RSA", "-keysize", "2048",
                "-validity", "10000",
                "-dname", "CN=Android Debug,O=Android,C=US",
                "-storepass", "android", "-keypass", "android"
            ], capture_output=True)

        signed_apk = output_apk
        sign_out, sign_err, sign_code = run([
            APKSIGNER, "sign",
            "--ks", debug_ks,
            "--ks-pass", "pass:android",
            "--key-pass", "pass:android",
            "--out", signed_apk,
            unsigned_apk
        ])
        if sign_code != 0:
            # Try jarsigner fallback
            console.print(f"[yellow]apksigner failed, trying jarsigner...[/yellow]")
            shutil.copy(unsigned_apk, signed_apk)
            run(["jarsigner", "-verbose", "-sigalg", "SHA1withRSA", "-digestalg", "SHA1",
                 "-keystore", debug_ks, "-storepass", "android",
                 signed_apk, "androiddebugkey"])

        if os.path.isfile(signed_apk):
            size_mb = os.path.getsize(signed_apk) / (1024 * 1024)
            console.print(f"[green]✓ Patched APK: {signed_apk} ({size_mb:.1f} MB)[/green]")
            return True

    return False


def install_patched(patched_apk: str, package: str):
    """Install the patched APK over the existing installation."""
    console.print("\n[cyan]Installing patched APK...[/cyan]")
    out, err, code = adb("install", "-r", "-d", "--bypass-low-target-sdk-block", patched_apk)
    if code != 0:
        # Try without bypass flag (older adb)
        out, err, code = adb("install", "-r", "-d", patched_apk)
    if code == 0 or "Success" in out:
        console.print("[green bold]✓ Patched app installed![/green bold]")
        console.print("[yellow]Note: App is signed with debug key — must uninstall first if signature mismatch.[/yellow]")
    else:
        console.print(f"[red]Install failed: {err}[/red]")
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in err:
            console.print("\n[yellow]Signature mismatch — uninstall first:[/yellow]")
            console.print(f"  adb uninstall {package}")
            console.print(f"  adb install {patched_apk}")
            console.print("[red]Warning: Uninstalling will delete app data (login, cache)[/red]")


def main():
    parser = argparse.ArgumentParser(description="Patch KukuTV APK to trust user CAs")
    parser.add_argument("--apkm", default=None, help="APKM file to extract base.apk from")
    parser.add_argument("--package", default="com.vlv.aravali.reels", help="App package name")
    parser.add_argument("--output", default=None, help="Output patched APK path")
    parser.add_argument("--no-install", action="store_true", help="Only patch, don't install")
    args = parser.parse_args()

    console.print("\n[bold]KukuTV APK Patcher — Trust User CAs[/bold]\n")

    # Find APKM
    apkm_path = args.apkm
    if not apkm_path:
        matches = glob.glob(os.path.join(PROJECT, "apkm", "*.apkm"))
        if matches:
            apkm_path = matches[0]

    output_apk = args.output or os.path.join(PROJECT, "apkm", f"{args.package}_patched.apk")
    os.makedirs(os.path.dirname(output_apk), exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        # Get base.apk
        base_apk = None

        if apkm_path and os.path.isfile(apkm_path):
            console.print(f"[cyan]Extracting base.apk from APKM:[/cyan] {apkm_path}")
            base_apk = extract_base_apk(apkm_path, tmp)

        if not base_apk:
            console.print(f"[cyan]Pulling base.apk from device ({args.package})...[/cyan]")
            base_apk = pull_apk_from_device(args.package, tmp)

        if not base_apk:
            console.print("[red]Could not get base.apk. Check device connection or APKM path.[/red]")
            sys.exit(1)

        console.print(f"[green]✓ Got base.apk[/green] ({os.path.getsize(base_apk) / 1024 / 1024:.1f} MB)")

        # Patch
        console.print("\n[cyan]Patching APK...[/cyan]")
        success = patch_with_apktool(base_apk, output_apk)

        if not success:
            console.print("[red]Patching failed.[/red]")
            sys.exit(1)

    if not args.no_install:
        install_patched(output_apk, args.package)

    console.print("\n[bold green]Done![/bold green]")
    console.print("Now run: [cyan]./run.sh capture[/cyan]")
    console.print("[dim]mitmproxy will now be trusted by the patched app.[/dim]")


if __name__ == "__main__":
    main()
