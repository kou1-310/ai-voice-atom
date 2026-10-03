#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <M5Unified.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <base64.h>             // base64::encode() — Arduino-ESP32 内蔵
#include <esp32-hal-rgb-led.h>  // neopixelWrite() — Arduino-ESP32 内蔵の WS2812 ドライバ
#include <sys/stat.h>

// モード切替の音声案内（host/scripts/generate_mode_prompts.py が生成する。コミットしない）。
// 未生成でもビルドでき、その場合は従来のチャイムで代用する。
#if __has_include("mode_prompts.h")
#include "mode_prompts.h"
#endif

// ファームが参照する案内音声の版数（generate_mode_prompts.py の PROMPTS_VERSION と揃える）。
// 古い版のヘッダは案内が足りずビルドできないので使わず、チャイムで代用する。
#define REQUIRED_PROMPTS_VERSION 2
#if defined(MODE_PROMPTS_AVAILABLE) && defined(MODE_PROMPTS_VERSION) && \
    MODE_PROMPTS_VERSION >= REQUIRED_PROMPTS_VERSION
#define PROMPTS_USABLE 1
#else
#define PROMPTS_USABLE 0
#if defined(MODE_PROMPTS_AVAILABLE)
#warning "mode_prompts.h が古い版です。host/scripts/generate_mode_prompts.py で再生成してください（それまではチャイムで代用）"
#endif
#endif

namespace {

#ifndef WIFI_SSID
#define WIFI_SSID ""
#endif

#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD ""
#endif

// 固定IP設定（空の場合は DHCP で接続する）
#ifndef WIFI_STATIC_IP
#define WIFI_STATIC_IP ""
#endif

#ifndef WIFI_GATEWAY
#define WIFI_GATEWAY ""
#endif

#ifndef WIFI_SUBNET
#define WIFI_SUBNET ""
#endif

#ifndef WIFI_DNS
#define WIFI_DNS ""
#endif

#ifndef HOST_BASE_URL
#define HOST_BASE_URL "http://192.168.1.10:8000"
#endif

// デバイス識別子。.env の DEVICE_ID で上書き可能。複数台運用時は機体ごとに変える。
// ホスト側がこの ID でペルソナ・声色・会話履歴を出し分ける。
#ifndef DEVICE_ID
#define DEVICE_ID "atoms3-001"
#endif

// スピーカー音量 (0-255)。.env の SPEAKER_VOLUME で上書き可能。
#ifndef SPEAKER_VOLUME
#define SPEAKER_VOLUME 100
#endif

// オウム返しモードが声とみなす音量（100ms ごとの RMS）の下限。.env の PARROT_MIN_RMS で上書き可能。
// 静かな部屋で勝手に反応するなら上げ、話しても反応しないなら下げる（シリアルの [parrot] rms= を参考に）。
#ifndef PARROT_MIN_RMS
#define PARROT_MIN_RMS 400
#endif

// ---- constants ----

constexpr char kModeWeb[]   = "web";
constexpr char kModeVoice[] = "voice";
constexpr char kModeRelay[] = "relay";  // ホスト統括リレー（2台会話）。ホストから play を受けて再生する。
constexpr char kModeRecorder[] = "recorder";  // 単体動作の録音再生（Wi-Fi・ホスト不要）。
constexpr char kModeParrot[] = "parrot";      // 単体動作のオウム返し（話しかけると自動で言い返す）。
constexpr char kModeTimer[]  = "timer";       // 単体動作の声で知らせるタイマー。

constexpr uint32_t kColorWebHealthy   = 0x0000ff;
constexpr uint32_t kColorVoiceHealthy = 0x00ff00;
constexpr uint32_t kColorRelayHealthy = 0x00ffff;  // リレー待機（シアン）
constexpr uint32_t kColorConnecting   = 0xffa500;
constexpr uint32_t kColorPlaying      = 0xff00ff;  // 音声再生中（待機の青とはっきり区別できるマゼンタ）
constexpr uint32_t kColorBackendError = 0xff0000;
constexpr uint32_t kColorConfigMissing = 0x220022;

constexpr uint32_t kColorRecording    = 0x00ff00;  // 録音中の点滅色（緑）
constexpr uint32_t kColorRecorderIdle = 0xffffff;  // 録音再生モード待機（白）
constexpr uint32_t kColorRecorderRec  = 0xff0000;  // 録音再生モードの録音中の点滅色（赤）
constexpr uint32_t kColorParrot       = 0xffc800;  // オウム返し（黄）。聞き取り中は点灯、録音中は点滅
constexpr uint32_t kColorTimerIdle    = 0x8000ff;  // タイマー待機（紫）

constexpr unsigned long kWifiRetryIntervalMs   = 10000;
constexpr unsigned long kHealthCheckIntervalMs = 15000;
// ホストに届かない間のヘルスチェック間隔。確認中は loop() が止まってボタンが効かないので間引く。
constexpr unsigned long kHealthCheckDownIntervalMs = 30000;
// ヘルスチェックの接続待ち上限。既定（5 秒）のままだと、ホスト PC の電源が落ちているだけで
// 確認のたびに 5 秒固まり、その間のクリック・長押しを取りこぼす。LAN 内なら 1.5 秒で足りる。
constexpr int32_t kHealthConnectTimeoutMs = 1500;
constexpr unsigned long kLongPressMs           = 1200;

// 1 チャンクの録音サンプル数（16kHz で 100ms 相当）。
constexpr size_t kVoiceChunkSamples      = 1600;
// stt.final を待つ最大時間。
constexpr unsigned long kSttWaitMs       = 12000;
// 録音開始時に WebSocket 接続確立を待つ最大時間（自動再接続間隔 3s を跨げる長さ）。
constexpr unsigned long kWsConnectWaitMs = 4000;

// ---- recorder（単体録音再生）----
// 録音は RAM に溜めず（PSRAM 無し）LittleFS（spiffs パーティション 1.5MB）へ逐次書き込む。
// 形式はヘッダ無しの 16kHz / mono / 16-bit PCM。電源を切っても残る。
constexpr char kRecPath[] = "/rec.pcm";
constexpr uint32_t kRecSampleRate = 16000;
constexpr uint32_t kRecMaxSeconds = 30;
// 空き容量ぎりぎりまで書くと LittleFS のメタデータ更新が失敗しうるので余白を残す。
constexpr size_t kRecFsMarginBytes = 32 * 1024;
// 録音中の書き込み先。kRecMinKeepBytes 以上録れてから kRecPath と入れ替える。
constexpr char kRecTmpPath[] = "/rec.tmp";
// これより短い録音（0.5 秒未満）は誤操作とみなして捨て、前の録音を残す。遅いダブルクリックが
// 「録音開始 → すぐ停止」と判定されても、前の録音を一瞬の録音で上書きしないため。
constexpr uint32_t kRecMinKeepBytes = kRecSampleRate * sizeof(int16_t) / 2;
// ダブルクリック判定窓: 1 回目を離してから 2 回目を押すまでの上限。2 回目の「押し」で確定するので、
// 押している長さは窓に含まれない。M5Unified 標準判定は長押し閾値(1.2s)を待つため自前で判定する。
constexpr unsigned long kDoubleClickWindowMs = 500;
// 再生開始からこの間はボタンを見ない（再生を始めたクリック自体のチャタリングで止めないため）。
constexpr unsigned long kPlayStopGuardMs = 150;

// ---- parrot（オウム返し）----
// マイクで聞き続け、音量がしきい値を超えたら直前の数チャンクを含めて LittleFS へ録音し、
// 静かになったら止めて、再生サンプルレートを変えた「変な声」で言い返す。
constexpr char kParrotPath[] = "/parrot.pcm";
constexpr uint32_t kParrotMaxBytes = 10 * kRecSampleRate * sizeof(int16_t);  // 最大 10 秒
constexpr int kParrotPreRollChunks = 2;               // 話し始めを切らないよう直前 200ms を残す
constexpr unsigned long kParrotSilenceEndMs = 700;   // これだけ静かなら言い終わりとみなす
constexpr unsigned long kParrotMinVoicedMs  = 300;   // これより短い有音は雑音として捨てる
constexpr unsigned long kParrotGuardMs      = 300;   // 聞き取り再開直後は残響・切替ノイズを捨てる
constexpr unsigned long kParrotTailMs       = 200;   // 言い終わり後に残す余韻
constexpr float kParrotNoiseFactor  = 3.0f;          // しきい値 = 雑音レベル × これ（下限 PARROT_MIN_RMS）
constexpr float kParrotReleaseRatio = 0.6f;          // 録音中はしきい値のこの割合まで下がっても有音扱い
// 声の種類 = 再生サンプルレート。高いほど速く高い声になる。
constexpr uint32_t kParrotVoiceRates[] = {22400, 16000, 11500};  // 高い声 / ふつう / 低い声
constexpr int kParrotVoiceCount = sizeof(kParrotVoiceRates) / sizeof(kParrotVoiceRates[0]);

// ---- timer（声で知らせるタイマー）----
constexpr uint8_t kTimerPresetsMin[] = {1, 3, 5, 10, 25};
constexpr int kTimerPresetCount = sizeof(kTimerPresetsMin) / sizeof(kTimerPresetsMin[0]);
constexpr unsigned long kTimerAlarmIntervalMs = 4000;  // 「時間ですわ！」を繰り返す間隔
constexpr int kTimerAlarmMaxRepeats = 8;               // 止められなくても約 30 秒で鳴り止む

// ---- state ----

const char* currentMode = kModeWeb;
bool wifiConnected  = false;
bool backendHealthy = false;
unsigned long lastWifiAttemptMs   = 0;
unsigned long lastHealthCheckMs   = 0;
bool wifiAttempted  = false;  // 起動後に Wi-Fi 接続を試みたか（初回は再試行間隔を待たずに試す）
bool healthCheckDue = false;  // 次の loop() ですぐヘルスチェックする（Wi-Fi 接続直後・WS 切断直後）
unsigned long lastHeartbeatMs     = 0;
constexpr unsigned long kHeartbeatIntervalMs = 10000;

// ---- voice (WebSocket + mic) state ----

WebSocketsClient wsClient;
bool wsBegun     = false;   // wsClient.begin() 済み（以降は毎ループ loop() を回す）
bool wsConnected = false;   // WebSocket 接続確立中
bool recording   = false;   // 録音中（voice モードでクリック・トグル）
bool holdConsumed = false;  // 長押しでモード切替済み。直後の離しをクリック扱いしないためのフラグ。
String wsSessionId;
volatile bool sttReceived  = false;
String sttText;

// ---- relay（ホスト統括リレー）state ----
// ホストから push された play コマンドを onWsEvent が受け、loop() で処理する
// （WS コールバック内で HTTP/再生をブロックさせないため、フラグ越しに受け渡す）。
volatile bool relayPlayPending = false;
String relayPlayText;
String relayPlayPersona;  // 声色を借りるペルソナ（実機1台で2体を鳴らすとき自分以外になる）
int relayPlayTurn = 0;

// ---- recorder state ----
bool fsReady = false;          // LittleFS をマウントできたか
File recFile;                  // 録音中の書き込み先
uint32_t recBytes = 0;         // 今回の録音で書いたバイト数
uint32_t recMaxBytes = 0;      // 今回の録音の上限（最大秒数と空き容量の小さい方）
bool recOldRemoved = false;    // 前の録音（kRecPath）を消したか（今回の録音が十分な長さになったら消す）
bool clickPending = false;     // 1 回目のクリック後、ダブルクリック判定窓の中にいる
unsigned long clickPendingMs = 0;
bool doubleClickArmed = false; // 判定窓の中で 2 回目が押された（離したらダブルクリックとして処理する）

// モードを NVS に保存し、次回起動時に復元する（単体で遊ぶ人が毎回切り替えずに済むように）。
Preferences prefs;

// ---- parrot state ----
bool parrotActive = false;       // マイクで聞き取り中（parrot モードで録音パイプライン稼働中）
bool parrotCapturing = false;    // 声を検出して録音中
int parrotVoice = 0;             // kParrotVoiceRates の添字
float parrotNoise = 0.0f;        // 雑音レベル（RMS の指数移動平均）
File parrotFile;
uint32_t parrotBytes = 0;        // 今回の録音で書いたバイト数
uint32_t parrotVoicedBytes = 0;  // 最後に有音だった位置（ここ + 余韻までを再生する）
unsigned long parrotVoicedMs = 0;
unsigned long parrotSilenceMs = 0;
unsigned long parrotGuardUntil = 0;
unsigned long parrotLastLogMs = 0;
int16_t parrotPreRoll[kParrotPreRollChunks][kVoiceChunkSamples];
int parrotPreRollCount = 0;
int parrotPreRollIdx = 0;        // 次に書き込むスロット

// ---- timer state ----
int timerPreset = 1;             // kTimerPresetsMin の添字（既定 3 分）
bool timerRunning = false;
unsigned long timerStartMs = 0;
unsigned long timerDurationMs = 0;
bool timerOneMinuteDone = false;
bool timerAlarm = false;         // 時間になって鳴らしている
int timerAlarmCount = 0;
unsigned long timerNextAlarmMs = 0;

// ---- ギャップレス録音パイプライン ----
// マイクタスクを止めないため、常に複数の録音ジョブをキューに積んでおく。3 枚の
// バッファを巡回し、「次を record() で積む → その時点で完了が確定した 2 つ前の
// バッファを送信」を繰り返す。これでチャンク送信(base64/WS)の間も I2S が連続して
// 読み出され、録音の取りこぼし（音声の断片化）が起きない。
constexpr int kCapBuffers = 3;
int16_t capBuf[kCapBuffers][kVoiceChunkSamples];
int capQueueIdx = 0;  // 次に record() で積むバッファ
int capSendIdx  = 0;  // 次に送信する（完了が確定した）バッファ

// 再生用トリプルバッファ（ストリーム再生・録音再生で共用）。playRaw のキューは
// 1 チャンネルあたり 2 スロットなので、3 面を巡回すれば再生中バッファを上書きしない。
constexpr size_t kPlayBufSamples = 1024;
int16_t playBuf[3][kPlayBufSamples];

// ---- LED ----

// AtomS3-Lite の内蔵 RGB LED (WS2812B-2020) は GPIO35。
// M5Unified のボード検出に依存すると（非Liteの AtomS3 等に誤検出された場合）
// M5.Led が無反応になるため、Arduino-ESP32 内蔵の neopixelWrite() で GPIO35 を
// 直接駆動する。M5.Led を一切呼ばないことで M5Unified 側が GPIO35 の RMT を
// 確保せず、RMT ドライバ競合も回避できる。
constexpr int kAtomS3LedPin = 35;

// 輝度 (0-255)。WS2812 は最大輝度だと眩しいので控えめにする。
constexpr uint8_t kLedBrightness = 60;

void setLedColor(uint32_t color) {
  // 同じ色の書き込みは省く（録音中は loop() が 1ms 周期で回るので、毎回 LED を駆動しない）。
  static uint32_t lastColor = 0xffffffff;
  if (color == lastColor) return;
  lastColor = color;

  const uint8_t r = (color >> 16) & 0xFF;
  const uint8_t g = (color >> 8) & 0xFF;
  const uint8_t b = color & 0xFF;
  // 輝度スケールを掛けてから出力する
  neopixelWrite(kAtomS3LedPin,
                (r * kLedBrightness) / 255,
                (g * kLedBrightness) / 255,
                (b * kLedBrightness) / 255);
}

bool hasWifiConfig() {
  return WIFI_SSID[0] != '\0';
}

// USB の先に PC（USB ホスト）が居るか。充電器・モバイルバッテリー給電では false。
bool usbHostPresent() {
#if ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
  return HWCDC::isPlugged();
#else
  return true;
#endif
}

const char* resetReasonName(esp_reset_reason_t reason) {
  switch (reason) {
    case ESP_RST_POWERON:  return "power-on";
    case ESP_RST_SW:       return "software";
    case ESP_RST_PANIC:    return "panic";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:      return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout";
    default:               return "other";
  }
}

// ファームの異常終了（例外・ウォッチドッグ）によるリセットか。
bool isCrashReset(esp_reset_reason_t reason) {
  return reason == ESP_RST_PANIC || reason == ESP_RST_INT_WDT || reason == ESP_RST_TASK_WDT ||
         reason == ESP_RST_WDT;
}

// ホスト・Wi-Fi を使わない単体モードか。
bool isStandaloneMode(const char* mode) {
  return mode == kModeRecorder || mode == kModeParrot || mode == kModeTimer;
}

// タイマー動作中の LED。残りの割合で緑 → 黄 → 赤、1 秒ごとに短く消えて動作中を示す。
uint32_t timerLedColor() {
  const unsigned long now = millis();
  if (timerAlarm) return ((now / 250) % 2) ? 0xff0000 : 0xffffff;
  if (!timerRunning) return kColorTimerIdle;
  if (now % 1000 >= 900) return 0x000000;
  const unsigned long elapsed = now - timerStartMs;
  const float remaining = 1.0f - (float)elapsed / (float)timerDurationMs;
  if (remaining > 0.5f) return 0x00ff00;
  if (remaining > 0.2f) return 0xffff00;
  return 0xff0000;
}

void updateLedForState() {
  // 単体モードは Wi-Fi・ホストの状態を表示しない。
  if (currentMode == kModeRecorder) { setLedColor(kColorRecorderIdle); return; }
  if (currentMode == kModeParrot) {
    const bool blink = parrotCapturing && (millis() / 200) % 2;
    setLedColor(blink ? 0x000000 : kColorParrot);
    return;
  }
  if (currentMode == kModeTimer) { setLedColor(timerLedColor()); return; }
  if (!hasWifiConfig()) { setLedColor(kColorConfigMissing); return; }
  if (!wifiConnected)   { setLedColor(kColorConnecting);   return; }
  if (!backendHealthy)  { setLedColor(kColorBackendError); return; }
  if (currentMode == kModeVoice) { setLedColor(kColorVoiceHealthy); return; }
  if (currentMode == kModeRelay) { setLedColor(kColorRelayHealthy); return; }
  setLedColor(kColorWebHealthy);
}

// ---- 効果音・モード切替 ----

// モード移行のフィードバック音。voice は高めの 2 音、web は低めの 1 音で区別する。
// tone() はデフォルトで再生中を止めて鳴らすため、短い delay でキューが流れるのを待つ。
void playModeChime(const char* mode) {
  if (mode == kModeVoice) {
    M5.Speaker.tone(880, 120);
    delay(150);
    M5.Speaker.tone(1320, 140);
    delay(150);
  } else if (mode == kModeRelay) {
    // リレーは上昇3音で web/voice と区別する。
    M5.Speaker.tone(660, 110);
    delay(140);
    M5.Speaker.tone(990, 110);
    delay(140);
    M5.Speaker.tone(1320, 130);
    delay(150);
  } else if (mode == kModeRecorder) {
    // 録音再生は下降2音。
    M5.Speaker.tone(1320, 110);
    delay(140);
    M5.Speaker.tone(660, 140);
    delay(150);
  } else if (mode == kModeParrot) {
    // オウム返しは高い短音3回（さえずり）。
    for (int i = 0; i < 3; ++i) {
      M5.Speaker.tone(1760, 60);
      delay(90);
    }
  } else if (mode == kModeTimer) {
    // タイマーは同じ高さの2音（チッ、タッ）。
    M5.Speaker.tone(1000, 80);
    delay(300);
    M5.Speaker.tone(1000, 80);
    delay(120);
  } else {
    M5.Speaker.tone(440, 200);
    delay(220);
  }
}

// 埋め込み音声（16kHz/mono/16-bit WAV）。ヘッダ未生成なら空になり、呼び出し側がチャイムで代用する。
struct Prompt {
  const uint8_t* data;
  size_t size;
};
#if PROMPTS_USABLE
#define PROMPT(name) (Prompt{name, sizeof(name)})
#else
#define PROMPT(name) (Prompt{nullptr, 0})
#endif

Prompt promptForMode(const char* mode) {
  if (mode == kModeVoice)    return PROMPT(kPromptModeVoice);
  if (mode == kModeRelay)    return PROMPT(kPromptModeRelay);
  if (mode == kModeRecorder) return PROMPT(kPromptModeRecorder);
  if (mode == kModeParrot)   return PROMPT(kPromptModeParrot);
  if (mode == kModeTimer)    return PROMPT(kPromptModeTimer);
  return PROMPT(kPromptModeWeb);
}

Prompt promptForParrotVoice(int voice) {
  if (voice == 0) return PROMPT(kPromptVoiceHigh);
  if (voice == 2) return PROMPT(kPromptVoiceLow);
  return PROMPT(kPromptVoiceNormal);
}

Prompt promptForTimerPreset(int preset) {
  switch (kTimerPresetsMin[preset]) {
    case 1:  return PROMPT(kPromptTimer1);
    case 3:  return PROMPT(kPromptTimer3);
    case 5:  return PROMPT(kPromptTimer5);
    case 10: return PROMPT(kPromptTimer10);
    default: return PROMPT(kPromptTimer25);
  }
}

// 埋め込み音声を最後まで再生する。無ければ false を返す。
// 長押し直後に呼ばれるので M5.update() は回さない（離しイベントを呼び出し元の loop() に残すため）。
bool playPrompt(const Prompt& p) {
  if (p.data == nullptr) return false;
  M5.Speaker.playWav(p.data, p.size, 1, -1, true);
  while (M5.Speaker.isPlaying()) delay(5);
  return true;
}

// モードを音声で案内する。案内音声が無ければ従来のチャイムを鳴らす。
void announceMode(const char* mode) {
  if (!playPrompt(promptForMode(mode))) playModeChime(mode);
}

// エラー時の断続音（低めの 3 連）。赤 LED とあわせて失敗を通知する。
void playErrorChime() {
  for (int i = 0; i < 3; ++i) {
    M5.Speaker.tone(300, 90);
    delay(130);
  }
}

const char* modeFromName(const String& name) {
  if (name == kModeVoice)    return kModeVoice;
  if (name == kModeRelay)    return kModeRelay;
  if (name == kModeRecorder) return kModeRecorder;
  if (name == kModeParrot)   return kModeParrot;
  if (name == kModeTimer)    return kModeTimer;
  return kModeWeb;
}

// モードに入る/出るときの後始末（定義は各モードの節の後）。
void enterMode(const char* mode);
void leaveMode(const char* mode);

// WS を閉じ、未開始の状態へ戻す。
void disconnectWs() {
  if (!wsBegun) return;
  wsClient.disconnect();
  wsBegun = false;
  wsConnected = false;
  Serial.println("[ws] closed");
}

// モードを web → voice → relay → recorder → parrot → timer → web の順に巡回し、LED と音声案内で通知する。
// web=HTTPチャット、voice=マイクPTT(音響ループ用)、relay=ホスト統括リレー(2台会話)、
// recorder=単体の録音再生、parrot=単体のオウム返し、timer=単体のタイマー。
void toggleMode() {
  leaveMode(currentMode);
  if (currentMode == kModeWeb) {
    currentMode = kModeVoice;
  } else if (currentMode == kModeVoice) {
    currentMode = kModeRelay;
  } else if (currentMode == kModeRelay) {
    currentMode = kModeRecorder;
  } else if (currentMode == kModeRecorder) {
    currentMode = kModeParrot;
  } else if (currentMode == kModeParrot) {
    currentMode = kModeTimer;
  } else {
    currentMode = kModeWeb;
  }
  // 単体モードでは WS を閉じる（ホスト不在時の再接続試行が録音やタイマーを妨げないように）。
  // relay/voice に戻れば必要なときに張り直される。
  if (isStandaloneMode(currentMode)) disconnectWs();
  prefs.putString("mode", currentMode);
  Serial.printf("[mode] switched to %s\n", currentMode);
  updateLedForState();
  announceMode(currentMode);
  enterMode(currentMode);
}

// ---- mDNS ----

// hostname.local を IP アドレスに解決して URL を書き換える
// .local でない場合はそのまま返す
String resolveMdnsUrl(const char* baseUrl) {
  String url(baseUrl);
  const int protoEnd = url.indexOf("://");
  if (protoEnd < 0) return url;

  const String proto = url.substring(0, protoEnd + 3);
  const String rest  = url.substring(protoEnd + 3);
  const int pathStart  = rest.indexOf('/');
  const String hostPort = (pathStart >= 0) ? rest.substring(0, pathStart) : rest;
  const String path     = (pathStart >= 0) ? rest.substring(pathStart)    : String("");

  const int colonPos = hostPort.lastIndexOf(':');
  const String host = (colonPos >= 0) ? hostPort.substring(0, colonPos) : hostPort;
  const String portPart = (colonPos >= 0) ? hostPort.substring(colonPos) : String("");

  if (!host.endsWith(".local")) return url;

  const String name = host.substring(0, host.length() - 6);
  const IPAddress ip = MDNS.queryHost(name, 2000);
  if (ip == IPAddress(0, 0, 0, 0)) {
    Serial.printf("[mdns] failed to resolve %s\n", host.c_str());
    return url;
  }
  Serial.printf("[mdns] %s -> %s\n", host.c_str(), ip.toString().c_str());
  return proto + ip.toString() + portPart + path;
}

// ---- Wi-Fi ----

bool hasStaticIpConfig() {
  return WIFI_STATIC_IP[0] != '\0';
}

// .env で指定された固定IPを WiFi.config() に適用する。
// IP/ゲートウェイが不正な場合は設定をスキップし DHCP にフォールバックする。
void applyStaticIpConfig() {
  IPAddress ip, gateway, subnet, dns;

  if (!ip.fromString(WIFI_STATIC_IP)) {
    Serial.printf("[wifi] invalid WIFI_STATIC_IP \"%s\" — falling back to DHCP\n", WIFI_STATIC_IP);
    return;
  }
  if (!gateway.fromString(WIFI_GATEWAY)) {
    Serial.printf("[wifi] invalid/empty WIFI_GATEWAY \"%s\" — falling back to DHCP\n", WIFI_GATEWAY);
    return;
  }
  // サブネットマスク未指定時は /24 (255.255.255.0) を既定とする
  if (!subnet.fromString(WIFI_SUBNET)) {
    subnet = IPAddress(255, 255, 255, 0);
  }
  // DNS未指定時はゲートウェイを使用する（mDNSはDNS不要だが通常のホスト名解決用）
  if (!dns.fromString(WIFI_DNS)) {
    dns = gateway;
  }

  if (WiFi.config(ip, gateway, subnet, dns)) {
    Serial.printf("[wifi] static IP configured: ip=%s gw=%s mask=%s dns=%s\n",
      ip.toString().c_str(), gateway.toString().c_str(),
      subnet.toString().c_str(), dns.toString().c_str());
  } else {
    Serial.println("[wifi] WiFi.config() failed — falling back to DHCP");
  }
}

void connectWiFiIfNeeded() {
  if (!hasWifiConfig()) {
    wifiConnected = false;
    backendHealthy = false;
    Serial.println("[wifi] no config — set WIFI_SSID/WIFI_PASSWORD in .env");
    return;
  }
  if (WiFi.status() == WL_CONNECTED) {
    if (!wifiConnected) {
      wifiConnected = true;
      healthCheckDue = true;  // つながったらすぐホストを確認する（次の定期確認まで待たせない）
      Serial.printf("[wifi] connected, IP: %s\n", WiFi.localIP().toString().c_str());
      // mDNS を起動（接続後に1回だけ）。複数台が同一ネットワークに居ても
      // ホスト名が衝突しないよう DEVICE_ID をレスポンダ名にする。
      if (MDNS.begin(DEVICE_ID)) {
        Serial.println("[mdns] responder started as " DEVICE_ID ".local");
      }
    }
    return;
  }

  // 電源を入れてすぐ使えるよう、初回だけは再試行間隔を待たずに接続を始める。
  const auto now = millis();
  if (wifiAttempted && now - lastWifiAttemptMs < kWifiRetryIntervalMs) return;

  wifiAttempted = true;
  lastWifiAttemptMs = now;
  wifiConnected = false;
  backendHealthy = false;
  Serial.printf("[wifi] connecting to \"%s\" ...\n", WIFI_SSID);
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_STA);
  if (hasStaticIpConfig()) applyStaticIpConfig();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void checkBackendHealthIfNeeded(bool force = false) {
  if (WiFi.status() != WL_CONNECTED) { wifiConnected = false; backendHealthy = false; return; }
  wifiConnected = true;

  const unsigned long interval =
      backendHealthy ? kHealthCheckIntervalMs : kHealthCheckDownIntervalMs;
  if (!force && !healthCheckDue && millis() - lastHealthCheckMs < interval) return;
  healthCheckDue = false;

  const String base = resolveMdnsUrl(HOST_BASE_URL);
  const String url = base + "/api/health";
  // mDNS で引けなかった .local をそのまま渡すと DNS の応答待ちで十数秒固まりうるので、届かない扱いにする。
  if (base.indexOf(".local") >= 0) {
    backendHealthy = false;
    Serial.printf("[health] %s -> unresolved\n", url.c_str());
  } else {
    HTTPClient http;
    http.begin(url);
    http.setConnectTimeout(kHealthConnectTimeoutMs);
    http.setTimeout(3000);
    const int code = http.GET();
    backendHealthy = (code == 200);
    Serial.printf("[health] %s -> %d\n", url.c_str(), code);
    http.end();
  }
  // 確認にかかった時間を間隔に含めない（届かないときに確認が詰まって続くのを防ぐ）。
  lastHealthCheckMs = millis();
}

// ---- WAV playback ----

struct WavHeader {
  uint16_t numChannels;
  uint32_t sampleRate;
  uint16_t bitsPerSample;
  uint32_t dataSize;
};

bool parseWavHeader(const uint8_t* buf, size_t size, WavHeader& out) {
  // Minimal RIFF/WAV parser — fixed 44-byte canonical header
  if (size < 44) return false;
  if (buf[0] != 'R' || buf[1] != 'I' || buf[2] != 'F' || buf[3] != 'F') return false;
  if (buf[8] != 'W' || buf[9] != 'A' || buf[10] != 'V' || buf[11] != 'E') return false;

  out.numChannels  = buf[22] | (buf[23] << 8);
  out.sampleRate   = buf[24] | (buf[25] << 8) | (buf[26] << 16) | (buf[27] << 24);
  out.bitsPerSample = buf[34] | (buf[35] << 8);
  out.dataSize     = buf[40] | (buf[41] << 8) | (buf[42] << 16) | (buf[43] << 24);
  return true;
}

// ---- streaming WAV playback ----

// WiFiClient から need バイトを確実に読み出す（タイムアウト/切断まで待つ）。
// 戻り値は実際に読めたバイト数（need 未満なら EOF/タイムアウト）。
size_t readFully(WiFiClient* stream, uint8_t* dst, size_t need, uint32_t timeoutMs) {
  size_t off = 0;
  uint32_t lastData = millis();
  while (off < need) {
    const int r = stream->read(dst + off, need - off);
    if (r > 0) {
      off += r;
      lastData = millis();
      continue;
    }
    // データ待ち：接続が切れて残データも無ければ終了
    if (!stream->connected() && stream->available() == 0) break;
    if (millis() - lastData > timeoutMs) {
      Serial.println("[wav] stream read timeout");
      break;
    }
    delay(1);
  }
  return off;
}

// 再生中のボタン押下を検出して停止する（クリックで停止）。検出したら離すまで待って
// 押下を消費し、呼び出し元へ戻った後の loop() でクリック扱いの再送が起きないようにする。
bool consumeStopPress() {
  if (!M5.BtnA.wasPressed()) return false;
  while (M5.BtnA.isPressed()) { M5.update(); delay(5); }
  return true;
}

// HTTP レスポンスボディ(audio/wav)をチャンク受信しながら逐次再生する。
// 全体をRAMに展開しないため、応答サイズに依存せず再生できる。
void streamWavPlayback(WiFiClient* stream) {
  const uint32_t tStreamStart = millis();
  uint8_t header[44];
  if (readFully(stream, header, 44, 5000) < 44) {
    Serial.println("[wav] header read failed");
    return;
  }
  WavHeader hdr;
  if (!parseWavHeader(header, 44, hdr)) {
    Serial.println("[wav] invalid WAV header");
    return;
  }
  // ホストは 16kHz / mono / 16bit を返す前提
  if (hdr.bitsPerSample != 16 || hdr.numChannels != 1) {
    Serial.printf("[wav] unexpected format ch=%u bits=%u\n", hdr.numChannels, hdr.bitsPerSample);
    return;
  }
  Serial.printf("[wav] sr=%lu ch=%u bits=%u dataSize=%lu\n",
    (unsigned long)hdr.sampleRate, hdr.numChannels, hdr.bitsPerSample,
    (unsigned long)hdr.dataSize);

  setLedColor(kColorPlaying);

  int bufIdx = 0;
  uint32_t remaining = hdr.dataSize;
  uint32_t tFirstAudio = 0;

  while (remaining > 0) {
    const size_t wantBytes = (remaining < kPlayBufSamples * 2) ? remaining : kPlayBufSamples * 2;
    const size_t got = readFully(stream, reinterpret_cast<uint8_t*>(playBuf[bufIdx]), wantBytes, 5000);
    if (got == 0) break;
    remaining -= got;
    const size_t samples = got / 2;

    // キューが空くまで playRaw をリトライ（stop_current=false で順次追加）
    while (!M5.Speaker.playRaw(playBuf[bufIdx], samples, hdr.sampleRate, false, 1, 0, false)) {
      M5.update();
      if (wsBegun) wsClient.loop();  // 再生中も WS を回し、heartbeat 切断と ack 取りこぼしを防ぐ
      if (consumeStopPress()) { M5.Speaker.stop(); return; }
      delay(2);
    }
    if (tFirstAudio == 0) tFirstAudio = millis();  // 最初のチャンクを再生キューに積んだ時刻
    bufIdx = (bufIdx + 1) % 3;
  }

  // 残りの再生完了を待つ（長押しで中断）
  while (M5.Speaker.isPlaying()) {
    M5.update();
    if (wsBegun) wsClient.loop();  // 同上: 再生末尾でも WS を生かしておく
    if (consumeStopPress()) { M5.Speaker.stop(); break; }
    delay(5);
  }

  const uint32_t tEnd = millis();
  Serial.printf("[timing] stream: hdr+first_audio=%lu ms, playback_total=%lu ms\n",
    (unsigned long)(tFirstAudio ? tFirstAudio - tStreamStart : 0),
    (unsigned long)(tEnd - tStreamStart));
}

// ---- HTTP chat ----

void stopPlaybackRequest() {
  if (!wifiConnected) return;

  M5.Speaker.stop();

  HTTPClient http;
  http.begin(resolveMdnsUrl(HOST_BASE_URL) + "/api/playback/stop");
  http.addHeader("Content-Type", "application/json");
  const String body =
      String(R"({"device_id":")") + DEVICE_ID + R"(","session_id":")" + ("sess-" DEVICE_ID) + R"("})";
  const int code = http.POST(body);
  Serial.printf("[playback/stop] -> %d\n", code);
  http.end();

  updateLedForState();
}

void sendChatRequest(const char* text) {
  // ホストに届かない判定のままなら、押された時点で確かめ直す（確認を間引いている間に復帰していることがある）。
  if (wifiConnected && !backendHealthy) checkBackendHealthIfNeeded(true);
  if (!wifiConnected || !backendHealthy) {
    Serial.println("[chat] not connected");
    playErrorChime();
    return;
  }

  const uint32_t tChatStart = millis();
  setLedColor(kColorConnecting);
  Serial.printf("[chat] sending: %s\n", text);

  HTTPClient http;
  http.begin(resolveMdnsUrl(HOST_BASE_URL) + "/api/chat/audio");
  http.addHeader("Content-Type", "application/json");
  // HTTPClient::setTimeout は uint16_t(最大65535ms)。応答が無いまま 65秒で打ち切る。
  // ※ホストの LLM/TTS をウォームに保ち応答を 65秒以内に収めること（下記コメント参照）。
  http.setTimeout(65000);

  // 応答テキストはヘッダで受け取る（ボディは純粋な audio/wav バイナリ）
  const char* collectHeaders[] = {"X-LLM-Text"};
  http.collectHeaders(collectHeaders, 1);

  JsonDocument req;
  req["device_id"]  = DEVICE_ID;
  req["session_id"] = "sess-" DEVICE_ID;
  req["mode"]       = currentMode;
  req["input_text"] = text;

  String reqBody;
  serializeJson(req, reqBody);

  // POST が返るまで＝ホストが LLM+TTS+WAV変換を終えて応答を返すまで（ボディは生成済み）
  const uint32_t tPostStart = millis();
  const int code = http.POST(reqBody);
  const uint32_t tPostEnd = millis();
  Serial.printf("[chat] status: %d\n", code);
  Serial.printf("[timing] server round-trip (POST->resp): %lu ms\n",
    (unsigned long)(tPostEnd - tPostStart));

  if (code != 200) {
    Serial.printf("[chat] error body: %s\n", http.getString().c_str());
    http.end();
    setLedColor(kColorBackendError);
    playErrorChime();
    return;
  }

  if (http.hasHeader("X-LLM-Text")) {
    Serial.printf("[chat] llm_text(url-encoded): %s\n", http.header("X-LLM-Text").c_str());
  }

  // WAV バイナリをチャンク受信しながら逐次再生（全体をRAMに載せない）
  WiFiClient* stream = http.getStreamPtr();
  streamWavPlayback(stream);
  http.end();

  Serial.printf("[timing] total (send->playback done): %lu ms\n",
    (unsigned long)(millis() - tChatStart));

  updateLedForState();
}

// ---- voice 対話（WebSocket /ws/audio + マイク PTT 録音） ----

// /ws/audio からの受信イベント。stt.final / error を受けてフラグを立てる。
void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED: {
      wsConnected = true;
      Serial.println("[ws] connected /ws/audio");
      // 接続のたびに device_id を登録し、ホストからの play push を受けられるようにする
      // （リレーモード用。voice モードでも無害）。
      JsonDocument reg;
      reg["type"] = "register";
      reg["device_id"] = DEVICE_ID;
      String s;
      serializeJson(reg, s);
      wsClient.sendTXT(s);
      break;
    }
    case WStype_DISCONNECTED:
      // つながっていた WS が切れたら、ホストが落ちたのかをすぐ確かめる（loop() の再接続抑止に使う）。
      if (wsConnected) healthCheckDue = true;
      wsConnected = false;
      Serial.println("[ws] disconnected");
      break;
    case WStype_ERROR:
      if (wsConnected) healthCheckDue = true;
      wsConnected = false;
      Serial.println("[ws] error");
      break;
    case WStype_TEXT: {
      JsonDocument doc;
      if (deserializeJson(doc, payload, length)) break;
      const char* t = doc["type"] | "";
      if (strcmp(t, "stt.final") == 0) {
        sttText = String((const char*)(doc["text"] | ""));
        sttReceived = true;
        Serial.printf("[ws] stt.final: %s\n", sttText.c_str());
      } else if (strcmp(t, "play") == 0) {
        // リレー: ホストから「このテキストを自分の声で再生せよ」。loop() で処理する。
        relayPlayText = String((const char*)(doc["text"] | ""));
        relayPlayPersona = String((const char*)(doc["persona"] | ""));
        relayPlayTurn = doc["turn"] | 0;
        relayPlayPending = true;
        Serial.printf("[ws] play(turn=%d, persona=%s): %s\n", relayPlayTurn,
                      relayPlayPersona.c_str(), relayPlayText.c_str());
      } else if (strcmp(t, "registered") == 0) {
        Serial.printf("[ws] registered as %s\n", (const char*)(doc["device_id"] | ""));
      } else if (strcmp(t, "error") == 0) {
        Serial.printf("[ws] error: %s\n", (const char*)(doc["message"] | ""));
        sttText = "";          // 空にしておけば後段でチャット送信されない
        sttReceived = true;
      }
      break;
    }
    default:
      break;
  }
}

// "http://host:port/..." から host と port を取り出す。
bool parseHostPort(const String& url, String& host, uint16_t& port) {
  const int p = url.indexOf("://");
  if (p < 0) return false;
  const String rest = url.substring(p + 3);
  const int slash = rest.indexOf('/');
  const String hp = (slash >= 0) ? rest.substring(0, slash) : rest;
  const int colon = hp.lastIndexOf(':');
  if (colon >= 0) {
    host = hp.substring(0, colon);
    port = (uint16_t)hp.substring(colon + 1).toInt();
  } else {
    host = hp;
    port = 80;
  }
  return host.length() > 0;
}

void wsConnect() {
  if (!wifiConnected) {
    Serial.println("[ws] wifi down — skip connect");
    return;
  }
  const String resolved = resolveMdnsUrl(HOST_BASE_URL);
  String host;
  uint16_t port = 0;
  if (!parseHostPort(resolved, host, port)) {
    Serial.println("[ws] failed to parse host URL");
    return;
  }
  Serial.printf("[ws] connecting to ws://%s:%u/ws/audio\n", host.c_str(), port);
  wsClient.begin(host, port, "/ws/audio");
  wsClient.onEvent(onWsEvent);
  // 切断後は 3 秒間隔で自動再接続する。
  wsClient.setReconnectInterval(3000);
  // ping/pong で半開（host 再起動・WiFi 瞬断）を検知し、応答が無ければ切断扱いにする。
  // 15s ごとに ping、pong を 3s 待ち、2 回連続で取りこぼしたら切断とみなす。
  wsClient.enableHeartbeat(15000, 3000, 2);
  wsBegun = true;
}

void sendWsJson(JsonDocument& doc) {
  String s;
  serializeJson(doc, s);
  wsClient.sendTXT(s);
}

bool beginMicCapture(bool markRecording = true);

// PTT 録音を開始する。WS 未接続なら接続を待ち、session.start を送ってマイクを起動する。
void startVoiceCapture() {
  if (!wifiConnected) {
    Serial.println("[voice] wifi down — abort");
    playErrorChime();
    return;
  }
  // ホストに届かないまま WS を張りに行くと、接続待ちで数秒固まったうえ再接続を繰り返してしまう。
  if (!backendHealthy) checkBackendHealthIfNeeded(true);
  if (!backendHealthy) {
    Serial.println("[voice] backend down — abort");
    playErrorChime();
    return;
  }

  if (!wsBegun) {
    wsConnect();
  } else if (!wsConnected) {
    // 既に begin 済みだが切断中（host 再起動・瞬断後など）。自動再接続の 3s を待たず即時に張り直す。
    Serial.println("[voice] ws stale — forcing reconnect");
    wsClient.disconnect();
    wsConnect();
  }

  // 接続確立を待つ（自動再接続間隔を跨げる長さ）。待機中も loop() を回して接続処理を進める。
  const uint32_t deadline = millis() + kWsConnectWaitMs;
  while (!wsConnected && millis() < deadline) {
    wsClient.loop();
    delay(5);
  }
  if (!wsConnected) {
    Serial.println("[voice] ws not connected — abort");
    playErrorChime();
    return;
  }

  wsSessionId = String("atom-") + String(millis());
  sttReceived = false;
  sttText = "";

  JsonDocument start;
  start["type"] = "session.start";
  start["session_id"] = wsSessionId;
  start["device_id"] = DEVICE_ID;
  sendWsJson(start);

  if (!beginMicCapture()) return;
  Serial.printf("[voice] capture start (session=%s)\n", wsSessionId.c_str());
}

// マイクを起動してギャップレス録音パイプラインをプライムする（voice / recorder / parrot 共通）。
// 失敗時はスピーカーを戻してエラー音を鳴らし false を返す。markRecording=false は parrot の常時聞き取り用で、
// recording を立てない（立てると長押しのモード切替が効かなくなるため）。
bool beginMicCapture(bool markRecording) {
  // マイクとスピーカーは I2S を共有するため、録音前にスピーカーを止めてマイクを起動する。
  M5.Speaker.end();

  // DMA バッファを既定(128×8=1024sample)から拡大する。oversampling=2 で I2S は実 32kHz
  // 動作のため既定では約 32ms 分しか保持できず、チャンク送信(base64+WS)のすき間で
  // マイク入力を取りこぼし、認識精度が落ちる。256×16=4096sample ≒ 128ms 分まで広げて
  // すき間中の音を確実にバッファする。あわせて入力ゲインも少し上げる。
  auto micCfg = M5.Mic.config();
  micCfg.dma_buf_len   = 256;
  micCfg.dma_buf_count = 16;
  micCfg.magnification = 24;  // 既定16。発話レベルを底上げ（上げ過ぎるとクリップするので控えめに）
  M5.Mic.config(micCfg);

  if (!M5.Mic.begin()) {
    Serial.println("[mic] M5.Mic.begin() failed");
    M5.Speaker.begin();
    playErrorChime();
    return false;
  }
  recording = markRecording;

  // パイプラインをプライムする: 2 ジョブを先に積み、マイクタスクが常に「録音中＋次が
  // キュー済み」の状態を保てるようにする（dst_remain==0 でタスクが停止しないため連続録音）。
  capSendIdx = 0;
  M5.Mic.record(capBuf[0], kVoiceChunkSamples, 16000);
  M5.Mic.record(capBuf[1], kVoiceChunkSamples, 16000);
  capQueueIdx = 2 % kCapBuffers;
  return true;
}

// 録音パイプラインに次のジョブを積めるか（＝いちばん古いジョブが録り終わったか）。
// record() は積み先が空くまで内部で最大 100ms 待ち、その間は M5.update() が回らないので、短いクリックを
// 取りこぼす（録音を止めたつもりが止まらない）。空きができてから record() を呼び、loop() を回し続ける。
bool micChunkReady() {
  return M5.Mic.isRecording() < 2;
}

// 指定バッファ 1 枚を audio.chunk として送信する。
void sendCapBuffer(int idx) {
  const String b64 = base64::encode(reinterpret_cast<uint8_t*>(capBuf[idx]),
                                    kVoiceChunkSamples * sizeof(int16_t));
  JsonDocument chunk;
  chunk["type"] = "audio.chunk";
  chunk["session_id"] = wsSessionId;
  chunk["data"] = b64;
  sendWsJson(chunk);
  wsClient.loop();
}

// 次の録音ジョブを積み、その時点で完了が確定した 2 つ前のバッファを送信する。
// 積み先スロットが空いた（=2 つ前のジョブ完了）ときだけ進むので、capSendIdx は確実に録音完了済み。
// 録音中ループから繰り返し呼ばれ、まだ空いていなければ何もせず戻る。
void captureAndSendChunk() {
  if (!micChunkReady()) return;
  if (!M5.Mic.record(capBuf[capQueueIdx], kVoiceChunkSamples, 16000)) return;
  sendCapBuffer(capSendIdx);
  capQueueIdx = (capQueueIdx + 1) % kCapBuffers;
  capSendIdx  = (capSendIdx + 1) % kCapBuffers;

  // 録音中の LED 点滅（緑）
  setLedColor(((millis() / 250) % 2) ? kColorRecording : 0x000000);
}

// PTT 録音を終了し、audio.end → stt.final 受信 → /api/chat/audio で応答再生まで行う。
void finishVoiceCapture() {
  recording = false;
  M5.Mic.end();
  M5.Speaker.begin();

  JsonDocument end;
  end["type"] = "audio.end";
  end["session_id"] = wsSessionId;
  sendWsJson(end);
  Serial.println("[voice] audio.end sent — waiting stt.final");

  setLedColor(kColorConnecting);
  const uint32_t deadline = millis() + kSttWaitMs;
  while (!sttReceived && millis() < deadline) {
    wsClient.loop();
    M5.update();
    delay(5);
  }

  if (!sttReceived) {
    Serial.println("[voice] stt.final timeout");
    playErrorChime();
    updateLedForState();
    return;
  }
  sttReceived = false;
  const String text = sttText;
  sttText = "";

  if (text.length() == 0) {
    Serial.println("[voice] empty transcript — skip chat");
    updateLedForState();
    return;
  }

  // 文字起こし結果でそのまま既存の HTTP チャット経路（/api/chat/audio）を叩く
  sendChatRequest(text.c_str());
}

// ---- relay（ホスト統括リレー）----

// ホストから push された text を /api/chat/say で TTS してもらって再生し、完了を played で
// ホストへ返す。ホストはこの ack を待って次のターンへ進む。persona が空なら自分の声色、
// 指定があればそのペルソナの声色で鳴らす（実機1台で2体の会話・Web からの話しかけ）。
void playRelayTurn(const String& text, const String& persona, int turn) {
  if (wifiConnected && !backendHealthy) checkBackendHealthIfNeeded(true);
  if (!wifiConnected || !backendHealthy) {
    Serial.println("[relay] not connected — skip turn");
    // ホストを固まらせないため、失敗でも played は返す（下で送る）。
  } else {
    setLedColor(kColorConnecting);
    Serial.printf("[relay] say(turn=%d): %s\n", turn, text.c_str());

    HTTPClient http;
    http.begin(resolveMdnsUrl(HOST_BASE_URL) + "/api/chat/say");
    http.addHeader("Content-Type", "application/json");
    // 合成は GPU 競合で遅くなりうるので、待てるだけ待つ。HTTPClient::setTimeout は uint16_t
    // （最大 65535ms）で、それより大きい値は桁あふれして短くなる（150000 は 18.9 秒になっていた）。
    http.setTimeout(65000);

    JsonDocument req;
    req["device_id"] = DEVICE_ID;
    req["text"]      = text;
    if (persona.length() > 0) req["persona_id"] = persona;
    String reqBody;
    serializeJson(req, reqBody);

    const int code = http.POST(reqBody);
    Serial.printf("[relay] /api/chat/say -> %d\n", code);
    if (code != 200) {
      Serial.printf("[relay] error body: %s\n", http.getString().c_str());
      http.end();
      setLedColor(kColorBackendError);
      playErrorChime();
    } else {
      // WAV をチャンク受信しながら逐次再生（既存の再生経路を再利用）
      WiFiClient* stream = http.getStreamPtr();
      streamWavPlayback(stream);
      http.end();
    }
  }

  // 長時間の合成中に WS が切れていることがある。played が宛先不明にならないよう、
  // 自動再接続（3s 間隔・再接続時に register を再送）を最大数秒だけ待ってから送る。
  if (wsBegun && !wsConnected) {
    Serial.println("[relay] ws down before ack — waiting reconnect");
    const uint32_t deadline = millis() + 5000;
    while (!wsConnected && millis() < deadline) {
      wsClient.loop();
      delay(20);
    }
  }

  // 再生完了をホストへ通知。これが次ターンの合図になる（失敗時も会話を止めないため送る）。
  JsonDocument done;
  done["type"]      = "played";
  done["device_id"] = DEVICE_ID;
  done["turn"]      = turn;
  sendWsJson(done);

  updateLedForState();
}

// ---- recorder（単体録音再生）----

void playRecording();

// LittleFS にファイルがあるか。LittleFS.exists() / remove() は無いファイルに対してエラーログを出すので、
// ログを汚さない stat() で確かめる。
bool fileExists(const char* path) {
  struct stat st;
  return stat((String("/littlefs") + path).c_str(), &st) == 0;
}

void removeIfExists(const char* path) {
  if (fileExists(path)) LittleFS.remove(path);
}

// いまの空き容量から、今回の録音の上限バイト数を求める（最大秒数と空き容量の小さい方）。
// written は今回の録音ですでに書いたバイト数。
uint32_t recorderMaxBytes(uint32_t written) {
  const size_t freeBytes = LittleFS.totalBytes() - LittleFS.usedBytes();
  const uint32_t maxByTime = kRecMaxSeconds * kRecSampleRate * sizeof(int16_t);
  const uint32_t maxBySpace =
      written + (freeBytes > kRecFsMarginBytes ? freeBytes - kRecFsMarginBytes : 0);
  return maxByTime < maxBySpace ? maxByTime : maxBySpace;
}

// 録音を開始する（1 スロット）。録音は kRecTmpPath へ書き、前の録音は十分な長さが録れるまで残す。
void startRecorderCapture() {
  if (!fsReady) {
    Serial.println("[rec] LittleFS not ready");
    playErrorChime();
    return;
  }
  removeIfExists(kRecTmpPath);
  recOldRemoved = !fileExists(kRecPath);
  recMaxBytes = recorderMaxBytes(0);
  if (!recOldRemoved && recMaxBytes <= kRecMinKeepBytes) {
    // 前の録音を残したままでは入らない。先に消して空ける。
    LittleFS.remove(kRecPath);
    recOldRemoved = true;
    recMaxBytes = recorderMaxBytes(0);
  }
  if (recMaxBytes < kVoiceChunkSamples * sizeof(int16_t)) {
    Serial.println("[rec] no space");
    playErrorChime();
    return;
  }

  recFile = LittleFS.open(kRecTmpPath, FILE_WRITE);
  if (!recFile) {
    Serial.println("[rec] open failed");
    playErrorChime();
    return;
  }
  recBytes = 0;

  // 開始合図。鳴り終わってからマイクへ切り替える（合図を録音しないため）。
  // 合図の間に押されたら、遅めのダブルクリックの 2 回目とみなして録音をやめる（離したら再生する）。
  M5.Speaker.tone(1000, 80);
  const uint32_t beepStartMs = millis();
  while (millis() - beepStartMs < 120) {
    M5.update();
    if (M5.BtnA.wasPressed()) {
      Serial.println("[rec] pressed during start beep — treat as double click");
      recFile.close();
      LittleFS.remove(kRecTmpPath);
      doubleClickArmed = true;
      return;
    }
    delay(5);
  }
  if (!beginMicCapture()) {
    recFile.close();
    LittleFS.remove(kRecTmpPath);
    return;
  }
  Serial.printf("[rec] start (max %lu bytes)\n", (unsigned long)recMaxBytes);
}

// 録音を止めてファイルを閉じ、今回の録音を kRecPath に置き換える。
void finishRecorderCapture() {
  recording = false;
  M5.Mic.end();
  recFile.close();
  M5.Speaker.begin();

  if (recBytes < kRecMinKeepBytes) {
    // 短すぎる録音は誤操作（遅いダブルクリックの 2 回目で止めた等）。捨てて前の録音を残し、
    // やりたかったはずの再生をする。
    LittleFS.remove(kRecTmpPath);
    Serial.printf("[rec] too short (%lu bytes) — discarded, playing previous\n",
                  (unsigned long)recBytes);
    playRecording();
    return;
  }

  removeIfExists(kRecPath);
  if (!LittleFS.rename(kRecTmpPath, kRecPath)) Serial.println("[rec] rename failed");
  Serial.printf("[rec] saved %lu bytes (%.1f s)\n", (unsigned long)recBytes,
                recBytes / (2.0f * kRecSampleRate));
  // 終了合図（開始より低い音）。鳴り終わりは待たない（待つ間のクリックを取りこぼすため）。
  M5.Speaker.tone(700, 120);
  updateLedForState();
}

// 次の録音ジョブを積み、完了が確定したバッファをファイルへ書く（captureAndSendChunk と同じ要領）。
// 上限到達・書き込み失敗（容量不足）で自動停止する。
void captureChunkToFile() {
  if (!micChunkReady()) return;
  if (!M5.Mic.record(capBuf[capQueueIdx], kVoiceChunkSamples, kRecSampleRate)) return;
  const size_t bytes = kVoiceChunkSamples * sizeof(int16_t);
  const size_t written = recFile.write(reinterpret_cast<uint8_t*>(capBuf[capSendIdx]), bytes);
  recBytes += written;
  capQueueIdx = (capQueueIdx + 1) % kCapBuffers;
  capSendIdx  = (capSendIdx + 1) % kCapBuffers;

  setLedColor(((millis() / 250) % 2) ? kColorRecorderRec : 0x000000);

  // 誤操作でない長さになったら前の録音を消し、空いたぶん上限を伸ばす。
  if (!recOldRemoved && recBytes >= kRecMinKeepBytes) {
    LittleFS.remove(kRecPath);
    recOldRemoved = true;
    recMaxBytes = recorderMaxBytes(recBytes);
  }

  if (written < bytes || recBytes >= recMaxBytes) {
    Serial.println("[rec] limit reached — auto stop");
    finishRecorderCapture();
  }
}

// 再生中にボタンが押されたか。押された時点で true を返し、その離しはクリック扱いさせない
// （holdConsumed を立てるので、押し続ければそのまま長押しのモード切替になる）。
bool stopPressed() {
  M5.update();
  if (!M5.BtnA.wasPressed()) return false;
  holdConsumed = true;
  return true;
}

// LittleFS の PCM（16kHz/mono/16-bit、ヘッダ無し）を先頭から limitBytes まで、sampleRate で再生する。
// sampleRate を変えると声の高さと速さが変わる（parrot の声の種類）。クリックで中断できる。
void playPcmFile(const char* path, uint32_t limitBytes, uint32_t sampleRate) {
  File f = LittleFS.open(path, FILE_READ);
  if (!f) return;
  setLedColor(kColorPlaying);

  const uint32_t startMs = millis();
  uint32_t remaining = limitBytes;
  int bufIdx = 0;
  bool stopped = false;
  while (!stopped && remaining > 1) {
    const size_t want = remaining < kPlayBufSamples * 2 ? remaining : kPlayBufSamples * 2;
    const int got = f.read(reinterpret_cast<uint8_t*>(playBuf[bufIdx]), want);
    if (got <= 1) break;
    remaining -= got;
    // playRaw はキューが空くまで内部で待つ（M5Unified 0.2.x）。false を返すのは無限リピート中だけ。
    while (!M5.Speaker.playRaw(playBuf[bufIdx], got / 2, sampleRate, false, 1, 0, false)) delay(2);
    bufIdx = (bufIdx + 1) % 3;
    // チャンク（約 64ms）ごとにボタンを見る。playRaw の中では M5.update() が回らないため。
    if (millis() - startMs >= kPlayStopGuardMs && stopPressed()) stopped = true;
  }
  while (!stopped && M5.Speaker.isPlaying()) {
    if (millis() - startMs >= kPlayStopGuardMs && stopPressed()) stopped = true;
    delay(5);
  }
  if (stopped) M5.Speaker.stop();
  f.close();
}

// 保存済みの録音を再生する。クリックで中断。録音が無ければ音声で案内する。
void playRecording() {
  File f = fsReady ? LittleFS.open(kRecPath, FILE_READ) : File();
  const size_t size = f ? f.size() : 0;
  if (f) f.close();
  if (size < kVoiceChunkSamples * sizeof(int16_t)) {
    Serial.println("[rec] no recording");
    if (!playPrompt(PROMPT(kPromptRecEmpty))) playErrorChime();
    updateLedForState();
    return;
  }
  Serial.printf("[rec] play %u bytes\n", (unsigned)size);
  playPcmFile(kRecPath, size, kRecSampleRate);
  updateLedForState();
}

// ---- parrot（オウム返し）----

uint32_t chunkRms(const int16_t* samples, size_t count) {
  uint64_t sum = 0;
  for (size_t i = 0; i < count; ++i) sum += (int32_t)samples[i] * samples[i];
  return (uint32_t)sqrtf((float)(sum / count));
}

float parrotThreshold() {
  const float byNoise = parrotNoise * kParrotNoiseFactor;
  return byNoise > PARROT_MIN_RMS ? byNoise : (float)PARROT_MIN_RMS;
}

// マイクを起動して聞き取りを始める。起動直後の切替ノイズは kParrotGuardMs だけ捨てる。
void parrotStartListening() {
  if (!fsReady) {
    Serial.println("[parrot] LittleFS not ready");
    playErrorChime();
    return;
  }
  if (!beginMicCapture(false)) return;
  parrotActive = true;
  parrotCapturing = false;
  parrotPreRollCount = 0;
  parrotGuardUntil = millis() + kParrotGuardMs;
  updateLedForState();
}

// 聞き取りを止めてスピーカーへ戻す（録音途中なら破棄）。
void parrotStopListening() {
  if (!parrotActive) return;
  if (parrotCapturing) {
    parrotFile.close();
    parrotCapturing = false;
  }
  M5.Mic.end();
  M5.Speaker.begin();
  parrotActive = false;
}

// 言い終わったら録音を閉じ、選んだ声で言い返してから聞き取りに戻る。
void parrotFinishAndReply() {
  parrotFile.close();
  parrotCapturing = false;
  M5.Mic.end();
  M5.Speaker.begin();
  parrotActive = false;

  if (parrotVoicedMs < kParrotMinVoicedMs) {
    Serial.printf("[parrot] too short (%lums) — ignore\n", parrotVoicedMs);
  } else {
    const uint32_t tail = kParrotTailMs * kRecSampleRate / 1000 * sizeof(int16_t);
    const uint32_t limit =
        parrotVoicedBytes + tail < parrotBytes ? parrotVoicedBytes + tail : parrotBytes;
    Serial.printf("[parrot] reply %lu bytes (voice=%d)\n", (unsigned long)limit, parrotVoice);
    playPcmFile(kParrotPath, limit, kParrotVoiceRates[parrotVoice]);
  }
  parrotStartListening();
}

// 聞き取り中に 1 チャンク（100ms）ずつ呼ぶ。声の始まり・終わりを音量で判定する。
void parrotStep() {
  if (!micChunkReady()) return;
  if (!M5.Mic.record(capBuf[capQueueIdx], kVoiceChunkSamples, kRecSampleRate)) return;
  const int16_t* chunk = capBuf[capSendIdx];
  capQueueIdx = (capQueueIdx + 1) % kCapBuffers;
  capSendIdx  = (capSendIdx + 1) % kCapBuffers;

  const float rms = (float)chunkRms(chunk, kVoiceChunkSamples);
  const float threshold = parrotThreshold();
  const unsigned long now = millis();
  if (now - parrotLastLogMs >= 1000) {
    parrotLastLogMs = now;
    Serial.printf("[parrot] rms=%.0f noise=%.0f threshold=%.0f\n", rms, parrotNoise, threshold);
  }
  if (now < parrotGuardUntil) return;

  const size_t bytes = kVoiceChunkSamples * sizeof(int16_t);
  if (!parrotCapturing) {
    if (rms < threshold) {
      // 静か: 雑音レベルを学習し、話し始めの取りこぼし防止用に直前のチャンクを取っておく。
      parrotNoise = parrotNoise == 0.0f ? rms : parrotNoise * 0.95f + rms * 0.05f;
      memcpy(parrotPreRoll[parrotPreRollIdx], chunk, bytes);
      parrotPreRollIdx = (parrotPreRollIdx + 1) % kParrotPreRollChunks;
      if (parrotPreRollCount < kParrotPreRollChunks) ++parrotPreRollCount;
      return;
    }
    // 声を検出: 直前のチャンク（古い順）→ 今回のチャンクから録音を始める。
    LittleFS.remove(kParrotPath);
    parrotFile = LittleFS.open(kParrotPath, FILE_WRITE);
    if (!parrotFile) {
      Serial.println("[parrot] open failed");
      return;
    }
    parrotBytes = 0;
    for (int i = 0; i < parrotPreRollCount; ++i) {
      const int slot = (parrotPreRollIdx - parrotPreRollCount + i + kParrotPreRollChunks) %
                       kParrotPreRollChunks;
      parrotBytes += parrotFile.write(reinterpret_cast<const uint8_t*>(parrotPreRoll[slot]), bytes);
    }
    parrotCapturing = true;
    parrotVoicedMs = 0;
    parrotSilenceMs = 0;
    Serial.printf("[parrot] voice start (rms=%.0f)\n", rms);
  }

  const size_t written = parrotFile.write(reinterpret_cast<const uint8_t*>(chunk), bytes);
  parrotBytes += written;
  if (rms >= threshold * kParrotReleaseRatio) {
    parrotVoicedBytes = parrotBytes;
    parrotVoicedMs += 100;
    parrotSilenceMs = 0;
  } else {
    parrotSilenceMs += 100;
  }
  if (parrotSilenceMs >= kParrotSilenceEndMs || parrotBytes >= kParrotMaxBytes || written < bytes) {
    parrotFinishAndReply();
  }
}

// parrot モードのクリック: 声の種類を 高い → ふつう → 低い の順に切り替えて案内する。
void onParrotClick() {
  parrotStopListening();
  parrotVoice = (parrotVoice + 1) % kParrotVoiceCount;
  prefs.putUChar("pvoice", (uint8_t)parrotVoice);
  Serial.printf("[parrot] voice -> %d\n", parrotVoice);
  if (!playPrompt(promptForParrotVoice(parrotVoice))) {
    // 案内が無ければ、選んだ声の高さのビープで知らせる。
    M5.Speaker.tone(kParrotVoiceRates[parrotVoice] / 16, 150);
    delay(200);
  }
  parrotStartListening();
}

// ---- timer（声で知らせるタイマー）----

// 案内音声を鳴らし始めて待たずに戻る（アラーム中もクリックで止められるように）。
bool startPrompt(const Prompt& p) {
  if (p.data == nullptr) return false;
  M5.Speaker.playWav(p.data, p.size, 1, -1, true);
  return true;
}

void timerStart() {
  timerDurationMs = kTimerPresetsMin[timerPreset] * 60000UL;
  timerStartMs = millis();
  timerRunning = true;
  timerOneMinuteDone = false;
  timerAlarm = false;
  Serial.printf("[timer] start %u min\n", kTimerPresetsMin[timerPreset]);
  if (!playPrompt(PROMPT(kPromptTimerStart))) {
    M5.Speaker.tone(1320, 150);
    delay(200);
  }
}

// タイマーを止める。announce=false はモード切替時の無言の後始末。
void timerCancel(bool announce) {
  const bool wasActive = timerRunning || timerAlarm;
  timerRunning = false;
  timerAlarm = false;
  if (!announce || !wasActive) return;
  M5.Speaker.stop();
  Serial.println("[timer] canceled");
  if (!playPrompt(PROMPT(kPromptTimerStop))) {
    M5.Speaker.tone(440, 200);
    delay(250);
  }
}

void timerCyclePreset() {
  timerPreset = (timerPreset + 1) % kTimerPresetCount;
  prefs.putUChar("tpreset", (uint8_t)timerPreset);
  Serial.printf("[timer] preset -> %u min\n", kTimerPresetsMin[timerPreset]);
  if (!playPrompt(promptForTimerPreset(timerPreset))) {
    // 案内が無ければ、何番目の時間かをビープの回数で知らせる。
    for (int i = 0; i <= timerPreset; ++i) {
      M5.Speaker.tone(880, 80);
      delay(160);
    }
  }
}

// loop() から毎回呼ぶ。残り 1 分の案内と、時間切れのアラームを進める。
void timerTick() {
  const unsigned long now = millis();
  if (timerAlarm) {
    if ((long)(now - timerNextAlarmMs) < 0) return;
    if (timerAlarmCount >= kTimerAlarmMaxRepeats) {
      timerAlarm = false;
      Serial.println("[timer] alarm timeout");
      return;
    }
    ++timerAlarmCount;
    timerNextAlarmMs = now + kTimerAlarmIntervalMs;
    if (!startPrompt(PROMPT(kPromptTimerDone))) M5.Speaker.tone(1760, 600);
    return;
  }
  if (!timerRunning) return;
  const unsigned long elapsed = now - timerStartMs;
  if (!timerOneMinuteDone && timerDurationMs >= 120000UL && timerDurationMs - elapsed <= 60000UL) {
    timerOneMinuteDone = true;
    Serial.println("[timer] one minute left");
    if (!startPrompt(PROMPT(kPromptTimerOneMinute))) M5.Speaker.tone(1320, 300);
  }
  if (elapsed >= timerDurationMs) {
    timerRunning = false;
    timerAlarm = true;
    timerAlarmCount = 0;
    timerNextAlarmMs = now;
    Serial.println("[timer] time is up");
  }
}

// timer モードのクリック。鳴っていれば止め、動作中なら中止、待機中はダブルクリック判定へ。
void onTimerClick() {
  if (timerAlarm) {
    timerAlarm = false;
    M5.Speaker.stop();
    Serial.println("[timer] alarm stopped");
    return;
  }
  if (timerRunning) {
    timerCancel(true);
    return;
  }
  if (doubleClickArmed) {
    doubleClickArmed = false;
    timerCyclePreset();
    return;
  }
  clickPending = true;
  clickPendingMs = millis();
}

// recorder モードのクリック（離し）。録音中なら停止、そうでなければダブルクリック判定へ。
void onRecorderClick() {
  if (recording) {
    Serial.println("[btn] click — stop recording");
    finishRecorderCapture();
    return;
  }
  if (doubleClickArmed) {
    doubleClickArmed = false;
    Serial.println("[btn] double click — play recording");
    playRecording();
    return;
  }
  clickPending = true;
  clickPendingMs = millis();
}

// ダブルクリック判定窓が過ぎたらシングルクリックとして確定する（recorder=録音開始、timer=開始）。
void resolvePendingClick() {
  if (!clickPending || millis() - clickPendingMs <= kDoubleClickWindowMs) return;
  clickPending = false;
  if (currentMode == kModeRecorder && !recording) {
    Serial.println("[btn] click — start recording");
    startRecorderCapture();
  } else if (currentMode == kModeTimer && !timerRunning && !timerAlarm) {
    timerStart();
  }
}

void enterMode(const char* mode) {
  if (mode == kModeParrot) parrotStartListening();
}

void leaveMode(const char* mode) {
  clickPending = false;
  doubleClickArmed = false;
  if (mode == kModeParrot) parrotStopListening();
  if (mode == kModeTimer) timerCancel(false);
}

}  // namespace

void setup() {
  // USB CDCはM5.begin()より前にSerialを初期化する必要がある
  Serial.begin(115200);
  {
    // PC につながっているときだけ、シリアルモニターが接続するまで最大5秒待機する。
    // 充電器・モバイルバッテリー給電では待たずに起動する（電源投入から 5 秒無反応になるのを防ぐ）。
    delay(100);  // USB ホストの有無が確定するのを待つ
    const uint32_t t = millis();
    while (usbHostPresent() && !Serial && millis() - t < 5000) delay(10);
  }
  const esp_reset_reason_t resetReason = esp_reset_reason();
  Serial.println("[boot] === AtomS3 starting ===");
  Serial.printf("[boot] reset reason: %s\n", resetReasonName(resetReason));
  Serial.printf("[boot] SDK version: %s\n", ESP.getSdkVersion());
  Serial.printf("[boot] free heap: %lu bytes\n", (unsigned long)ESP.getFreeHeap());
  Serial.printf("[boot] PSRAM size: %lu bytes\n", (unsigned long)ESP.getPsramSize());

  auto cfg = M5.config();
  // AtomS3-Lite に内蔵スピーカーは無い。音声は Atomic Echo Base(I2S, NS4168) から出力する。
  cfg.external_speaker.atomic_echo = true;
  cfg.internal_spk = false;
  cfg.internal_mic = false;
  M5.begin(cfg);
  Serial.println("[boot] M5.begin done");
  Serial.printf("[boot] board=%d speaker_enabled=%s\n",
    (int)M5.getBoard(), M5.Speaker.isEnabled() ? "yes" : "no");

  M5.Speaker.setVolume(SPEAKER_VOLUME);
  Serial.printf("[boot] Speaker volume set: %d\n", SPEAKER_VOLUME);

  // 長押し判定の閾値を再生中断（streamWavPlayback 内の pressedFor）と揃える。
  M5.BtnA.setHoldThresh(kLongPressMs);

  // 録音の保存先。初回は自動フォーマットする（1.5MB で数秒かかる）。
  fsReady = LittleFS.begin(true);
  Serial.printf("[boot] LittleFS: %s (used %u / %u bytes)\n", fsReady ? "ok" : "ng",
                fsReady ? (unsigned)LittleFS.usedBytes() : 0u,
                fsReady ? (unsigned)LittleFS.totalBytes() : 0u);

  // 前回のモードを復元する。
  prefs.begin("atom", false);
  currentMode = modeFromName(prefs.getString("mode", kModeWeb));
  parrotVoice = prefs.getUChar("pvoice", 0) % kParrotVoiceCount;
  timerPreset = prefs.getUChar("tpreset", 1) % kTimerPresetCount;
  Serial.printf("[boot] mode: %s\n", currentMode);

  // LED 点灯テスト（GPIO35 の WS2812 を neopixelWrite で直接駆動）
  setLedColor(kColorConnecting);
  Serial.printf("[boot] LED init on GPIO%d (brightness=%d)\n",
    kAtomS3LedPin, kLedBrightness);

  // 電圧低下や異常終了で再起動した直後は LED の点滅で知らせる（赤=電圧低下、黄=異常終了）。
  // PC 無しで動かしているとログを見られないので、電源の不調かどうかを本体だけで切り分けられるようにする。
  if (resetReason == ESP_RST_BROWNOUT || isCrashReset(resetReason)) {
    const uint32_t color = resetReason == ESP_RST_BROWNOUT ? 0xff0000 : 0xffff00;
    for (int i = 0; i < 5; ++i) {
      setLedColor(color);
      delay(120);
      setLedColor(0x000000);
      delay(120);
    }
    setLedColor(kColorConnecting);
  }

  Serial.printf("[boot] WiFi SSID: \"%s\"\n", WIFI_SSID);
  Serial.printf("[boot] IP mode  : %s\n",
    hasStaticIpConfig() ? "static (" WIFI_STATIC_IP ")" : "DHCP");
  Serial.printf("[boot] Host URL : %s\n", HOST_BASE_URL);
  Serial.printf("[boot] hasWifiConfig: %s\n", hasWifiConfig() ? "yes" : "no");

  updateLedForState();
  announceMode(currentMode);
  enterMode(currentMode);
  // 単体モードは起動時の Wi-Fi 接続・ヘルスチェック（ブロッキング）を省く。
  if (!isStandaloneMode(currentMode)) {
    Serial.println("[boot] connecting WiFi...");
    connectWiFiIfNeeded();
    Serial.println("[boot] checking backend health...");
    checkBackendHealthIfNeeded(true);
  }
  Serial.println("[boot] setup complete — press BtnA to chat");
}

void loop() {
  M5.update();

  // ホストに届かない間は WS の自動再接続を止める。接続試行は 1 回あたり最大 5 秒ブロックし、
  // 3 秒おきに繰り返すので、放置するとクリックも長押し（モード切替）もほとんど効かなくなる。
  // relay はホスト復帰後に下で張り直し、voice は録音開始時に張り直す。
  if (wsBegun && !wsConnected && !backendHealthy) disconnectWs();
  if (wsBegun) wsClient.loop();

  // 単体モードの間は Wi-Fi 再接続やヘルスチェック（最大数秒ブロックする HTTP）を行わない
  // （録音の取りこぼしやタイマー・オウム返しの反応遅れを防ぐ）。
  if (!isStandaloneMode(currentMode)) {
    connectWiFiIfNeeded();
    checkBackendHealthIfNeeded();
  }
  // 録音中は録音 LED を優先し、状態 LED で上書きしない
  if (!recording) updateLedForState();

  // 10秒ごとにステータスをシリアル出力
  const auto now = millis();
  if (now - lastHeartbeatMs >= kHeartbeatIntervalMs) {
    lastHeartbeatMs = now;
    Serial.printf("[status] uptime=%lus wifi=%s backend=%s mode=%s heap=%lubytes\n",
      now / 1000,
      wifiConnected  ? "ok" : "ng",
      backendHealthy ? "ok" : "ng",
      currentMode,
      (unsigned long)ESP.getFreeHeap());
  }

  // --- ボタン操作 ---
  // 長押し（kLongPressMs 到達）= モード切替（両モード共通）。wasHold() は閾値到達時に
  // 1 回だけ発火する。録音中は切替せず、離し（クリック）で録音停止に倒す。
  if (M5.BtnA.wasHold() && !recording) {
    Serial.println("[btn] hold — toggling mode");
    toggleMode();
    holdConsumed = true;  // 直後の離しをクリックとして扱わない
  }

  // 録音中は 1 チャンクずつマイク入力を送り続ける（voice モードのクリック録音）。
  if (recording && currentMode == kModeVoice) {
    captureAndSendChunk();
  }
  // recorder モードの録音はファイルへ書き込む。
  if (recording && currentMode == kModeRecorder) {
    captureChunkToFile();
  }
  // parrot モードは常に聞き取り、timer モードは経過時間を進める。
  if (parrotActive && currentMode == kModeParrot) parrotStep();
  if (currentMode == kModeTimer) timerTick();
  // ダブルクリックは 2 回目の「押し」で確定する（離しで判定すると、押している長さのぶん窓が狭くなり、
  // 少し遅いだけで「クリック = 録音開始」になって前の録音を消してしまう）。
  if (clickPending && M5.BtnA.wasPressed()) {
    clickPending = false;
    doubleClickArmed = true;
  }
  resolvePendingClick();

  // リレーモード: WS を張って register し（ホストからの play 受信に必須）、
  // 受け取った play を loop 内で処理する（WS コールバック内ではブロックしないため）。
  if (currentMode == kModeRelay && wifiConnected && backendHealthy && !wsBegun) {
    wsConnect();
  }
  if (relayPlayPending) {
    relayPlayPending = false;
    const String text = relayPlayText;
    const String persona = relayPlayPersona;
    const int turn = relayPlayTurn;
    relayPlayText = "";
    playRelayTurn(text, persona, turn);
  }

  // 離し = クリック確定。長押しの離しは holdConsumed で無視する。
  if (M5.BtnA.wasReleased()) {
    if (holdConsumed) {
      holdConsumed = false;
    } else if (currentMode == kModeVoice) {
      // voice モード: クリックで録音開始/停止をトグルする。
      if (!recording) {
        Serial.println("[btn] click — start recording");
        startVoiceCapture();
      } else {
        Serial.println("[btn] click — stop recording");
        finishVoiceCapture();
      }
    } else if (currentMode == kModeRecorder) {
      // recorder モード: クリック = 録音開始/停止、ダブルクリック = 再生。
      onRecorderClick();
    } else if (currentMode == kModeParrot) {
      // parrot モード: クリック = 声の種類を切り替え（聞き取りはボタン不要）。
      onParrotClick();
    } else if (currentMode == kModeTimer) {
      // timer モード: クリック = 開始/中止/アラーム停止、ダブルクリック = 時間の切替。
      onTimerClick();
    } else if (currentMode == kModeRelay) {
      // relay モード: 開始/停止はフロントから。クリックは再生中の停止のみに使う。
      if (M5.Speaker.isPlaying()) {
        Serial.println("[btn] click — stopping relay playback");
        M5.Speaker.stop();
      }
    } else {
      // web モード: 再生中なら停止、そうでなければチャット送信。
      if (M5.Speaker.isPlaying()) {
        Serial.println("[btn] click — stopping playback");
        stopPlaybackRequest();
      } else {
        Serial.println("[btn] click — sending chat request");
        sendChatRequest("こんにちは");
      }
    }
  }

  // 録音中はチャンクの取り出しとボタンの見落としを防ぐため短く回す。それ以外は従来どおり間引く。
  delay((recording || parrotActive) ? 1 : 20);
}
