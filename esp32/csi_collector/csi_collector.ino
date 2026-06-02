// WiFi CSI collector for ESP32-WROOM-32D — outputs ESP32-CSI-Tool format over serial
#include "Arduino.h"
#include "WiFi.h"
#include "esp_wifi.h"
#include "esp_wifi_types.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "config.h"

void IRAM_ATTR csi_callback(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf) return;

    static uint32_t seq = 0;
    seq++;

    char mac_str[18];
    snprintf(mac_str, sizeof(mac_str), "%02X:%02X:%02X:%02X:%02X:%02X",
             info->mac[0], info->mac[1], info->mac[2],
             info->mac[3], info->mac[4], info->mac[5]);

    uint32_t ts = (uint32_t)esp_timer_get_time();

    Serial.printf("CSI_DATA,%u,%s,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%u,%d,%d,%d,%d,%d,[",
        seq,
        mac_str,
        info->rx_ctrl.rssi,
        info->rx_ctrl.rate,
        info->rx_ctrl.sig_mode,
        info->rx_ctrl.mcs,
        info->rx_ctrl.cwb,
        info->rx_ctrl.smoothing,
        info->rx_ctrl.not_sounding,
        info->rx_ctrl.aggregation,
        info->rx_ctrl.stbc,
        info->rx_ctrl.fec_coding,
        info->rx_ctrl.sgi,
        info->rx_ctrl.noise_floor,
        info->rx_ctrl.ampdu_cnt,
        info->rx_ctrl.channel,
        info->rx_ctrl.secondary_channel,
        ts,
        info->rx_ctrl.ant,
        info->rx_ctrl.sig_len,
        info->rx_ctrl.rx_state,
        info->len,
        info->first_word_invalid ? 1 : 0
    );

    for (int i = 0; i < info->len; i++) {
        if (i > 0) Serial.print(",");
        Serial.print((int)(int8_t)info->buf[i]);
    }

    Serial.print("]\n");
}

void setup() {
    Serial.begin(SERIAL_BAUD);

    nvs_flash_init();
    WiFi.mode(WIFI_STA);
    esp_wifi_start();
    esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);

    esp_wifi_set_promiscuous(true);

    wifi_csi_config_t csi_config = {};
    csi_config.lltf_en           = ENABLE_LLTF;
    csi_config.htltf_en          = ENABLE_HTLTF;
    csi_config.stbc_htltf2_en    = ENABLE_STBC;
    csi_config.ltf_merge_en      = true;
    csi_config.channel_filter_en = true;
    csi_config.manu_scale        = false;
    esp_wifi_set_csi_config(&csi_config);

    esp_wifi_set_csi_rx_cb(csi_callback, NULL);
    esp_wifi_set_csi(true);

    Serial.printf("CSI collector started on channel %d\n", CHANNEL);
}

void loop() {
    delay(1000);
}
