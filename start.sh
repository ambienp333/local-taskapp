#!/bin/bash
cd "$(dirname "$0")"

TOR_SOCKS_PORT=9150
TOR_DATA=$(mktemp -d)
TOR_RC=$(mktemp)
TOR_LOG=$(mktemp)
TORSOCKS_CONF=$(mktemp)

cleanup() {
    kill "$TOR_PID" 2>/dev/null
    rm -rf "$TOR_DATA" "$TOR_RC" "$TOR_LOG" "$TORSOCKS_CONF"
}
trap cleanup EXIT INT TERM

# Free the port if a previous instance didn't clean up
fuser -k ${TOR_SOCKS_PORT}/tcp 2>/dev/null || true

echo "TorAddress 127.0.0.1" > "$TORSOCKS_CONF"
echo "TorPort $TOR_SOCKS_PORT" >> "$TORSOCKS_CONF"

tor -f "$TOR_RC" \
    --SocksPort $TOR_SOCKS_PORT \
    --SocksPolicy "accept 127.0.0.1" \
    --SocksPolicy "reject *" \
    --ControlPort 0 \
    --DataDirectory "$TOR_DATA" \
    --Log "notice file $TOR_LOG" \
    --Log "warn stderr" &
TOR_PID=$!

# Wait for full bootstrap (circuits ready), not just port open
echo "[start] waiting for Tor to bootstrap..."
for i in $(seq 1 60); do
    grep -q "Bootstrapped 100%" "$TOR_LOG" 2>/dev/null && break
    sleep 1
done

if grep -q "Bootstrapped 100%" "$TOR_LOG" 2>/dev/null; then
    echo "[start] Tor ready"
else
    echo "[start] WARNING: Tor did not fully bootstrap in 60s"
fi

TORSOCKS_CONF_FILE="$TORSOCKS_CONF" torsocks python server/server.py
