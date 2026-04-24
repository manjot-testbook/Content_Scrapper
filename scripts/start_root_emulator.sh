#!/usr/bin/env bash
# Start KukuTV_Root emulator, enable root, install mitmproxy cert
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
EMULATOR="$HOME/Library/Android/sdk/emulator/emulator"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"

echo "[1] Killing any running emulator..."
"$ADB" emu kill 2>/dev/null || true
sleep 3

echo "[2] Starting KukuTV_Root AVD (google_apis — adb root supported)..."
"$EMULATOR" -avd KukuTV_Root -no-snapshot-save -no-audio > "$PROJECT/logs/emulator.log" 2>&1 &
echo $! > "$PROJECT/logs/emulator.pid"
echo "    Emulator PID: $!"

echo "[3] Waiting for boot (up to 180s)..."
waited=0
while [ $waited -lt 180 ]; do
    boot=$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
    if [ "$boot" = "1" ]; then
        echo "    Booted!"
        break
    fi
    sleep 5; waited=$((waited+5)); printf "."
done
echo ""

echo "[4] Enabling ADB root..."
"$ADB" root
sleep 3

echo "[5] Remounting system partition..."
"$ADB" remount
sleep 1

echo "[6] Generating mitmproxy cert if needed..."
if [ ! -f "$CERT" ]; then
    echo "    Generating cert..."
    mitmdump --listen-port 8081 &
    MPID=$!
    sleep 4
    kill $MPID 2>/dev/null
fi

echo "[7] Installing mitmproxy CA as system cert..."
HASH=$(openssl x509 -inform PEM -subject_hash_old -in "$CERT" 2>/dev/null | head -1)
echo "    Cert hash: $HASH"
"$ADB" push "$CERT" "/system/etc/security/cacerts/${HASH}.0"
"$ADB" shell chmod 644 "/system/etc/security/cacerts/${HASH}.0"

# Verify
CHECK=$("$ADB" shell ls "/system/etc/security/cacerts/${HASH}.0" 2>/dev/null)
if [ -n "$CHECK" ]; then
    echo "    [OK] Cert installed: $CHECK"
else
    echo "    [ERR] Cert not found — check remount"
    exit 1
fi

echo "[8] Rebooting to apply cert..."
"$ADB" reboot
sleep 15
"$ADB" wait-for-device
waited=0
while [ $waited -lt 90 ]; do
    boot=$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
    if [ "$boot" = "1" ]; then echo "    Rebooted!"; break; fi
    sleep 5; waited=$((waited+5)); printf "."
done
echo ""
echo ""
echo "=== KukuTV_Root emulator ready with mitmproxy CA trusted ==="
echo ""
echo "Next steps:"
echo "  1. Pull KukuTV APKs from Medium_Phone emulator and install here:"
echo "     python scripts/transfer_app.py"
echo "  OR install KukuTV manually via adb:"
echo "     adb install-multiple <apk files>"
echo ""
echo "  2. Then start capturing:"
echo "     ./run.sh proxy"
echo "     ./run.sh navigate"
