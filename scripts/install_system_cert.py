#!/usr/bin/env python3
"""
install_system_cert.py — Install mitmproxy CA cert as Android SYSTEM certificate.

Works on:
  - Emulators started with: emulator -avd <name> -writable-system
  - Rooted physical devices
  - google_apis (non-playstore) emulators via adb root

Android 7+ ignores user-installed CAs for apps — system cert is required.

Usage:
    python scripts/install_system_cert.py
    python scripts/install_system_cert.py --cert ~/.mitmproxy/mitmproxy-ca-cert.pem
    python scripts/install_system_cert.py --check   # just check if cert is installed
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from rich.console import Console

console = Console()

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
EMULATOR = os.path.expanduser("~/Library/Android/sdk/emulator/emulator")
DEFAULT_CERT = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")


def run(cmd: list, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def adb(*args) -> tuple[str, str, int]:
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def get_cert_hash(cert_pem: str) -> str:
    """Get the Android-expected hash filename for a PEM cert."""
    r = subprocess.run(
        ["openssl", "x509", "-inform", "PEM", "-subject_hash_old", "-in", cert_pem],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        console.print(f"[red]openssl error: {r.stderr}[/red]")
        sys.exit(1)
    return r.stdout.strip().splitlines()[0]


def check_root() -> bool:
    """Return True if adb root is available."""
    out, err, code = adb("root")
    if "adbd is already running as root" in out or "restarting adbd as root" in out:
        return True
    if "cannot run as root in production builds" in out or "cannot run as root" in out:
        return False
    # Try whoami
    out2, _, _ = adb("shell", "whoami")
    return out2.strip() == "root"


def check_cert_installed(cert_hash: str) -> bool:
    """Check if cert is already in system store."""
    out, _, _ = adb("shell", f"ls /system/etc/security/cacerts/{cert_hash}.0 2>/dev/null")
    return bool(out.strip())


def install_system_cert_root(cert_pem: str, cert_hash: str):
    """Install cert as system CA on a rootable emulator."""
    console.print("[cyan]Installing mitmproxy CA as system certificate...[/cyan]")

    with tempfile.TemporaryDirectory() as tmp:
        cert_file = os.path.join(tmp, f"{cert_hash}.0")
        shutil.copy(cert_pem, cert_file)

        # Push to sdcard first
        out, err, code = adb("push", cert_file, f"/sdcard/{cert_hash}.0")
        if code != 0:
            console.print(f"[red]Push failed: {err}[/red]")
            sys.exit(1)

    # Remount system partition as writable
    out, err, code = adb("shell", "mount -o remount,rw /system")
    if code != 0:
        # Try via su
        out, err, code = adb("shell", "su -c 'mount -o remount,rw /system'")
    if code != 0:
        console.print(f"[yellow]Warning: remount may have failed: {err}[/yellow]")

    # Copy cert to system store
    cp_cmd = f"cp /sdcard/{cert_hash}.0 /system/etc/security/cacerts/{cert_hash}.0"
    chmod_cmd = f"chmod 644 /system/etc/security/cacerts/{cert_hash}.0"

    out, err, code = adb("shell", cp_cmd)
    if code != 0:
        # Try via su (for rooted devices where adb isn't root but su is available)
        out, err, code = adb("shell", f"su -c '{cp_cmd}'")
    if code != 0:
        console.print(f"[red]Copy failed: {err}[/red]")
        console.print("[yellow]Tip: Start emulator with -writable-system flag[/yellow]")
        sys.exit(1)

    adb("shell", chmod_cmd)

    # Verify
    if check_cert_installed(cert_hash):
        console.print(f"[green bold]✓ System cert installed: /system/etc/security/cacerts/{cert_hash}.0[/green bold]")
        console.print("[cyan]Rebooting emulator to apply cert...[/cyan]")
        adb("reboot")
        console.print("[green]✓ Rebooting — wait ~30s then re-run capture.[/green]")
    else:
        console.print("[red]Cert not found after install — something went wrong.[/red]")
        sys.exit(1)


def install_via_magisk_module(cert_pem: str, cert_hash: str):
    """
    Magisk-based system cert injection — works on Magisk-rooted devices.
    Creates a Magisk module that overlays /system/etc/security/cacerts/
    """
    console.print("[cyan]Trying Magisk module method...[/cyan]")
    with tempfile.TemporaryDirectory() as tmp:
        module_dir = os.path.join(tmp, "mitm_cert_module")
        cacerts_dir = os.path.join(module_dir, "system", "etc", "security", "cacerts")
        os.makedirs(cacerts_dir)

        # Create Magisk module structure
        with open(os.path.join(module_dir, "module.prop"), "w") as f:
            f.write("id=mitmproxy_ca\nname=mitmproxy CA Cert\nversion=v1\nversionCode=1\n"
                    "author=scraper\ndescription=mitmproxy CA for HTTPS interception\n")

        shutil.copy(cert_pem, os.path.join(cacerts_dir, f"{cert_hash}.0"))
        os.chmod(os.path.join(cacerts_dir, f"{cert_hash}.0"), 0o644)

        # Zip the module
        module_zip = os.path.join(tmp, "mitm_module.zip")
        shutil.make_archive(module_zip.replace(".zip", ""), "zip", module_dir)

        # Push and install via Magisk
        adb("push", module_zip, "/sdcard/mitm_module.zip")
        out, err, code = adb("shell", "su -c 'magisk --install-module /sdcard/mitm_module.zip'")
        if code == 0:
            console.print("[green]✓ Magisk module installed. Reboot to apply.[/green]")
            adb("reboot")
        else:
            console.print(f"[yellow]Magisk method failed: {err}[/yellow]")


def main():
    parser = argparse.ArgumentParser(description="Install mitmproxy CA as Android system certificate")
    parser.add_argument("--cert", default=DEFAULT_CERT, help=f"Path to CA PEM (default: {DEFAULT_CERT})")
    parser.add_argument("--check", action="store_true", help="Only check if cert is installed")
    parser.add_argument("--no-reboot", action="store_true", help="Skip reboot after install")
    args = parser.parse_args()

    cert_pem = os.path.expanduser(args.cert)
    if not os.path.isfile(cert_pem):
        console.print(f"[red]Cert not found: {cert_pem}[/red]")
        console.print("Run mitmproxy once to generate it, or specify --cert path")
        sys.exit(1)

    cert_hash = get_cert_hash(cert_pem)
    console.print(f"[cyan]Cert hash:[/cyan] {cert_hash}")
    console.print(f"[cyan]Cert file:[/cyan] {cert_pem}")

    # Check device
    out, _, _ = adb("devices")
    if "device" not in out.split("\n", 1)[-1]:
        console.print("[red]No device connected.[/red]")
        sys.exit(1)

    # Check Android version
    ver, _, _ = adb("shell", "getprop ro.build.version.sdk")
    build_type, _, _ = adb("shell", "getprop ro.build.type")
    console.print(f"[cyan]Android SDK:[/cyan] {ver.strip()}  [cyan]Build type:[/cyan] {build_type.strip()}")

    if args.check:
        if check_cert_installed(cert_hash):
            console.print(f"[green]✓ Cert IS installed as system CA[/green]")
        else:
            console.print(f"[red]✗ Cert NOT in system CA store[/red]")
            console.print("  Run without --check to install it.")
        return

    if check_cert_installed(cert_hash):
        console.print("[green]✓ Cert already installed as system CA — nothing to do.[/green]")
        return

    # Try to get root
    is_root = check_root()
    console.print(f"[cyan]ADB root:[/cyan] {'✓ yes' if is_root else '✗ no (production build)'}")

    if not is_root:
        console.print("\n[red bold]Cannot install system cert — emulator is a production build (google_apis_playstore).[/red bold]")
        console.print("\n[yellow]Solutions:[/yellow]")
        console.print("  [bold]Option 1 (Recommended):[/bold] Use KukuTV_Root AVD instead")
        console.print("    ./run.sh stop-emulator")
        console.print("    ./run.sh emulator --avd KukuTV_Root")
        console.print("    ./run.sh install-cert")
        console.print("    ./run.sh install")
        console.print("\n  [bold]Option 2:[/bold] Restart current emulator with -writable-system")
        console.print("    Kill the emulator, then run:")
        console.print("    ~/Library/Android/sdk/emulator/emulator -avd Medium_Phone_API_36.1 -writable-system &")
        console.print("    Then: ./run.sh install-cert")
        console.print("\n  [bold]Option 3:[/bold] Patch APK with network_security_config (no root needed)")
        console.print("    python scripts/patch_apk.py")
        sys.exit(1)

    install_system_cert_root(cert_pem, cert_hash)


if __name__ == "__main__":
    main()
