#!/usr/bin/env bash
# =============================================================================
# KukuTV Content Scraper — Master Run Script
# =============================================================================
# Usage:
#   ./run.sh capture   → start proxy + navigate app + capture APIs
#   ./run.sh analyze   → analyze captured traffic
#   ./run.sh scrape    → download videos from discovered URLs
#   ./run.sh all       → capture + analyze + scrape
#   ./run.sh proxy     → start proxy only (background)
#   ./run.sh navigate  → run Appium navigator only
#   ./run.sh status    → print current state
# =============================================================================

set -e
PROJECT="$(cd "$(dirname "$0")" && pwd)"
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
PYTHON="python"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

log()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}   $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC}  $*"; }

# ── helpers ───────────────────────────────────────────────────────────────────

check_deps() {
    log "Checking dependencies..."
    local missing=0
    for cmd in mitmdump ffmpeg adb; do
        if ! command -v "$cmd" &>/dev/null; then
            warn "$cmd not found"
            missing=$((missing+1))
        else
            ok "$cmd found"
        fi
    done
    if ! python -c "import appium" 2>/dev/null; then
        warn "Appium Python client not installed (pip install Appium-Python-Client)"
        missing=$((missing+1))
    fi
    return $missing
}

check_device() {
    local devices
    devices=$("$ADB" devices 2>/dev/null | grep -v "^List" | grep "device$" | wc -l | tr -d ' ')
    if [ "$devices" -eq 0 ]; then
        err "No Android device/emulator connected."
        echo ""
        echo "  Start Android emulator from Android Studio, then re-run."
        echo "  Emulator AVD should have:"
        echo "    • Google Play Store enabled (app installed via Play)"
        echo "    • API 30+ recommended"
        return 1
    fi
    ok "Device connected ($devices device(s))"
}

check_app() {
    local pkg="com.vlv.aravali.reels"
    if "$ADB" shell pm list packages 2>/dev/null | grep -q "$pkg"; then
        ok "KukuTV app installed"
    else
        err "KukuTV (com.vlv.aravali.reels) not installed on device."
        echo "  Install from Play Store, then re-run."
        return 1
    fi
}

start_proxy() {
    if lsof -i :8080 -sTCP:LISTEN &>/dev/null; then
        warn "Proxy already running on :8080"
        return 0
    fi

    log "Starting mitmproxy..."
    mkdir -p "$PROJECT/logs"

    # Clear old log to get a fresh capture session
    rm -f "$PROJECT/metadata/captured_apis/api_traffic.jsonl"

    mitmdump \
        -s "$PROJECT/mitm_addons/mitm_addon.py" \
        --listen-port 8080 \
        --set flow_detail=0 \
        --ssl-insecure \
        > "$PROJECT/logs/mitm.log" 2>&1 &

    MITM_PID=$!
    echo "$MITM_PID" > "$PROJECT/logs/mitm.pid"
    sleep 3

    if lsof -i :8080 -sTCP:LISTEN &>/dev/null; then
        ok "mitmproxy started (PID $MITM_PID)"
    else
        err "mitmproxy failed to start. Check logs/mitm.log"
        return 1
    fi
}

configure_device_proxy() {
    local host_ip
    host_ip=$(python3 -c "import socket; s=socket.socket(); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()")
    log "Setting device proxy to $host_ip:8080 ..."
    "$ADB" shell settings put global http_proxy "$host_ip:8080"

    # Install mitmproxy CA cert if not yet installed
    local cert="$HOME/.mitmproxy/mitmproxy-ca-cert.cer"
    if [ -f "$cert" ]; then
        "$ADB" push "$cert" /sdcard/Download/mitmproxy-ca-cert.cer 2>/dev/null || true
        ok "CA cert pushed to /sdcard/Download/ — install via: Settings → Security → Install from storage"
    else
        warn "mitmproxy CA cert not found at $cert — run 'mitmdump' once first to generate it."
    fi
}

stop_proxy() {
    if [ -f "$PROJECT/logs/mitm.pid" ]; then
        kill "$(cat "$PROJECT/logs/mitm.pid")" 2>/dev/null || true
        rm -f "$PROJECT/logs/mitm.pid"
        ok "mitmproxy stopped"
    fi
    # Clear device proxy
    "$ADB" shell settings put global http_proxy ":0" 2>/dev/null || true
    ok "Device proxy cleared"
}

# ── commands ──────────────────────────────────────────────────────────────────

cmd_status() {
    echo ""
    echo "═══════════════════════════════════"
    echo "        KukuTV Scraper Status      "
    echo "═══════════════════════════════════"
    "$ADB" devices 2>/dev/null | grep -v "^List" || true
    "$ADB" shell pm list packages 2>/dev/null | grep kuku || echo "  App: not installed"
    lsof -i :8080 -sTCP:LISTEN &>/dev/null && echo "  Proxy: running on :8080" || echo "  Proxy: not running"
    local log="$PROJECT/metadata/captured_apis/api_traffic.jsonl"
    [ -f "$log" ] && echo "  Traffic: $(wc -l < "$log") requests captured" || echo "  Traffic: no log yet"
    echo ""
}

cmd_proxy() {
    check_device
    start_proxy
    configure_device_proxy
    log "Proxy is running. Browse app manually or run:  ./run.sh navigate"
    log "Stop proxy with:  ./run.sh stop"
}

cmd_stop() { stop_proxy; }

cmd_navigate() {
    log "Starting Appium navigator..."
    "$PYTHON" "$PROJECT/scripts/appium_navigator.py" "$@"
}

cmd_analyze() {
    log "Analyzing captured traffic..."
    "$PYTHON" "$PROJECT/scripts/analyze_apis.py"
    log "Quick summary:"
    "$PYTHON" "$PROJECT/scripts/quick_analyze.py"
    ok "Results → logs/traffic_summary.json  &  metadata/api_catalog/"
}

cmd_scrape() {
    log "Downloading videos..."
    "$PYTHON" "$PROJECT/scripts/scraper.py" --mode both "$@"
}

cmd_bypass() {
    log "Injecting Frida SSL pinning bypass..."
    "$PYTHON" "$PROJECT/scripts/bypass_ssl_pinning.py" "$@"
}

cmd_capture() {
    check_device
    check_app
    start_proxy
    configure_device_proxy

    log "Launching KukuTV app + navigating via Appium..."
    "$PYTHON" "$PROJECT/scripts/appium_navigator.py" || warn "Appium navigator finished (may need Appium server running)"

    local log="$PROJECT/metadata/captured_apis/api_traffic.jsonl"
    local count=0
    [ -f "$log" ] && count=$(wc -l < "$log" | tr -d ' ')
    ok "$count requests captured"

    if [ "$count" -eq 0 ]; then
        warn "No traffic captured — app may have SSL pinning."
        warn "Try:  ./run.sh bypass   (requires Frida/rooted emulator)"
        warn "Or manually browse the app for ~2 minutes while proxy is running."
    fi
}

cmd_all() {
    cmd_capture
    cmd_analyze
    cmd_scrape
}

# ── main ──────────────────────────────────────────────────────────────────────

CMD="${1:-status}"
shift 2>/dev/null || true

case "$CMD" in
    status)   cmd_status ;;
    proxy)    cmd_proxy ;;
    stop)     cmd_stop ;;
    navigate) cmd_navigate "$@" ;;
    analyze)  cmd_analyze ;;
    scrape)   cmd_scrape "$@" ;;
    bypass)   cmd_bypass "$@" ;;
    capture)  cmd_capture ;;
    all)      cmd_all ;;
    *)
        echo "Usage: ./run.sh [status|proxy|stop|navigate|analyze|scrape|bypass|capture|all]"
        exit 1
        ;;
esac
