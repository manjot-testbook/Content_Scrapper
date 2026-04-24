"""
bypass_ssl_pinning.py — Use Frida to bypass SSL certificate pinning on KukuTV app.
Required when the app rejects mitmproxy's CA certificate.

Prerequisites:
    - Rooted device/emulator
    - frida-server running on device
    - pip install frida-tools
"""

import argparse
import subprocess
import sys
import time

import frida
from rich.console import Console

console = Console()

# Universal SSL pinning bypass script for Android
# Covers: OkHttp, TrustManager, WebView, network_security_config
SSL_BYPASS_SCRIPT = """
Java.perform(function() {
    console.log("[*] SSL Pinning Bypass loaded");

    // === TrustManager bypass ===
    try {
        var TrustManagerImpl = Java.use("com.android.org.conscrypt.TrustManagerImpl");
        TrustManagerImpl.verifyChain.implementation = function(untrustedChain, trustAnchorChain,
            host, clientAuth, ocspData, tlsSctData) {
            console.log("[+] TrustManagerImpl.verifyChain bypassed for: " + host);
            return untrustedChain;
        };
    } catch(e) { console.log("[-] TrustManagerImpl not found: " + e); }

    // === OkHttp CertificatePinner bypass ===
    try {
        var CertificatePinner = Java.use("okhttp3.CertificatePinner");
        CertificatePinner.check.overload("java.lang.String", "java.util.List").implementation = function(hostname, peerCertificates) {
            console.log("[+] OkHttp CertificatePinner.check bypassed for: " + hostname);
        };
    } catch(e) { console.log("[-] OkHttp3 CertificatePinner not found: " + e); }

    // === OkHttp CertificatePinner (older) ===
    try {
        var CertificatePinner2 = Java.use("okhttp3.CertificatePinner");
        CertificatePinner2.check.overload("java.lang.String", "[Ljava.security.cert.Certificate;").implementation = function(hostname, certs) {
            console.log("[+] OkHttp CertificatePinner.check (array) bypassed for: " + hostname);
        };
    } catch(e) {}

    // === SSLContext bypass ===
    try {
        var SSLContext = Java.use("javax.net.ssl.SSLContext");
        SSLContext.init.overload("[Ljavax.net.ssl.KeyManager;", "[Ljavax.net.ssl.TrustManager;",
            "java.security.SecureRandom").implementation = function(km, tm, sr) {
            console.log("[+] SSLContext.init bypassed");
            var TrustManager = Java.use("javax.net.ssl.X509TrustManager");
            var MyTrustManager = Java.registerClass({
                name: "com.bypass.TrustManager",
                implements: [TrustManager],
                methods: {
                    checkClientTrusted: function(chain, authType) {},
                    checkServerTrusted: function(chain, authType) {},
                    getAcceptedIssuers: function() { return []; }
                }
            });
            var myTm = MyTrustManager.$new();
            this.init(km, [myTm], sr);
        };
    } catch(e) { console.log("[-] SSLContext bypass failed: " + e); }

    // === WebView SSL bypass ===
    try {
        var WebViewClient = Java.use("android.webkit.WebViewClient");
        WebViewClient.onReceivedSslError.implementation = function(view, handler, error) {
            console.log("[+] WebView SSL error bypassed");
            handler.proceed();
        };
    } catch(e) {}

    // === HostnameVerifier bypass ===
    try {
        var HostnameVerifier = Java.use("javax.net.ssl.HostnameVerifier");
        var SSLSession = Java.use("javax.net.ssl.SSLSession");
        // Find and bypass all HostnameVerifier implementations
        Java.enumerateLoadedClasses({
            onMatch: function(className) {
                try {
                    var cls = Java.use(className);
                    if (cls.verify) {
                        cls.verify.overload("java.lang.String", "javax.net.ssl.SSLSession").implementation = function(hostname, session) {
                            console.log("[+] HostnameVerifier bypassed for: " + hostname);
                            return true;
                        };
                    }
                } catch(e) {}
            },
            onComplete: function() {}
        });
    } catch(e) {}

    console.log("[*] SSL Pinning Bypass complete");
});
"""


def check_frida_server(device_serial: str | None = None):
    """Check if frida-server is running on the device."""
    adb_cmd = ["adb"]
    if device_serial:
        adb_cmd.extend(["-s", device_serial])

    result = subprocess.run(
        adb_cmd + ["shell", "ps | grep frida"],
        capture_output=True, text=True,
    )
    if "frida" in result.stdout:
        console.print("[green]✓ frida-server is running[/green]")
        return True
    else:
        console.print("[yellow]⚠ frida-server not detected on device[/yellow]")
        console.print("  Start it with: adb shell '/data/local/tmp/frida-server &'")
        return False


def main():
    parser = argparse.ArgumentParser(description="Bypass SSL pinning on KukuTV using Frida")
    parser.add_argument("--package", default="com.kukufm.android", help="App package name")
    parser.add_argument("--device", default=None, help="Device serial")
    parser.add_argument("--spawn", action="store_true", help="Spawn app (vs attach to running)")
    args = parser.parse_args()

    check_frida_server(args.device)

    console.print(f"\n[bold]Frida SSL Pinning Bypass[/bold]")
    console.print(f"Package: {args.package}")
    console.print(f"Mode: {'spawn' if args.spawn else 'attach'}\n")

    try:
        if args.device:
            device = frida.get_device(args.device)
        else:
            device = frida.get_usb_device(timeout=10)

        console.print(f"[green]✓ Connected to device: {device.name}[/green]")

        if args.spawn:
            pid = device.spawn([args.package])
            console.print(f"[cyan]Spawned {args.package} (PID: {pid})[/cyan]")
            session = device.attach(pid)
            script = session.create_script(SSL_BYPASS_SCRIPT)
            script.on("message", lambda msg, data: console.print(f"  [dim]{msg}[/dim]"))
            script.load()
            device.resume(pid)
        else:
            session = device.attach(args.package)
            script = session.create_script(SSL_BYPASS_SCRIPT)
            script.on("message", lambda msg, data: console.print(f"  [dim]{msg}[/dim]"))
            script.load()

        console.print(f"\n[green bold]✓ SSL pinning bypass active![/green bold]")
        console.print("[yellow]Keep this running while using the proxy. Press Ctrl+C to stop.[/yellow]\n")

        sys.stdin.read()  # Keep alive

    except frida.ProcessNotFoundError:
        console.print(f"[red]Process not found: {args.package}[/red]")
        console.print("Make sure the app is running, or use --spawn")
        sys.exit(1)
    except frida.ServerNotRunningError:
        console.print("[red]frida-server not running on device[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Bypass stopped.[/yellow]")


if __name__ == "__main__":
    main()
