"""
install_apkm.py — Extract and install an APKM (split APK bundle) onto a connected device/emulator.

APKM files are ZIP archives containing multiple .apk files (base + splits).
This script unzips them and uses `adb install-multiple` to install.
"""

import argparse
import glob
import os
import subprocess
import sys
import tempfile
import zipfile

from rich.console import Console

console = Console()

SDK_ROOT = os.path.expanduser("~/Library/Android/sdk")


def find_adb() -> str:
    """Locate adb binary."""
    candidates = [
        os.path.join(SDK_ROOT, "platform-tools", "adb"),
        "/usr/local/bin/adb",
    ]
    result = subprocess.run(["which", "adb"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    for path in candidates:
        if os.path.isfile(path):
            return path
    console.print("[red]adb not found. Install Android SDK platform-tools.[/red]")
    sys.exit(1)


def find_aapt2() -> str | None:
    """Locate aapt2 — checks $PATH first, then Android SDK build-tools."""
    result = subprocess.run(["which", "aapt2"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    # Search build-tools (newest version first)
    pattern = os.path.join(SDK_ROOT, "build-tools", "*", "aapt2")
    matches = sorted(glob.glob(pattern), reverse=True)
    if matches:
        console.print(f"[dim]Found aapt2: {matches[0]}[/dim]")
        return matches[0]
    return None


def check_device(adb: str) -> str:
    """Ensure a device/emulator is connected. Returns device serial."""
    result = subprocess.run([adb, "devices"], capture_output=True, text=True)
    lines = [l for l in result.stdout.strip().splitlines()[1:] if l.strip() and "device" in l]
    if not lines:
        console.print("[red]No device/emulator connected. Start one first.[/red]")
        sys.exit(1)
    serial = lines[0].split()[0]
    console.print(f"[green]Using device:[/green] {serial}")
    return serial


def extract_apkm(apkm_path: str, dest_dir: str) -> list[str]:
    """Extract APK files from APKM archive."""
    apk_files = []
    with zipfile.ZipFile(apkm_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".apk"):
                zf.extract(name, dest_dir)
                apk_files.append(os.path.join(dest_dir, name))
                console.print(f"  Extracted: {name}")
    if not apk_files:
        console.print("[red]No .apk files found in APKM archive.[/red]")
        sys.exit(1)
    return apk_files


def install_apks(adb: str, serial: str, apk_files: list[str]) -> None:
    """Install split APKs using adb install-multiple."""
    cmd = [adb, "-s", serial, "install-multiple", "-r"] + apk_files
    console.print(f"\n[cyan]Running:[/cyan] {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green bold]✓ Installation successful![/green bold]")
        console.print(result.stdout)
    else:
        console.print(f"[red]Installation failed:[/red]\n{result.stderr}")
        sys.exit(1)


def get_package_name(apkm_path: str) -> str | None:
    """Try to extract package name from the base APK using aapt2, or infer from filename."""
    # Try inferring from filename first (e.g. com.vlv.aravali.reels_5.7.1-...)
    basename = os.path.basename(apkm_path)
    parts = basename.split("_")
    if parts and parts[0].count(".") >= 2:
        return parts[0]

    # Try aapt2 (located via find_aapt2)
    aapt2 = find_aapt2()
    if not aapt2:
        console.print("[dim]aapt2 not found — skipping package name detection[/dim]")
        return None

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(apkm_path, "r") as zf:
                for name in zf.namelist():
                    if name == "base.apk" or (name.endswith(".apk") and "base" in name.lower()):
                        zf.extract(name, tmp)
                        apk_path = os.path.join(tmp, name)
                        result = subprocess.run(
                            [aapt2, "dump", "badging", apk_path],
                            capture_output=True, text=True,
                        )
                        if result.returncode == 0:
                            for line in result.stdout.splitlines():
                                if line.startswith("package:"):
                                    p = line.split("name='")
                                    if len(p) > 1:
                                        return p[1].split("'")[0]
    except Exception as e:
        console.print(f"[dim]aapt2 error: {e}[/dim]")
    return None


def main():
    parser = argparse.ArgumentParser(description="Install APKM file on Android device/emulator")
    parser.add_argument("--apkm", required=True, help="Path to the .apkm file")
    parser.add_argument("--serial", default=None, help="Device serial (optional)")
    args = parser.parse_args()

    apkm_path = os.path.abspath(args.apkm)
    if not os.path.isfile(apkm_path):
        console.print(f"[red]File not found: {apkm_path}[/red]")
        sys.exit(1)

    console.print(f"\n[bold]APKM Installer[/bold]")
    console.print(f"File: {apkm_path}\n")

    # Try to get package name
    pkg = get_package_name(apkm_path)
    if pkg:
        console.print(f"[cyan]Package:[/cyan] {pkg}")

    adb = find_adb()
    serial = args.serial or check_device(adb).split()[0]

    with tempfile.TemporaryDirectory() as tmp_dir:
        console.print("\n[cyan]Extracting APKs from APKM...[/cyan]")
        apk_files = extract_apkm(apkm_path, tmp_dir)
        console.print(f"\n[cyan]Found {len(apk_files)} APK(s). Installing...[/cyan]")
        install_apks(adb, serial, apk_files)

    if pkg:
        console.print(f"\n[green]Launch with:[/green] adb -s {serial} shell monkey -p {pkg} 1")


if __name__ == "__main__":
    main()
