#!/usr/bin/env python3
"""
Full pipeline: ensure proxy running, launch app, capture traffic, analyze.
Run from Content_Scrapper conda env.
"""
import subprocess
import os
import sys
import time
import json

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
PROJECT = "/Users/manjotsingh/PycharmProjects/Content_Scrapper"
TRAFFIC_LOG = os.path.join(PROJECT, "metadata", "captured_apis", "api_traffic.jsonl")
STATE_FILE = os.path.join(PROJECT, "logs", "pipeline_state.json")


def adb_cmd(args):
    r = subprocess.run([ADB] + args, capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def check_state():
    state = {}

    # Device
    out, _, _ = adb_cmd(["devices"])
    state["device_connected"] = "device" in out and "emulator" in out

    # App
    out, _, _ = adb_cmd(["shell", "pm", "list", "packages"])
    state["app_installed"] = "com.vlv.aravali.reels" in out

    # Proxy
    out, _, _ = adb_cmd(["shell", "settings", "get", "global", "http_proxy"])
    state["proxy"] = out

    # Port 8080
    r = subprocess.run(["lsof", "-i", ":8080"], capture_output=True, text=True)
    state["proxy_listening"] = "LISTEN" in r.stdout

    # Traffic log
    state["traffic_log_exists"] = os.path.isfile(TRAFFIC_LOG)
    if state["traffic_log_exists"]:
        with open(TRAFFIC_LOG) as f:
            state["traffic_lines"] = sum(1 for _ in f)
    else:
        state["traffic_lines"] = 0

    return state


def start_proxy_if_needed(state):
    if not state["proxy_listening"]:
        print("Starting mitmproxy...")
        # Clear old log
        if os.path.isfile(TRAFFIC_LOG):
            os.remove(TRAFFIC_LOG)
        addon = os.path.join(PROJECT, "mitm_addons", "mitm_addon.py")
        subprocess.Popen(
            ["mitmdump", "-s", addon, "--listen-port", "8080", "--set", "flow_detail=0"],
            stdout=open(os.path.join(PROJECT, "logs", "mitm.log"), "w"),
            stderr=subprocess.STDOUT,
            cwd=PROJECT,
        )
        time.sleep(5)
        print("mitmproxy started")
    else:
        print("mitmproxy already running")


def set_proxy_if_needed(state):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    host_ip = s.getsockname()[0]
    s.close()

    expected = f"{host_ip}:8080"
    if state["proxy"] != expected:
        print(f"Setting proxy to {expected}...")
        adb_cmd(["shell", "settings", "put", "global", "http_proxy", expected])
    else:
        print(f"Proxy already set: {expected}")


def launch_and_capture():
    print("\nLaunching KukuTV app...")
    adb_cmd(["shell", "am", "force-stop", "com.vlv.aravali.reels"])
    time.sleep(1)
    adb_cmd(["shell", "monkey", "-p", "com.vlv.aravali.reels", "-c",
             "android.intent.category.LAUNCHER", "1"])

    print("Waiting 30 seconds for app to make API calls...")
    for i in range(6):
        time.sleep(5)
        if os.path.isfile(TRAFFIC_LOG):
            with open(TRAFFIC_LOG) as f:
                lines = f.readlines()
            print(f"  {(i+1)*5}s: {len(lines)} requests captured")
        else:
            print(f"  {(i+1)*5}s: no traffic yet")


def show_results():
    if not os.path.isfile(TRAFFIC_LOG):
        print("\nNo traffic captured. App may use SSL pinning.")
        print("Check logs/mitm.log for TLS errors.")

        mitm_log = os.path.join(PROJECT, "logs", "mitm.log")
        if os.path.isfile(mitm_log):
            with open(mitm_log) as f:
                content = f.read()
            tls_fails = content.count("TLS handshake failed")
            client_connects = content.count("client connect")
            print(f"\nMITM log: {client_connects} connections, {tls_fails} TLS failures")
            if tls_fails > 0:
                print("=> SSL pinning detected! Need Frida bypass.")
                # Show unique hosts that failed
                hosts = set()
                for line in content.splitlines():
                    if "server connect" in line:
                        parts = line.split("server connect ")
                        if len(parts) > 1:
                            host = parts[1].split(":")[0].split(" ")[0]
                            hosts.add(host)
                if hosts:
                    print(f"   Hosts seen: {', '.join(sorted(hosts))}")
        return False

    with open(TRAFFIC_LOG) as f:
        lines = f.readlines()

    print(f"\n{'='*60}")
    print(f"CAPTURED {len(lines)} REQUESTS")
    print(f"{'='*60}")

    hosts = {}
    kuku_requests = []
    video_urls = []

    for l in lines:
        r = json.loads(l)
        host = r.get("host", "unknown")
        hosts[host] = hosts.get(host, 0) + 1
        if r.get("is_kukutv"):
            kuku_requests.append(r)
        url = r.get("url", "")
        if any(ext in url.lower() for ext in [".m3u8", ".mp4", ".mpd", ".mp3"]):
            video_urls.append(url)

    print("\nTop hosts:")
    for host, count in sorted(hosts.items(), key=lambda x: -x[1])[:15]:
        kuku = " ★" if any(kw in host.lower() for kw in ["kuku", "vlv", "aravali"]) else ""
        print(f"  {count:4d}  {host}{kuku}")

    if kuku_requests:
        print(f"\nKukuTV API requests: {len(kuku_requests)}")
        for r in kuku_requests[:20]:
            print(f"  {r['method']} {r.get('status_code')} {r.get('path', '')[:80]}")

    if video_urls:
        print(f"\nVideo URLs found: {len(video_urls)}")
        for u in video_urls[:10]:
            print(f"  {u[:120]}")

    return True


def main():
    os.makedirs(os.path.join(PROJECT, "logs"), exist_ok=True)
    os.makedirs(os.path.join(PROJECT, "metadata", "captured_apis"), exist_ok=True)

    print("=== KukuTV Traffic Capture Pipeline ===\n")

    state = check_state()
    print(f"Device: {'✓' if state['device_connected'] else '✗'}")
    print(f"App: {'✓' if state['app_installed'] else '✗'}")
    print(f"Proxy: {state['proxy']}")
    print(f"MITM listening: {'✓' if state['proxy_listening'] else '✗'}")

    if not state["device_connected"]:
        print("\nERROR: No device connected!")
        sys.exit(1)

    if not state["app_installed"]:
        print("\nERROR: App not installed!")
        sys.exit(1)

    start_proxy_if_needed(state)
    set_proxy_if_needed(state)
    launch_and_capture()
    success = show_results()

    # Save state
    final_state = check_state()
    with open(STATE_FILE, "w") as f:
        json.dump(final_state, f, indent=2)


if __name__ == "__main__":
    main()
