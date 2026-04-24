#!/usr/bin/env bash
# start_emulator.sh — Start emulator with writable-system for cert installation
# Run this from a fresh terminal: bash scripts/start_emulator.sh

set -e
EMULATOR="$HOME/Library/Android/sdk/emulator/emulator"
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
LOG="/Users/manjotsingh/PycharmProjects/Content_Scrapper/logs/emulator.log"
AVD="${1:-Medium_Phone_API_36.1}"

mkdir -p "$(dirname "$LOG")"

echo "[*] Starting AVD: $AVD (with -writable-system)"
"$EMULATOR" -avd "$AVD" -writable-system -no-snapshot-save -no-audio > "$LOG" 2>&1 &
PID=$!
echo "[*] Emulator PID: $PID"
echo "$PID" > /tmp/emulator.pid

echo "[*] Waiting for device..."
"$ADB" wait-for-device

echo "[*] Waiting for boot to complete..."
BOOTED=""
for i in $(seq 1 30); do
    BOOTED=$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
    if [ "$BOOTED" = "1" ]; then
        echo "[OK] Emulator booted!"
        break
    fi
    echo "  ... ${i}0s"
    sleep 10
done

if [ "$BOOTED" != "1" ]; then
    echo "[WARN] Boot not confirmed yet — emulator may still be starting"
fi

echo ""
echo "[*] Getting root access..."
"$ADB" root && sleep 3

echo "[*] Remounting /system as writable..."
"$ADB" remount 2>&1 || "$ADB" shell "mount -o remount,rw /system" 2>&1

echo ""
echo "[*] Installing mitmproxy CA cert as system cert..."
CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
HASH=$(openssl x509 -inform PEM -subject_hash_old -in "$CERT" 2>/dev/null | head -1)
echo "    Cert hash: $HASH"

"$ADB" push "$CERT" "/system/etc/security/cacerts/${HASH}.0"
"$ADB" shell chmod 644 "/system/etc/security/cacerts/${HASH}.0"
"$ADB" shell ls "/system/etc/security/cacerts/${HASH}.0"

echo ""
echo "[OK] System CA cert installed!"
echo "[*] Setting proxy to 192.168.1.4:8080 ..."
"$ADB" shell settings put global http_proxy 192.168.1.4:8080

echo ""
echo "============================================"
echo "  Emulator ready for traffic capture!"
echo "  Next: cd .. && ./run.sh capture"
echo "============================================"
