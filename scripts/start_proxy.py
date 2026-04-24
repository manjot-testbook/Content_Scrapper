"""
start_proxy.py — Launch mitmproxy with the KukuTV capture addon,
and optionally configure the connected Android device to use the proxy.
"""

import argparse
import os
import shutil
import subprocess
import sys

from rich.console import Console

console = Console()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_PATH = os.path.join(PROJECT_ROOT, "mitm_addons", "mitm_addon.py")


def get_local_ip() -> str:
    """Get the local network IP of this machine."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def setup_device_proxy(adb: str, host_ip: str, port: int):
    """Configure Android device/emulator to route traffic through our proxy."""
    console.print(f"\n[cyan]Setting device proxy to {host_ip}:{port}...[/cyan]")
    subprocess.run([adb, "shell", "settings", "put", "global", "http_proxy", f"{host_ip}:{port}"])
    console.print("[green]✓ Device proxy configured[/green]")

    # Push mitmproxy CA cert to device
    cert_path = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.cer")
    if os.path.isfile(cert_path):
        console.print("[cyan]Pushing mitmproxy CA cert to device...[/cyan]")
        subprocess.run([adb, "push", cert_path, "/sdcard/Download/mitmproxy-ca-cert.cer"])
        console.print("[yellow]⚠ You must manually install the CA cert on the device:[/yellow]")
        console.print("  Settings → Security → Install from storage → select mitmproxy-ca-cert.cer")
    else:
        console.print("[yellow]⚠ mitmproxy CA cert not found. Run mitmdump once first to generate it.[/yellow]")


def clear_device_proxy(adb: str):
    """Remove proxy settings from device."""
    subprocess.run([adb, "shell", "settings", "put", "global", "http_proxy", ":0"])
    console.print("[green]✓ Device proxy cleared[/green]")


def main():
    parser = argparse.ArgumentParser(description="Start mitmproxy with KukuTV capture addon")
    parser.add_argument("--port", type=int, default=8080, help="Proxy port (default: 8080)")
    parser.add_argument("--mode", choices=["transparent", "regular", "upstream"], default="regular")
    parser.add_argument("--setup-device", action="store_true", help="Auto-configure connected device proxy")
    parser.add_argument("--clear-proxy", action="store_true", help="Clear device proxy and exit")
    parser.add_argument("--web", action="store_true", help="Use mitmweb (GUI) instead of mitmdump")
    args = parser.parse_args()

    adb = shutil.which("adb")

    if args.clear_proxy:
        if adb:
            clear_device_proxy(adb)
        return

    host_ip = get_local_ip()
    console.print(f"\n[bold]KukuTV MITM Proxy[/bold]")
    console.print(f"Host IP: {host_ip}")
    console.print(f"Port: {args.port}")
    console.print(f"Addon: {ADDON_PATH}")

    if args.setup_device and adb:
        setup_device_proxy(adb, host_ip, args.port)

    # Build mitm command
    tool = "mitmweb" if args.web else "mitmdump"
    cmd = [
        tool,
        "-s", ADDON_PATH,
        "--listen-port", str(args.port),
        "--set", "flow_detail=0",  # reduce noise, our addon handles logging
    ]

    if args.mode == "transparent":
        cmd.append("--mode=transparent")

    console.print(f"\n[cyan]Starting {tool}...[/cyan]")
    console.print(f"[dim]{' '.join(cmd)}[/dim]\n")
    console.print("[yellow]Configure your device proxy to:[/yellow]")
    console.print(f"  [bold]{host_ip}:{args.port}[/bold]\n")

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        console.print("\n[yellow]Proxy stopped.[/yellow]")
    finally:
        if args.setup_device and adb:
            clear_device_proxy(adb)


if __name__ == "__main__":
    main()
