# Troubleshooting Guide

## 1. Pi Not Streaming

**Symptom**: PC receives 0 frames, `tcpdump` shows nothing.

**Diagnostics**:
```bash
# Check Nexmon module
lsmod | grep brcmfmac

# Check monitor mode
iw dev wlan0 info

# Check service status
systemctl status wifi-csi-capture
journalctl -u wifi-csi-capture -n 50

# Check Nexmon socket (Pi itself receives CSI?)
sudo tcpdump -i lo udp port 5500 -c 5
```

**Fixes**:
```bash
# Reload module
sudo modprobe -r brcmfmac && sudo modprobe brcmfmac

# Restart monitor config
sudo wifi-csi-monitor

# Restart service
sudo systemctl restart wifi-csi-capture
```

---

## 2. PC Receiving 0 Frames

**Symptom**: `rate_hz=0.0` in ingestion stats despite Pi streaming.

**Diagnostics**:
```bash
# Check firewall
sudo ufw status
# Windows:
Get-NetFirewallRule -DisplayName "CSI*" | Select-Object DisplayName,Enabled,Action

# Check socket is bound
ss -ulnp | grep 5500
# Windows:
netstat -an | findstr "5500"

# Sniff on PC Ethernet
sudo tcpdump -i eth0 udp port 5500 -c 10

# Verify routes
ip route | grep 192.168.2
ping 192.168.2.50
```

**Fixes**:
```bash
# Increase socket buffer (Linux)
sudo sysctl -w net.core.rmem_max=67108864
sudo sysctl -w net.core.rmem_default=67108864

# Add to /etc/sysctl.conf for persistence:
echo "net.core.rmem_max=67108864" | sudo tee -a /etc/sysctl.conf
```

---

## 3. High Packet Loss

**Symptom**: `dropped_frames` climbing, `seq_gaps` > 1%.

**Diagnostics**:
```bash
# CPU load on Pi
top -n 1 -b | head -5

# Network stats
ip -s link show eth0 | grep -A2 RX

# Socket buffer actually set?
python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4194304); print(s.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF))"
```

**Fixes**:
- Increase `ring_buffer_size` in `config.yaml` (try 1000)
- Reduce DSP `window_stride` to process less frequently
- Check Pi CPU isn't overloaded (temp throttling)
- Verify Ethernet cable quality (try `ethtool eth0 | grep Speed`)

---

## 4. Motion Not Detected

**Symptom**: Walking past Pi produces no motion_start events.

**Diagnostics**:
```bash
# Check motion score in real-time
python -m pc.main --no-viz 2>&1 | grep "score="

# Run signal integrity check (capture 10s first)
python -m pc.main --no-viz --mode replay --replay-file capture.csi
```

**Fixes**:
1. Lower `detection.motion_threshold` in config (try 0.10)
2. Disable adaptive threshold to use static: `adaptive_threshold: false`
3. Reset baseline: send SIGTERM and restart (baseline was collected during motion)
4. Verify laptop is transmitting: check transmitter logs for `actual_rate`
5. Verify Pi is capturing: `curl http://192.168.2.50:8080/health`

---

## 5. False Positives

**Symptom**: Motion events when room is empty (AC, fans, vibration).

**Diagnostics**:
- Plot motion scores in web UI — look for periodic patterns (AC = 0.5-2 Hz)
- Check `max_score_spike_ratio` — are spikes causing triggers?

**Fixes**:
1. Let adaptive threshold run for 2+ minutes before testing
2. Increase `detection.motion_confirm_frames` (try 5-10)
3. Increase `detection.min_motion_duration_ms` (try 500)
4. Lower `dsp.bandwidth_variance` frequency band (try 1.0 - 5.0 Hz to avoid AC)
5. Increase `detection.adaptive_k` (try 4.0 or 5.0)

---

## 6. High DSP Latency

**Symptom**: `processing_time_ms` > 10ms in DSP stats.

**Diagnostics**:
```bash
cd wifi-csi-detector
python -m tests.benchmarks.latency_bench --n-frames 1000 --report
```

**Fixes**:
- Reduce `dsp.window_size` (try 30)
- Reduce `dsp.hampel_window` (try 3)
- Install scipy (dramatically faster filtering): `pip install scipy`
- Check if numpy uses optimized BLAS: `python -c "import numpy; numpy.show_config()"`

---

## 7. Nexmon Module Fails to Load

**Symptom**: `modprobe brcmfmac` fails; `dmesg` shows errors.

**Diagnostics**:
```bash
uname -r                         # kernel version
ls /lib/modules/$(uname -r)/     # check module directory
dmesg | grep brcmfmac | tail -20
```

**Fixes**:
```bash
# Rebuild for current kernel
cd /opt/nexmon_csi
make clean
source /opt/nexmon/setup_env.sh
make install-firmware

# If kernel was updated, reinstall headers first
sudo apt-get install --reinstall raspberrypi-kernel-headers
```

> Nexmon CSI is kernel-version specific. After `sudo apt-get upgrade`, you may need to rebuild.
