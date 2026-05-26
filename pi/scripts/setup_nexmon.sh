#!/usr/bin/env bash
# Nexmon CSI setup script for Raspberry Pi 4.
# Run as root. Idempotent — safe to run multiple times.
set -euo pipefail

NEXMON_DIR="/opt/nexmon"
NEXMON_CSI_DIR="/opt/nexmon_csi"
CONFIG_DIR="/etc/wifi-csi"
SERVICE_USER="wifi-csi"
LOG_DIR="/var/log/wifi-csi"
RUN_DIR="/run/wifi-csi"

log() { echo "[setup_nexmon] $*"; }

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
log "Installing build dependencies..."
apt-get update -q
apt-get install -y --no-install-recommends \
    git cmake libgmp-dev gawk qpdf bison flex make automake \
    bc raspberrypi-kernel-headers \
    iw tcpdump python3 python3-pip python3-yaml \
    net-tools iproute2 curl

# ---------------------------------------------------------------------------
# Nexmon (base)
# ---------------------------------------------------------------------------
if [[ ! -d "$NEXMON_DIR" ]]; then
    log "Cloning nexmon..."
    git clone --depth=1 https://github.com/seemoo-lab/nexmon.git "$NEXMON_DIR"
else
    log "nexmon already cloned at $NEXMON_DIR"
fi

# ---------------------------------------------------------------------------
# Nexmon CSI
# ---------------------------------------------------------------------------
if [[ ! -d "$NEXMON_CSI_DIR" ]]; then
    log "Cloning nexmon_csi..."
    git clone --depth=1 https://github.com/seemoo-lab/nexmon_csi.git "$NEXMON_CSI_DIR"
else
    log "nexmon_csi already cloned at $NEXMON_CSI_DIR"
fi

# ---------------------------------------------------------------------------
# Build nexmon kernel module for RPi4
# ---------------------------------------------------------------------------
log "Building nexmon kernel module..."
cd "$NEXMON_DIR"
source setup_env.sh
make -C buildtools/kerneltools

cd "$NEXMON_CSI_DIR"
# Target: RPi4 brcmfmac43455, firmware 7.45.241
CHIP_DIR="$NEXMON_CSI_DIR/patches/bcm43455c0/7_45_189/nexmon"
if [[ -d "$CHIP_DIR" ]]; then
    cd "$CHIP_DIR"
    make install-firmware
    log "Firmware installed."
else
    log "WARNING: chip directory $CHIP_DIR not found."
    log "Check nexmon_csi for correct RPi4 chip/firmware version."
fi

# ---------------------------------------------------------------------------
# Build and install nexutil
# ---------------------------------------------------------------------------
log "Building nexutil..."
cd "$NEXMON_DIR/utilities/nexutil"
make && make install
log "nexutil installed."

# ---------------------------------------------------------------------------
# System user
# ---------------------------------------------------------------------------
if ! id -u "$SERVICE_USER" &>/dev/null; then
    log "Creating system user $SERVICE_USER..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
mkdir -p "$CONFIG_DIR" "$LOG_DIR" "$RUN_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR" "$RUN_DIR"

# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    log "Writing default config to $CONFIG_DIR/config.yaml"
    cat > "$CONFIG_DIR/config.yaml" << 'YAML'
network:
  pc_ip: "192.168.2.100"
  udp_port: 5500

csi:
  interface: "wlan0"
  channel: 6
  bandwidth: 20

pi:
  log_level: "INFO"
  stats_interval_s: 10
  health_port: 8080
YAML
fi

# ---------------------------------------------------------------------------
# Copy capture scripts
# ---------------------------------------------------------------------------
SCRIPT_SRC="$(dirname "$(realpath "$0")")/.."
install -m 755 "$SCRIPT_SRC/scripts/configure_monitor.sh" /usr/local/bin/wifi-csi-monitor
install -m 755 "$SCRIPT_SRC/scripts/preflight_check.sh" /usr/local/bin/wifi-csi-preflight
install -m 644 "$SCRIPT_SRC/capture/csi_streamer.py" /usr/local/lib/wifi-csi-streamer.py
install -m 644 "$SCRIPT_SRC/capture/health_monitor.py" /usr/local/lib/wifi-csi-health.py

# ---------------------------------------------------------------------------
# Systemd services
# ---------------------------------------------------------------------------
install -m 644 "$SCRIPT_SRC/systemd/wifi-csi-capture.service" /etc/systemd/system/
install -m 644 "$SCRIPT_SRC/systemd/wifi-csi-health.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable wifi-csi-capture.service wifi-csi-health.service
systemctl start wifi-csi-capture.service wifi-csi-health.service

log "Setup complete. Check status with: systemctl status wifi-csi-capture"
log "View logs with: journalctl -u wifi-csi-capture -f"
