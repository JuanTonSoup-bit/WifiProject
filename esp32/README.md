# ESP32 CSI Collector Setup

Each ESP32-WROOM-32D connects to your home WiFi and streams CSI data over UDP to the Pi. No USB connection to the Pi is needed during operation — each board just needs a USB power source (charger or power bank).

The Pi listens on UDP port 5600 and accepts streams from all three ESP32s simultaneously.

---

## Hardware

- 3x Inland ESP32-WROOM-32D
- 3x USB power sources (chargers or power banks)
- Place them around the room for spatial coverage

---

## Arduino IDE Setup

### 1. Install the ESP32 board package

1. Open Arduino IDE 2.x
2. **File > Preferences** → add to "Additional boards manager URLs":
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. **Tools > Board > Boards Manager** → search `esp32` → install **esp32 by Espressif Systems version 2.0.15**
   - Do NOT install 3.x — CSI API changed and will break the sketch
   - Do NOT install "Arduino ESP32 Boards" by Arduino — wrong package

### 2. Select the board

**Tools > Board > ESP32 Arduino > ESP32 Dev Module**

Set port to whichever COM port shows "(USB)" next to it.

### 3. Create your secrets file

Copy `secrets.h.example` to `secrets.h` in the same folder:

```
esp32/csi_collector/secrets.h.example  →  esp32/csi_collector/secrets.h
```

Edit `secrets.h` with your WiFi credentials:
```c
#define WIFI_SSID     "your_network_name"
#define WIFI_PASSWORD "your_password"
```

`secrets.h` is gitignored and will never be committed.

### 4. Check config.h

```c
#define PI_IP    "192.168.1.81"   // Pi's ethernet IP
#define UDP_PORT 5600             // Pi listens on this port
```

Update `PI_IP` if your Pi's address is different.

### 5. Flash each ESP32

1. Plug in via USB
2. **Tools > Port** → select the USB COM port
3. Click Upload (→)
4. Open **Serial Monitor** at **115200 baud**

You should see:
```
Connecting to WiFi....
Connected. IP: 192.168.1.xxx  ->  192.168.1.81:5600
CSI collector running
```

If it sits at `Connecting to WiFi.....` forever, double-check `secrets.h`.

Repeat for all three ESP32s.

### 6. Power for deployment

Once flashed, each ESP32 just needs USB power — plug into a phone charger or power bank anywhere in the room. No PC or Pi connection needed.

---

## Running the Pi Streamer

On the Pi:

```bash
cd ~/wifi-csi-detector
source venv/bin/activate
pip install -r pi/requirements.txt   # first time only
python -m pi.capture.csi_streamer
```

The streamer listens on UDP port 5600 and accepts connections from any ESP32 automatically — no configuration needed when adding or removing boards.

As a systemd service:
```bash
sudo systemctl start wifi-csi-capture
sudo journalctl -u wifi-csi-capture -f
```

---

## Preflight Check

```bash
bash pi/scripts/preflight_check.sh
```

---

## Architecture

```
[Router / ambient WiFi traffic on channel 1]
         |
    (captured by all three ESP32s)
         |
[ESP32 #1] --WiFi UDP--> |              |
[ESP32 #2] --WiFi UDP--> | Pi :5600     | --UDP--> PC :5500
[ESP32 #3] --WiFi UDP--> |              |
```

No dedicated traffic generator needed. The ESP32s run in promiscuous mode and
capture CSI from any WiFi frame on the channel — router beacons (~10 Hz),
phone traffic, background app traffic, etc. Three receivers gives good spatial
coverage for location-aware motion detection.

All three ESP32s send to the same Pi port. The Pi tells them apart by source IP
and maintains separate timestamp unwrappers per device. Frames from all devices
are merged into one stream forwarded to the PC.

---

## Known Issues Fixed During Setup

| Problem | Fix |
|---|---|
| `wifi_pkt_rx_ctrl_t has no member 'sequence'` | Field doesn't exist in SDK 2.0.x — replaced with static counter |
| `wifi_pkt_rx_ctrl_t has no member 'smoothing_not_apply'` | Renamed to `smoothing` in this SDK version |
| Serial Monitor shows boxes for CSI values | `Serial.print((int8_t)val)` prints raw char — cast to `int` first |
| No CSI data — startup message visible but no CSI_DATA lines | Need `esp_wifi_set_promiscuous(true)` to capture without connecting to AP |
| No CSI data after promiscuous mode | Router on channel 1, sketch defaulted to channel 6 |
