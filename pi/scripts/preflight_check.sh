#!/usr/bin/env bash
# Pre-flight checks for the Pi CSI capture system.
set -uo pipefail

PASS=0
FAIL=0
WARN=0
CONFIG="/etc/wifi-csi/config.yaml"

check() {
    local name="$1"
    local result="$2"
    if [[ "$result" == "ok" ]]; then
        echo "  [PASS] $name"
        ((PASS++))
    else
        echo "  [FAIL] $name: $result"
        ((FAIL++))
    fi
}

echo "=== WiFi CSI Pre-flight Check ==="

# 1. ESP32 #1 at /dev/ttyUSB0
if [ -c /dev/ttyUSB0 ]; then
    check "ESP32 #1 present (/dev/ttyUSB0)" "ok"
else
    check "ESP32 #1 present (/dev/ttyUSB0)" "device not found (check USB connection)"
fi

# 2. ESP32 #2 at /dev/ttyUSB1 (optional — WARN only)
if [ -c /dev/ttyUSB1 ]; then
    check "ESP32 #2 present (/dev/ttyUSB1)" "ok"
else
    echo "  [WARN] ESP32 #2 present (/dev/ttyUSB1): device not found (port2 is optional)"
    ((WARN++))
fi

# 3. Config file
if [[ -f "$CONFIG" ]]; then
    check "Config file present ($CONFIG)" "ok"
else
    check "Config file present ($CONFIG)" "not found"
fi

# 4. Reach PC via Ethernet
PC_IP="192.168.2.100"
if [[ -f "$CONFIG" ]]; then
    PC_IP=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG')); print(c.get('network',{}).get('pc_ip','192.168.2.100'))" 2>/dev/null || echo "$PC_IP")
fi
if ping -c 1 -W 2 "$PC_IP" &>/dev/null; then
    check "Ethernet reach to PC ($PC_IP)" "ok"
else
    check "Ethernet reach to PC ($PC_IP)" "ping failed (check eth0 configuration)"
fi

# 5. Python dependencies
if python3 -c "import yaml" 2>/dev/null; then
    check "Python yaml module" "ok"
else
    check "Python yaml module" "not installed (pip3 install pyyaml)"
fi

# 6. Python serial module
if python3 -c "import serial" 2>/dev/null; then
    check "Python serial module" "ok"
else
    check "Python serial module" "not installed (pip3 install pyserial)"
fi

# 7. CSI streamer script
if [[ -f /usr/local/lib/wifi-csi-streamer.py ]]; then
    check "CSI streamer script installed" "ok"
else
    check "CSI streamer script installed" "not found at /usr/local/lib/wifi-csi-streamer.py"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed, $WARN warned"
[[ $FAIL -eq 0 ]]
