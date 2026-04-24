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


def adb(*args):
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

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


APKTOOL_JAR_PATHS = [
    "/tmp/apktool.jar",
    os.path.expanduser("~/apktool.jar"),
    os.path.join(PROJECT, "tools", "apktool.jar"),
]


def find_apktool() -> list | None:
    """Return the command to invoke apktool (binary or java -jar)."""
    binary = shutil.which("apktool")
    if binary:
        return [binary]
    # Try java -jar
    for jar in APKTOOL_JAR_PATHS:
        if os.path.isfile(jar):
            java = shutil.which("java")
            if java:
                # Quick validation
                r = subprocess.run([java, "-jar", jar, "--version"], capture_output=True, text=True)
                if r.returncode == 0:
                    console.print(f"[dim]Using apktool jar: {jar}[/dim]")
                    return [java, "-jar", jar]
    return None


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
    apktool_cmd = find_apktool()
    if not apktool_cmd:
        console.print("[red]apktool not found.[/red]")
        console.print("  Download: curl -L https://github.com/iBotPeaches/Apktool/releases/download/v2.9.3/apktool_2.9.3.jar -o /tmp/apktool.jar")
        console.print("  Or install: brew install apktool")
        return False

    with tempfile.TemporaryDirectory() as work_dir:
        decoded_dir = os.path.join(work_dir, "decoded")

        # Decompile
        console.print("  Decompiling APK with apktool...")
        out, err, code = run(apktool_cmd + ["d", base_apk, "-o", decoded_dir, "--force"], cwd=work_dir)
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
        out, err, code = run(apktool_cmd + ["b", decoded_dir, "-o", unsigned_apk], cwd=work_dir)
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


def install_patched(patched_apk: str, package: str, apkm_path: str = None):
    """Install the patched base.apk together with all original split APKs."""
    console.print("\n[cyan]Installing patched APK (split install)...[/cyan]")

    split_apks = []

    # If we have the APKM, extract splits to a temp dir and use them
    if apkm_path and os.path.isfile(apkm_path):
        import tempfile, zipfile as _zf
        _tmp = tempfile.mkdtemp()
        with _zf.ZipFile(apkm_path) as zf:
            for name in zf.namelist():
                if name.endswith(".apk") and name != "base.apk":
                    zf.extract(name, _tmp)
                    split_apks.append(os.path.join(_tmp, name))
        console.print(f"  Using {len(split_apks)} split APKs from APKM")

    # Build install command: patched base + all splits
    all_apks = [patched_apk] + split_apks
    cmd = [ADB, "install-multiple", "-r", "-d"] + all_apks
    console.print(f"  Installing {len(all_apks)} APKs total...")
    out, err, code = run(cmd)

    if code == 0 or "Success" in out:
        console.print("[green bold]✓ Patched app installed![/green bold]")
        console.print("[yellow]Note: Signed with debug key. If signature mismatch, uninstall first:[/yellow]")
        console.print(f"  adb uninstall {package}")
    else:
        console.print(f"[red]Install failed:[/red] {err}")
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in err or "INSTALL_FAILED_INVALID_APK" in err:
            console.print("\n[yellow]Trying after uninstall (will clear app data)...[/yellow]")
            adb("uninstall", package)
            out2, err2, code2 = run(cmd)
            if code2 == 0 or "Success" in out2:
                console.print("[green bold]✓ Installed after uninstall![/green bold]")
                console.print("[yellow]⚠ App data cleared — you'll need to log in again.[/yellow]")
            else:
                console.print(f"[red]Still failed: {err2}[/red]")


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
        install_patched(output_apk, args.package, apkm_path=apkm_path)

    console.print("\n[bold green]Done![/bold green]")
    console.print("Now run: [cyan]./run.sh capture[/cyan]")
    console.print("[dim]mitmproxy will now be trusted by the patched app.[/dim]")


if __name__ == "__main__":
    main()
