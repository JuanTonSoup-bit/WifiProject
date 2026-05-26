#!/usr/bin/env bash
# Configure wlan0 in monitor mode for Nexmon CSI capture.
set -euo pipefail

IFACE="${1:-wlan0}"
CHANNEL="${2:-6}"
BANDWIDTH="${3:-20}"
CONFIG_FILE="/etc/wifi-csi/config.yaml"

log() { echo "[monitor_config] $*"; }

# Read from config if available and args not provided
if [[ -f "$CONFIG_FILE" ]] && command -v python3 &>/dev/null; then
    IFACE=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('csi',{}).get('interface','wlan0'))" 2>/dev/null || echo "$IFACE")
    CHANNEL=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('csi',{}).get('channel',6))" 2>/dev/null || echo "$CHANNEL")
    BANDWIDTH=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('csi',{}).get('bandwidth',20))" 2>/dev/null || echo "$BANDWIDTH")
fi

log "Configuring $IFACE: channel=$CHANNEL bandwidth=${BANDWIDTH}MHz"

# Bring interface down
ip link set "$IFACE" down

# Set monitor mode
iw dev "$IFACE" set type monitor 2>/dev/null || true

# Bring back up
ip link set "$IFACE" up

# Set channel using nexutil (Nexmon must be installed)
if command -v nexutil &>/dev/null; then
    nexutil -I"$IFACE" -s500 -b -l 2500 -v"$(iwconfig "$IFACE" 2>/dev/null | grep -oP 'Frequency:\K[0-9.]+')" 2>/dev/null || true
    log "Using nexutil to configure channel $CHANNEL / BW $BANDWIDTH"
    nexutil -I"$IFACE" -s500 -b -l 2500 \
        -v"$(python3 -c "
bw_map = {20: 0, 40: 1, 80: 2}
chan = int($CHANNEL)
bw = int($BANDWIDTH)
# chanspec calculation for 2.4GHz
chanspec = chan | (bw_map.get(bw, 0) << 8) | 0xB000
print(chanspec)
")" 2>/dev/null || log "WARNING: nexutil chanspec config failed (may need firmware loaded first)"
else
    log "WARNING: nexutil not found. Install Nexmon first."
fi

# Verify
MODE=$(iw dev "$IFACE" info 2>/dev/null | grep -oP 'type \K\w+' || echo "unknown")
log "Interface $IFACE mode: $MODE"

if [[ "$MODE" == "monitor" ]]; then
    log "Monitor mode confirmed."
else
    log "ERROR: Interface is NOT in monitor mode (got: $MODE)"
    exit 1
fi
