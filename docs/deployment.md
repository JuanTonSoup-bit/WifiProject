# Deployment Guide — WiFi CSI Motion Detection System

## Architecture Overview

```
Laptop (WiFi TX: 192.168.1.x)
    │ 802.11 packets (100 Hz)
    ▼
Raspberry Pi 4
    wlan0 → monitor mode, Nexmon CSI capture
    eth0  → 192.168.2.50
    │ UDP stream (port 5500)
    ▼
Desktop PC
    eth0  → 192.168.2.100
    └── ingestion → DSP → detection → visualization
```

---

## Section 1: Network Setup

### 1.1 Ethernet (Pi ↔ PC)

Both devices need static IPs on the same subnet.

**On the Pi** (`/etc/systemd/network/10-eth0.network`):
```ini
[Match]
Name=eth0

[Network]
Address=192.168.2.50/24
```
```bash
sudo systemctl enable systemd-networkd
sudo systemctl restart systemd-networkd
```

**On the PC (Linux, nmcli)**:
```bash
sudo nmcli con add type ethernet ifname eth0 con-name csi-eth \
    ip4 192.168.2.100/24
sudo nmcli con up csi-eth
```

**On the PC (Windows, PowerShell)**:
```powershell
New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress 192.168.2.100 -PrefixLength 24
```

### 1.2 Firewall (PC — Linux)
```bash
sudo ufw allow in on eth0 to any port 5500 proto udp comment "CSI stream"
sudo ufw allow in on lo to any port 5599 comment "control socket"
```

**Windows Firewall**:
```powershell
New-NetFirewallRule -DisplayName "CSI UDP Inbound" -Direction Inbound `
    -Protocol UDP -LocalPort 5500 -Action Allow
```

### 1.3 Verify Connectivity
```bash
# From Pi:
ping 192.168.2.100 -c 4

# From PC:
ping 192.168.2.50 -c 4
```

---

## Section 2: Raspberry Pi Setup

### 2.1 Start from Raspberry Pi OS Lite (64-bit)
Download: https://www.raspberrypi.com/software/operating-systems/

Flash with `rpi-imager` or `dd`. Enable SSH in rpi-imager settings.

### 2.2 Install Nexmon CSI
```bash
# Copy project to Pi
scp -r wifi-csi-detector/pi pi@192.168.2.50:/home/pi/wifi-csi

# SSH in and run setup
ssh pi@192.168.2.50
sudo bash /home/pi/wifi-csi/scripts/setup_nexmon.sh
```

> **Note**: The build takes ~15 minutes on RPi4. The script is idempotent.

### 2.3 Edit Config
```bash
sudo nano /etc/wifi-csi/config.yaml
```
Update `network.pc_ip` to your PC's Ethernet IP.

### 2.4 Run Pre-flight Check
```bash
sudo wifi-csi-preflight
```
All checks should pass before proceeding.

### 2.5 Start Services
```bash
sudo systemctl start wifi-csi-capture wifi-csi-health
sudo systemctl status wifi-csi-capture
```

### 2.6 Verify Streaming
From the PC, run:
```bash
sudo tcpdump -i eth0 udp port 5500 -c 10
```
You should see packets arriving at ~100/sec.

---

## Section 3: Laptop Setup

### 3.1 Install Python
Python 3.9+: https://www.python.org/downloads/

### 3.2 Install Dependencies
```bash
pip install pyyaml
```

### 3.3 Configure Transmitter
Edit `wifi-csi-detector/config.yaml`:
```yaml
transmitter:
  target_ip: "192.168.1.100"   # Pi's WiFi IP (NOT Ethernet)
  rate_hz: 100
  method: "udp"
```

The laptop and Pi must be on the same WiFi network. Get Pi's WiFi IP:
```bash
# On Pi:
ip addr show wlan0
# Look for "inet xxx.xxx.xxx.xxx"
```

### 3.4 Start Transmitter
```bash
# Linux/Mac:
python3 wifi-csi-detector/laptop/transmitter.py --config config.yaml --verbose

# Windows:
python wifi-csi-detector\laptop\transmitter.py --config config.yaml --verbose
```

---

## Section 4: PC Setup

### 4.1 Python Environment
```bash
cd wifi-csi-detector
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate.bat     # Windows
pip install -r requirements.txt
```

### 4.2 Configure
Edit `config.yaml`:
- `network.pi_ip`: Pi's Ethernet IP (192.168.2.50)
- `network.pc_ip`: Your PC's Ethernet IP (192.168.2.100)

### 4.3 Run
```bash
# Matplotlib visualization:
python -m pc.main --config config.yaml

# Web UI (accessible at http://localhost:5503):
python -m pc.main --config config.yaml --web-ui

# Headless (no visualization):
python -m pc.main --config config.yaml --no-viz
```

---

## Section 5: Launch Order

1. **Boot Pi** and wait for services to start (~30s after power on):
   ```bash
   curl http://192.168.2.50:8080/health
   # Expected: {"status":"ok","capture_hz":98.5,...}
   ```

2. **Start laptop transmitter**:
   ```bash
   python3 laptop/transmitter.py --rate 100 --target <Pi_WiFi_IP>
   ```

3. **Run PC main**:
   ```bash
   python -m pc.main
   ```

4. **Wait for baseline** (~3 seconds). The system prints `[*] Waiting for CSI frames...`.

5. **Walk past Pi** to verify motion detection triggers.

---

## Section 6: Verification Checklist

```bash
# Pi health endpoint
curl http://192.168.2.50:8080/health

# PC receiving frames (should show 98-102 Hz)
python -m pc.main --no-viz 2>&1 | grep "Ingestion stats"

# Motion detection responds to movement
# Walk in front of Pi — look for "EVENT MOTION_START" in logs

# Events logged
cat logs/events.jsonl | tail -20

# Latency (run echo server first: python -m pc.ingestion.echo_server)
python3 laptop/latency_probe.py --host 192.168.2.100
```
