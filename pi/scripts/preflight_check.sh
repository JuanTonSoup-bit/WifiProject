#!/usr/bin/env bash
# Pre-flight checks for the Pi CSI capture system.
set -uo pipefail

PASS=0
FAIL=0
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

# 1. Nexmon kernel module
if lsmod | grep -q brcmfmac; then
    check "Nexmon brcmfmac module loaded" "ok"
else
    check "Nexmon brcmfmac module loaded" "module not loaded (run: modprobe brcmfmac)"
fi

# 2. Monitor mode
IFACE="wlan0"
MODE=$(iw dev "$IFACE" info 2>/dev/null | grep -oP 'type \K\w+' || echo "unknown")
if [[ "$MODE" == "monitor" ]]; then
    check "wlan0 in monitor mode" "ok"
else
    check "wlan0 in monitor mode" "mode=$MODE (run: configure_monitor.sh)"
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

# 6. nexutil
if command -v nexutil &>/dev/null; then
    check "nexutil installed" "ok"
else
    check "nexutil installed" "not found (build from nexmon repo)"
fi

# 7. CSI streamer script
if [[ -f /usr/local/lib/wifi-csi-streamer.py ]]; then
    check "CSI streamer script installed" "ok"
else
    check "CSI streamer script installed" "not found at /usr/local/lib/wifi-csi-streamer.py"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
