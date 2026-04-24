#!/usr/bin/env bash
# =============================================================================
# KukuTV Content Scraper — Master Run Script
# =============================================================================
# Usage:
#   ./run.sh emulator  → start Android emulator (Play Store AVD required)
#   ./run.sh install   → open Play Store on device to install KukuTV
#   ./run.sh patch              → install mitmproxy CA as system cert (preserves login); falls back to APK patch
#   ./run.sh writable-emulator  → restart emulator with -writable-system so system cert can be installed
#   ./run.sh capture   → start proxy + navigate app + capture APIs
#   ./run.sh analyze   → analyze captured traffic
#   ./run.sh scrape    → download videos from discovered URLs
#   ./run.sh all       → capture + analyze + scrape
#   ./run.sh proxy     → start proxy only (background)
#   ./run.sh navigate  → run Appium navigator only
#   ./run.sh bypass    → Frida SSL pinning bypass
#   ./run.sh fix-net   → clear stale proxy, restore internet on emulator
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

EMULATOR="$HOME/Library/Android/sdk/emulator/emulator"
PREFERRED_AVD="${KUKUTV_AVD:-Medium_Phone_API_36.1}"

start_emulator() {
    # Pick AVD: prefer the configured one, then any Play Store AVD, then first available
    local avd="$PREFERRED_AVD"
    local all_avds
    all_avds=$("$EMULATOR" -list-avds 2>/dev/null)

    if ! echo "$all_avds" | grep -q "^${avd}$"; then
        # Try to find a Play Store AVD
        avd=$(echo "$all_avds" | while read -r a; do
            cfg="$HOME/.android/avd/${a}.avd/config.ini"
            if grep -q 'playstore' "$cfg" 2>/dev/null; then echo "$a"; break; fi
        done | head -1)
        # Fall back to first AVD
        [ -z "$avd" ] && avd=$(echo "$all_avds" | head -1)
    fi

    if [ -z "$avd" ]; then
        err "No AVDs found. Create one in Android Studio with Google Play Store enabled."
        return 1
    fi

    # Warn if selected AVD lacks Play Store
    cfg="$HOME/.android/avd/${avd}.avd/config.ini"
    if ! grep -q 'playstore' "$cfg" 2>/dev/null; then
        warn "AVD '$avd' does NOT have Google Play Store."
        warn "KukuTV requires Google Play Services. Use 'Google Play' system image in AVD Manager."
        warn "Continuing anyway — install KukuTV from Play Store inside the emulator, then run: ./run.sh patch"
    else
        ok "AVD '$avd' has Google Play Store ✓"
    fi

    log "Starting emulator AVD: $avd ..."
    nohup "$EMULATOR" -avd "$avd" -no-snapshot-save -no-audio \
        > "$PROJECT/logs/emulator.log" 2>&1 &
    echo $! > "$PROJECT/logs/emulator.pid"

    log "Waiting for emulator to boot (up to 120s)..."
    local waited=0
    while [ $waited -lt 120 ]; do
        local boot
        boot=$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
        if [ "$boot" = "1" ]; then
            ok "Emulator booted ($avd)"
            return 0
        fi
        sleep 5
        waited=$((waited + 5))
        echo -n "."
    done
    echo ""
    warn "Emulator may still be booting — check Android Studio / logs/emulator.log"
}

check_device() {
    local devices
    devices=$("$ADB" devices 2>/dev/null | grep -v "^List" | grep "device$" | wc -l | tr -d ' ')
    if [ "$devices" -eq 0 ]; then
        warn "No Android device/emulator connected — attempting to start one automatically..."
        mkdir -p "$PROJECT/logs"
        start_emulator || return 1
        # Re-check
        devices=$("$ADB" devices 2>/dev/null | grep -v "^List" | grep "device$" | wc -l | tr -d ' ')
        if [ "$devices" -eq 0 ]; then
            err "Emulator still not detected. Start it manually from Android Studio."
            return 1
        fi
    fi
    ok "Device connected ($devices device(s))"
}

check_app() {
    local pkg="com.vlv.aravali.reels"
    if "$ADB" shell pm list packages 2>/dev/null | grep -q "$pkg"; then
        ok "KukuTV app installed"
    else
        err "KukuTV (com.vlv.aravali.reels) not installed on device."
        echo "  Run './run.sh install' to open Play Store, or install it manually."
        echo "  After installing, run './run.sh patch' to bypass SSL pinning."
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
    clear_device_proxy
}

clear_device_proxy() {
    # :0 is the Android convention for "no proxy"
    "$ADB" shell settings put global http_proxy :0 2>/dev/null || true
    "$ADB" shell settings delete global http_proxy 2>/dev/null || true
    ok "Device proxy cleared — internet should be restored"
    local cur
    cur=$("$ADB" shell settings get global http_proxy 2>/dev/null)
    log "Current proxy value: '${cur}'"
}

# ── commands ──────────────────────────────────────────────────────────────────

cmd_install_cert() {
    "$PYTHON" "$PROJECT/scripts/install_system_cert.py" "$@"
}

cmd_patch_apk() {
    log "Checking for apktool..."
    if ! command -v apktool &>/dev/null; then
        warn "apktool not found — installing via brew..."
        brew install apktool
    fi
    "$PYTHON" "$PROJECT/scripts/patch_apk.py" "$@"
}

cmd_fixnet() {
    log "Clearing stale proxy settings from emulator..."
    clear_device_proxy
    log "Restarting DNS on emulator..."
    "$ADB" shell ndc resolver flushdefaultif 2>/dev/null || true
    ok "Done — emulator internet should be restored."
    warn "If Play Store still fails, reboot emulator: adb reboot"
}

cmd_install() {
    # KukuTV must be installed via Google Play Store inside the emulator.
    local pkg="com.vlv.aravali.reels"
    local play_url="https://play.google.com/store/apps/details?id=${pkg}"

    log "KukuTV must be installed from Google Play Store."
    log "Opening Play Store on device..."
    "$ADB" shell am start -a android.intent.action.VIEW \
        -d "market://details?id=${pkg}" \
        com.android.vending 2>/dev/null || true

    log "If Play Store didn't open, manually browse to:"
    log "  ${play_url}"
    log "After installing KukuTV, run:  ./run.sh patch   to bypass SSL pinning."
}

cmd_patch() {
    check_device

    # ── Strategy 1: Install mitmproxy CA as SYSTEM cert (no reinstall, session preserved) ──
    log "Trying to install mitmproxy CA as system certificate (preserves your login session)..."
    if "$PYTHON" "$PROJECT/scripts/install_system_cert.py" 2>/dev/null; then
        ok "System cert installed — your KukuTV session is intact."
        log "Now run:  ./run.sh proxy   then open the app and browse manually."
        return 0
    fi

    # ── Strategy 2: Restart emulator with -writable-system, then install system cert ──
    warn "Direct root not available (Play Store emulator)."
    warn "Best option: restart emulator with -writable-system to install cert without touching the app."
    echo ""
    echo "  Run these commands in a new terminal:"
    echo "    # 1. Find your AVD name:"
    echo "    ~/Library/Android/sdk/emulator/emulator -list-avds"
    echo ""
    echo "    # 2. Kill current emulator:"
    echo "    adb emu kill"
    echo ""
    echo "    # 3. Restart with writable system:"
    echo "    ~/Library/Android/sdk/emulator/emulator -avd Medium_Phone_API_36.1 -writable-system -no-snapshot-save &"
    echo ""
    echo "    # 4. Wait for boot, then:"
    echo "    adb wait-for-device && adb root && adb remount"
    echo "    ./run.sh install-cert"
    echo ""
    echo "  Your app data (login session) is stored in /data and will NOT be wiped."
    echo ""

    # ── Strategy 3: Patch APK NSC (will ask before uninstalling) ──
    log "Alternatively: patch the APK's network_security_config (may require reinstall)..."
    read -r -p "  Try APK patching now? WARNING: may log you out if signature mismatch. [y/N]: " ans
    if [[ "${ans,,}" == "y" ]]; then
        log "Patching KukuTV NSC to trust mitmproxy CA (user certificates)..."
        "$PYTHON" "$PROJECT/scripts/swap_nsc.py"
    else
        log "Skipped. Use the -writable-system method above to avoid losing your session."
    fi
}

cmd_writable_emulator() {
    # Restart the current Play Store AVD with -writable-system so we can push system certs
    local avd="${1:-Medium_Phone_API_36.1}"
    log "Killing running emulator..."
    "$ADB" emu kill 2>/dev/null || true
    sleep 3
    mkdir -p "$PROJECT/logs"
    log "Restarting '$avd' with -writable-system (app data preserved)..."
    nohup "$EMULATOR" -avd "$avd" -writable-system -no-snapshot-save -no-audio \
        > "$PROJECT/logs/emulator.log" 2>&1 &
    echo $! > "$PROJECT/logs/emulator.pid"
    log "Waiting for boot..."
    local waited=0
    while [ $waited -lt 150 ]; do
        local boot
        boot=$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
        if [ "$boot" = "1" ]; then
            ok "Emulator booted with writable system partition"
            log "Enabling root + remounting..."
            "$ADB" root && sleep 2 && "$ADB" remount && sleep 1
            ok "Ready — now run:  ./run.sh install-cert"
            return 0
        fi
        sleep 5; waited=$((waited+5)); echo -n "."
    done
    echo ""
    warn "Boot timed out — try: adb root && adb remount && ./run.sh install-cert"
}

cmd_emulator() {
    mkdir -p "$PROJECT/logs"
    start_emulator
}

cmd_status() {
    echo ""
    echo "═══════════════════════════════════"
    echo "        KukuTV Scraper Status      "
    echo "═══════════════════════════════════"
    echo "Available AVDs:"
    "$EMULATOR" -list-avds 2>/dev/null | sed 's/^/  /' || echo "  (none)"
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
        warn "Run:  ./run.sh patch   to patch NSC and trust mitmproxy CA (no root needed)."
        warn "Or:   ./run.sh bypass  (requires Frida + rooted emulator)"
        warn "Then browse the app manually for ~2 minutes while proxy is running."
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
    emulator) cmd_emulator ;;
    writable-emulator) cmd_writable_emulator "$@" ;;
    fix-net)      cmd_fixnet ;;
    install-cert) cmd_install_cert "$@" ;;
    patch-apk)    cmd_patch_apk "$@" ;;
    setup)        "$PYTHON" "$PROJECT/scripts/setup_capture.py" "$@" ;;
    install)      cmd_install "$@" ;;
    patch)        cmd_patch ;;
    proxy)    cmd_proxy ;;
    stop)     cmd_stop ;;
    navigate) cmd_navigate "$@" ;;
    analyze)  cmd_analyze ;;
    scrape)   cmd_scrape "$@" ;;
    bypass)   cmd_bypass "$@" ;;
    capture)  cmd_capture ;;
    all)      cmd_all ;;
    *)
        echo "Usage: ./run.sh [status|emulator|install|patch|proxy|stop|navigate|analyze|scrape|bypass|capture|all|fix-net]"
        exit 1
        ;;
esac
