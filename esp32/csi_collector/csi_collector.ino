// WiFi CSI collector for ESP32-WROOM-32D — sends CSI over UDP to Pi
#include "Arduino.h"
#include "WiFi.h"
#include "WiFiUdp.h"
#include "esp_wifi.h"
#include "esp_wifi_types.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "config.h"

#define LINE_MAX    1024
#define QUEUE_DEPTH 12

typedef char CsiLine[LINE_MAX];

static QueueHandle_t s_queue;
static WiFiUDP       s_udp;

void IRAM_ATTR csi_callback(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf) return;

    static uint32_t seq = 0;
    seq++;

    char mac_str[18];
    snprintf(mac_str, sizeof(mac_str), "%02X:%02X:%02X:%02X:%02X:%02X",
             info->mac[0], info->mac[1], info->mac[2],
             info->mac[3], info->mac[4], info->mac[5]);

    uint32_t ts = (uint32_t)esp_timer_get_time();

    static CsiLine line;
    int pos = snprintf(line, LINE_MAX,
        "CSI_DATA,%u,%s,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%u,%d,%d,%d,%d,%d,[",
        seq, mac_str,
        info->rx_ctrl.rssi, info->rx_ctrl.rate, info->rx_ctrl.sig_mode,
        info->rx_ctrl.mcs, info->rx_ctrl.cwb, info->rx_ctrl.smoothing,
        info->rx_ctrl.not_sounding, info->rx_ctrl.aggregation,
        info->rx_ctrl.stbc, info->rx_ctrl.fec_coding, info->rx_ctrl.sgi,
        info->rx_ctrl.noise_floor, info->rx_ctrl.ampdu_cnt,
        info->rx_ctrl.channel, info->rx_ctrl.secondary_channel,
        ts, info->rx_ctrl.ant, info->rx_ctrl.sig_len, info->rx_ctrl.rx_state,
        info->len, info->first_word_invalid ? 1 : 0);

    for (int i = 0; i < info->len && pos < LINE_MAX - 10; i++) {
        if (i > 0) line[pos++] = ',';
        pos += snprintf(line + pos, LINE_MAX - pos, "%d", (int)(int8_t)info->buf[i]);
    }

    if (pos < LINE_MAX - 2) {
        line[pos++] = ']';
        line[pos++] = '\n';
        line[pos]   = '\0';
    }

    xQueueSend(s_queue, &line, 0);
}

static void sender_task(void *param) {
    CsiLine line;
    while (true) {
        if (xQueueReceive(s_queue, &line, pdMS_TO_TICKS(1000)) != pdTRUE) continue;
        if (WiFi.status() != WL_CONNECTED) continue;
        s_udp.beginPacket(PI_IP, UDP_PORT);
        s_udp.write((const uint8_t *)line, strlen(line));
        s_udp.endPacket();
    }
}

static void connect_wifi() {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print("Connecting to WiFi");
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.printf("\nConnected. IP: %s  ->  %s:%d\n",
                  WiFi.localIP().toString().c_str(), PI_IP, UDP_PORT);
}

void setup() {
    Serial.begin(115200);
    nvs_flash_init();

    s_queue = xQueueCreate(QUEUE_DEPTH, sizeof(CsiLine));
    xTaskCreatePinnedToCore(sender_task, "udp_sender", 8192, NULL, 5, NULL, 1);

    connect_wifi();

    esp_wifi_set_promiscuous(true);

    wifi_csi_config_t csi_cfg = {};
    csi_cfg.lltf_en           = ENABLE_LLTF;
    csi_cfg.htltf_en          = ENABLE_HTLTF;
    csi_cfg.stbc_htltf2_en    = ENABLE_STBC;
    csi_cfg.ltf_merge_en      = true;
    csi_cfg.channel_filter_en = true;
    csi_cfg.manu_scale        = false;
    esp_wifi_set_csi_config(&csi_cfg);
    esp_wifi_set_csi_rx_cb(csi_callback, NULL);
    esp_wifi_set_csi(true);

    Serial.println("CSI collector running");
}

void loop() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi lost, reconnecting...");
        WiFi.reconnect();
        delay(5000);
    }
    delay(1000);
}
