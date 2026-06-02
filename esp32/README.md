# ESP32 CSI Collector Setup

Switched from Nexmon (Pi firmware patching) to two ESP32-WROOM-32D boards as passive CSI receivers. Nexmon was abandoned due to a b43 assembler incompatibility on kernel 6.12.x — the `enable_carrier_search` label was missing from the disassembled ucode and there was no clean fix.

Each ESP32 connects to the Pi via USB and streams CSI data over serial at 921600 baud. The Pi reads both serial streams and forwards them to the PC over UDP.

---

## Hardware

- 2x Inland ESP32-WROOM-32D (Micro Center, ~$8 each)
- 2x USB-A to Micro-USB cables
- Both plug into the Pi's USB ports

---

## Arduino IDE Setup

### 1. Install the ESP32 board package

1. Open Arduino IDE 2.x
2. **File > Preferences** → add this URL to "Additional boards manager URLs":
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. **Tools > Board > Boards Manager** → search `esp32` → install **esp32 by Espressif Systems version 2.0.15**
   - Do NOT install 3.x — the CSI API changed and will break the sketch
   - Do NOT install "Arduino ESP32 Boards" by Arduino — that's a different package

### 2. Select the board

**Tools > Board > ESP32 Arduino > ESP32 Dev Module**

Set port to whichever COM port shows "(USB)" next to it.

### 3. Open the sketch

**File > Open** → navigate to `esp32/csi_collector/csi_collector.ino`

### 4. Check config.h

```c
#define CHANNEL     1       // must match your router's 2.4 GHz channel
#define SERIAL_BAUD 921600
```

Find your router's channel: on Windows run `netsh wlan show all` and look for the Channel line of your connected network. Update `CHANNEL` to match if needed.

### 5. Flash

Click Upload (→). After it finishes, press the **EN** button on the board to reboot.

Open **Tools > Serial Monitor** at **921600 baud**. You should see:
```
CSI collector started on channel 1
CSI_DATA,1,AA:BB:CC:DD:EE:FF,-65,...,[1,-2,3,...]
```

Repeat for the second ESP32.

---

## Connecting to the Pi

Plug both ESP32s into the Pi's USB ports. They will appear as:
- `/dev/ttyUSB0` — ESP32 #1
- `/dev/ttyUSB1` — ESP32 #2

If the Pi doesn't recognize them, the `dialout` group permission is needed:
```bash
sudo usermod -aG dialout $USER
# log out and back in
```

---

## Running the Streamer

On the Pi:

```bash
cd ~/wifi-csi-detector
source venv/bin/activate
pip install -r pi/requirements.txt   # first time only: adds pyserial + numpy
python -m pi.capture.csi_streamer --port1 /dev/ttyUSB0 --port2 /dev/ttyUSB1
```

Or if running as a systemd service:
```bash
sudo systemctl start wifi-csi-capture
sudo journalctl -u wifi-csi-capture -f
```

---

## Preflight Check

```bash
bash pi/scripts/preflight_check.sh
```

Checks that both ESP32 devices are present, config file exists, and Python dependencies are installed.

---

## Known Issues Fixed During Setup

| Problem | Fix |
|---|---|
| `wifi_pkt_rx_ctrl_t has no member 'sequence'` | Field doesn't exist in SDK 2.0.x — replaced with a static counter |
| `wifi_pkt_rx_ctrl_t has no member 'smoothing_not_apply'` | Renamed to `smoothing` in this SDK version |
| Serial Monitor shows boxes/garbage for CSI values | `Serial.print((int8_t)val)` prints raw char bytes — fixed by casting to `int` first |
| Serial Monitor blank after flash | Startup message fires before monitor opens — press EN button to reboot |
| No CSI data (startup message visible but no CSI_DATA lines) | ESP32 needs promiscuous mode enabled to capture frames without connecting to an AP — added `esp_wifi_set_promiscuous(true)` |
| No CSI data after enabling promiscuous mode | Router was on channel 1, sketch defaulted to channel 6 — updated `config.h` and `config.yaml` |
