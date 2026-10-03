# 実装計画

> **この文書の位置づけ**: 実装状況の一覧と、これまでの実装計画の記録です。2 章以降は当時の計画のままです。
> 現在のしくみは [architecture.md](../architecture.md)、ほかの文書は [docs/README.md](../README.md) から探せます。

最終更新: 2026-09-23（1 章の実装状態・5 章のチートシートを現行コードに合わせて更新。2 章以降のセッション計画は当時の記録）

## 1. 現在の実装状態

```
記号凡例:  [済] 実装・動作確認済み   [ス] スタブのみ   [未] 未着手
```

### 1.0 実機の主経路（要点）

実機（AtomS3-Lite）のテキストチャットは **`POST /api/chat/audio`** を使う。これは JSON ではなく
生の `audio/wav`（16kHz / mono / 16-bit）をボディで返し、LLM テキストはヘッダ
`X-LLM-Text`(URLエンコード) / `X-LLM-Text-B64`(base64) に入れる。ファームウェアは WAV を
チャンク受信しながら逐次再生する（応答全体を RAM に保持しない）。`POST /api/chat` は JSON 版で、
主に Web UI / デバッグ用（音声インラインは `AI_VOICE_ATOM_INCLUDE_AUDIO_INLINE=true` のときのみ）。

TTS は既定で **常駐サイドカー**（`host/tts_server.py`、`AI_VOICE_ATOM_TTS_SERVER_URL`）経由で
合成し、初回呼び出しで自動起動・モデル常駐させて 2 回目以降のレイテンシを下げる。サイドカー不調時は
従来どおり `infer.py` の subprocess 実行へ自動フォールバックする。`host/voices/reference.wav` を
置くとその声色で合成する（**話者リファレンス音声**、無ければ既定の声）。

### ホスト (host/)

| ファイル | 状態 | 内容 |
|---|---|---|
| app/main.py | [済] | FastAPI 起動、ルーター登録、/frontend 静的配信 |
| app/config.py | [済] | 環境変数読み込み（.env 自動ロード・既存変数は非上書き）、パス解決 |
| app/models/schemas.py | [済] | Pydantic スキーマ |
| app/api/health.py | [済] | GET /api/health（submodule 存在確認 + paths を返す） |
| app/api/device.py | [済] | GET /api/status（device_id / mode / backend 状態） |
| app/api/chat.py | [済] | POST /api/chat（LLM+TTS）、**POST /api/chat/audio（ストリーミングWAV・実機主経路）** |
| app/api/ws_audio.py | [済] | WebSocket /ws/audio（session.start/audio.chunk/audio.end → stt.final） |
| app/api/tts.py | [済] | POST /api/tts（base64 wav 返却） |
| app/api/playback.py | [済] | POST /api/playback/stop |
| app/adapters/openai_client.py | [済] | AsyncOpenAI chat completions（reasoning_effort 制御つき） |
| app/adapters/irodori_tts_adapter.py | [済] | 常駐サイドカー HTTP 呼び出し + subprocess フォールバック + 参照音声 |
| app/adapters/moonshine_adapter.py | [済] | Transcriber による PCM→テキスト変換（遅延ロード・キャッシュ） |
| app/services/chat_service.py | [済] | LLM + TTS 連携、16kHz/mono 変換、タイミング計測、会話履歴（session 単位・上限/TTL） |
| app/services/audio_session_service.py | [済] | セッション管理 + STT 処理 |
| app/services/playback_service.py | [済] | 停止受付 |
| app/device_profiles.py | [済] | device_profiles.json の読み込み（ペルソナ・声色・サンプリング上書き・口火） |
| app/api/device.py の /api/devices | [済] | ペルソナ一覧（フロントの選択肢） |
| app/api/conversation.py | [済] | /api/conversation/start・stop・status（リレー会話）、/api/conversation/chat（Web から話しかけ） |
| app/services/connection_manager.py | [済] | device_id → WebSocket の登録と play/played の待ち合わせ（未接続は DeviceNotConnectedError） |
| app/services/conversation_orchestrator.py | [済] | リレー会話のターン統括（相手認識・フェーズ指示・反復ガード）、ペルソナと出力実機の分離、chat_once |
| scripts/check_setup.py | [済] | 準備状況の点検 |
| scripts/fetch_moonshine_native.py | [済] | Moonshine ネイティブライブラリの取得・配置 |
| scripts/generate_mode_prompts.py | [済] | モード切替の音声案内を合成し、ファーム埋め込み用ヘッダを生成 |
| app/middleware/logging_middleware.py | [済] | リクエスト構造化ログ |
| app/utils/wav.py | [済] | WAV メタデータ読み取り + to_pcm16_mono（リサンプル/ダウンミックス） |
| tts_server.py | [済] | 常駐 TTS サイドカー（モデル常駐・/tts・/health） |

### ファームウェア (firmware/atom/)

| 機能 | 状態 | 内容 |
|---|---|---|
| Wi-Fi 接続・再接続 | [済] | connectWiFiIfNeeded（固定IP / DHCP 両対応） |
| mDNS ホスト名解決 | [済] | resolveMdnsUrl（`*.local` を IP へ解決）、自身も atoms3.local で応答 |
| /api/health チェック | [済] | checkBackendHealthIfNeeded |
| LED 状態表示 | [済] | setLedColor（GPIO35 を neopixelWrite で直接駆動）, updateLedForState |
| Echo Base / I2S 初期化 | [済] | cfg.external_speaker.atomic_echo=true, M5.Speaker.setVolume |
| ストリーミング wav 再生 | [済] | parseWavHeader + streamWavPlayback（チャンク受信しながら playRaw） |
| HTTP チャット送信 | [済] | sendChatRequest（POST /api/chat/audio、ヘッダで LLM テキスト受信） |
| ボタン操作 | [済] | クリック=モードごとの操作 / 長押し 1.2s=モード切替（wasHold・holdConsumed）/ ダブルクリック=recorder の再生（自前判定 400ms） |
| モード切替（6 モード） | [済] | toggleMode（`web → voice → relay → recorder → parrot → timer`）。NVS（Preferences）に保存し起動時に復元。出入りの後始末は enterMode/leaveMode |
| 音声案内・効果音 | [済] | announceMode（`include/mode_prompts.h` があれば埋め込み WAV を playWav、無ければ playModeChime）/ playErrorChime |
| WebSocket 接続 | [済] | wsConnect（/ws/audio）、onWsEvent で stt.final・play を受信。ping/pong で半開検知・自動再接続 |
| マイク録音送信（PTT） | [済] | voice モードでクリック開始/停止のトグル録音 → audio.chunk 逐次送信（3 面のギャップレス録音）→ audio.end |
| 音声対話フル連携 | [済] | stt.final → sendChatRequest（/api/chat/audio）でストリーミング再生 |
| relay（ホスト統括の再生） | [済] | register → play（persona 付き）→ /api/chat/say（persona_id）で取得・再生 → played を返す |
| recorder（単体録音再生） | [済] | LittleFS の /rec.pcm に最大 30 秒録音・再生。単体モード中は Wi-Fi 再接続・ヘルスチェック・WS を停止 |
| parrot（オウム返し） | [済] | 常時聞き取り → RMS で発話検出（雑音 EMA × 3 と PARROT_MIN_RMS）→ /parrot.pcm に録音 → 再生レートを変えて言い返す |
| timer（タイマー） | [済] | 1/3/5/10/25 分。LED で残り時間、残り 1 分と時間切れを音声案内（アラームは非ブロッキング再生） |

### フロントエンド (frontend/)

| ファイル | 状態 | 内容 |
|---|---|---|
| frontend/index.html | [済] | 会話コンソール（状態チップ・ペルソナカード・実行モード切替・会話ログ） |
| frontend/style.css | [済] | コーポレート基調のデザイン（DESIGN.md）、モバイル対応 |
| frontend/app.js | [済] | 実行モード「画面でデモ（/api/chat/audio をブラウザ再生）」「実機で会話（/api/conversation/start、2 台 or 1 台）」「実機に話しかける（/api/conversation/chat）」、状態ポーリング |

> **テスト:** `host/` で `uv run pytest -q` が 90 件パス（2026-09-23 時点）。

---

## 2. セッション別実装計画

各セッションは **1 回のコーディング作業で完結できる粒度** で定義する。
前提セッション（依存）がある場合は明記する。

---

### Session 1: Irodori-TTS アダプタ実装（ホスト）

**目標:** テキストから wav ファイルを生成できること

**前提条件:**
- `third_party/irodori-tts` の submodule が存在すること
- `cd third_party/irodori-tts && uv sync --extra cpu` が完了していること
- `host/.env` に `AI_VOICE_ATOM_IRODORI_ROOT` が設定されていること

**実装ファイル:**
- `host/app/adapters/irodori_tts_adapter.py` — infer.py 呼び出しロジック
- `host/app/api/tts.py` — POST /api/tts エンドポイント（新規）
- `host/app/main.py` — /api/tts ルーター登録追加
- `host/tests/test_tts.py` — TTS 疎通テスト（新規）

**実装内容:**
- `IrodoriTtsAdapter.synthesize(text, ref_wav, output_path)` を実装
  - `subprocess` で `uv run --no-sync python infer.py` を呼ぶ
  - 生成 wav のパス管理（一時ファイル or 出力ディレクトリ）
  - タイムアウト・失敗時の例外変換
- `POST /api/tts` を追加
  - リクエスト: `{ device_id, session_id, text, voice, audio: { format, sample_rate } }`
  - レスポンス: wav を base64 で返す

**完了条件:**
- `uv run pytest tests/test_tts.py` が通ること
- curl で POST /api/tts を叩き、`audio.data` に base64 wav が返ること
- base64 デコードして再生し音声が聞こえること

**検証コマンド:**
```bash
cd host
uv run pytest tests/test_tts.py -v

curl -s -X POST http://localhost:8000/api/tts \
  -H "Content-Type: application/json" \
  -d '{"device_id":"test","session_id":"s1","text":"こんにちは"}' \
  | python3 -c "import sys,json,base64; d=json.load(sys.stdin); open('/tmp/out.wav','wb').write(base64.b64decode(d['audio']['data']))"

aplay /tmp/out.wav
```

---

### Session 2: LLM アダプタ実装（ホスト）

**目標:** OpenAI API 互換 LLM から応答テキストを取得できること

**前提条件:**
- LLM サーバーが起動していること（LM Studio / Ollama / vLLM など）
- `host/.env` に `AI_VOICE_ATOM_LLM_BASE_URL`, `AI_VOICE_ATOM_LLM_API_KEY`, `AI_VOICE_ATOM_LLM_MODEL` が設定されていること

**実装ファイル:**
- `host/app/adapters/openai_client.py` — chat completions 呼び出し実装
- `host/tests/test_llm.py` — LLM 疎通テスト（新規）

**実装内容:**
- `OpenAIClient.complete(messages, system_prompt, max_tokens)` を実装
  - `openai.AsyncOpenAI(base_url=..., api_key=...)` を使う
  - `messages` は `[{"role": "user", "content": "..."}]` 形式
  - タイムアウト設定
  - 失敗時の例外変換
- `OpenAIClient.is_configured()` を実際の ping に更新（オプション）

**完了条件:**
- `uv run pytest tests/test_llm.py` が通ること
- テストメッセージへの応答テキストが返ること（LLM に依存）

**検証コマンド:**
```bash
cd host
uv run pytest tests/test_llm.py -v
```

---

### Session 3: chat エンドポイント完成（LLM + TTS 連携）

**目標:** POST /api/chat がテキスト応答 + 音声を返すこと

**前提条件:**
- Session 1（TTS アダプタ）完了
- Session 2（LLM アダプタ）完了

**実装ファイル:**
- `host/app/services/chat_service.py` — LLM + TTS 連携ロジック
- `host/tests/test_chat.py` — 結合テスト追加

**実装内容:**
- `ChatService.handle_chat(payload)` を実装
  - `OpenAIClient.complete` でテキスト取得
  - `IrodoriTtsAdapter.synthesize` で wav 生成
  - wav を base64 に変換して `ChatResponse.audio` にセット
- `include_audio_inline` 設定が `false` のときは `audio = None`（テキストのみ）

**完了条件:**
- `uv run pytest tests/test_chat.py` が通ること
- curl で POST /api/chat を叩き `llm_text` に LLM 応答テキストが入ること
- `response_format: "text+audio"` のとき `audio.data` に wav が入ること

**検証コマンド:**
```bash
cd host
uv run pytest tests/test_chat.py -v

curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"device_id":"test","session_id":"s1","mode":"web","input_text":"今日の天気は？","response_format":"text+audio"}' \
  | python3 -m json.tool
```

---

### Session 4: ファームウェア Echo Base 初期化と wav 再生

**目標:** ファームウェア単体で wav を再生できること

**前提条件:**
- AtomS3-Lite + Atomic Echo Base が接続されていること
- PlatformIO 書き込み環境が整っていること

**実装ファイル:**
- `firmware/atom/src/main.cpp` — Echo Base I2S 初期化、wav 再生関数
- `firmware/atom/platformio.ini` — lib_deps に `bblanchon/ArduinoJson` 追加

**実装内容:**
- M5Unified による Echo Base スピーカー初期化
  - `cfg.internal_spk = true`（AtomS3-Lite の Echo Base 側スピーカー）
  - I2S ピン設定（Echo Base: BCLK=G6, LRCLK=G5, DOUT=G7）
- `playWavFromBuffer(const uint8_t* data, size_t size)` 実装
  - WAV ヘッダ（44 バイト）をパース
  - `M5.Speaker.playRaw(samples, len, sampleRate, stereo, ...)` で出力
- テスト用: PROGMEM に置いた短い PCM データを自動再生

**完了条件:**
- Upload 後に確認音が鳴ること
- LED 表示が崩れないこと

---

### Session 5: ファームウェア HTTP チャット送信と wav 再生連携

**目標:** ボタン操作でホストに問い合わせ、応答音声を再生できること

**前提条件:**
- Session 3（ホスト chat エンドポイント完成）
- Session 4（ファームウェア wav 再生）
- `platformio.ini` の build_flags に SSID / PASSWORD / HOST_BASE_URL が設定済み

**実装ファイル:**
- `firmware/atom/src/main.cpp` — HTTP POST /api/chat 実装、base64 デコード
- `firmware/atom/platformio.ini` — `bblanchon/ArduinoJson` lib_deps 追加（Session 4 未追加の場合）

**実装内容:**
- `sendChatRequest(const char* text)` 実装
  - HTTP POST /api/chat
  - ArduinoJson でレスポンスをパース
  - base64 デコードして `playWavFromBuffer` を呼ぶ
  - ヒープ管理（wav バッファは `heap_caps_malloc` を使う）
- LED フィードバック
  - 送信中: オレンジ点滅（`kColorConnecting`）
  - 再生中: シアン点灯（`0x00ffff`）
- ボタン A 短押し → `sendChatRequest("こんにちは")` で疎通確認
- ボタン A 長押し（1200ms）→ 再生停止 + POST /api/playback/stop

**完了条件:**
- ボタン A を押すと音声応答が聞こえること
- 通信中・再生中の LED 変化が確認できること
- Serial モニタに通信ログが出ること

---

### Session 6: Moonshine アダプタ実装（ホスト）

**目標:** ホストで PCM 音声を受け取りテキストに変換できること

**前提条件:**
- `third_party/moonshine` の submodule が存在すること
- `cd host && uv sync --extra dev` で moonshine-voice が入っていること
- Moonshine のネイティブライブラリ配置済み（`cd host && uv run python scripts/fetch_moonshine_native.py`）。
  モデル本体は初回の文字起こし時に `.cache/moonshine_voice`（`AI_VOICE_ATOM_MOONSHINE_CACHE`）へ自動取得される

**実装ファイル:**
- `host/app/adapters/moonshine_adapter.py` — Transcriber 実装
- `host/tests/test_moonshine.py` — STT 疎通テスト（新規）

**実装内容:**
- `MoonshineAdapter` の初期化で `moonshine_voice.Transcriber` を生成
  - モデルパスを `config.moonshine_cache_path` から参照
  - 言語設定を `config.stt_model_language` から参照
- `MoonshineAdapter.transcribe_pcm(pcm_bytes, sample_rate=16000)` を実装
  - pcm_bytes (16bit / mono) を numpy float32 に変換
  - `transcriber.transcribe(audio)` でテキスト取得

**完了条件:**
- `uv run pytest tests/test_moonshine.py` が通ること
- テスト用 wav を渡してテキストが返ること

**検証コマンド:**
```bash
cd host
uv run pytest tests/test_moonshine.py -v
```

---

### Session 7: WebSocket 音声対話（ホスト STT 連携）

**目標:** WebSocket 経由で音声を受け取り STT 結果を返せること

**前提条件:**
- Session 6（Moonshine アダプタ）完了

**実装ファイル:**
- `host/app/services/audio_session_service.py` — セッション管理 + STT 処理
- `host/app/api/ws_audio.py` — audio.chunk イベント処理追加
- `host/tests/test_ws_audio.py` — WebSocket テスト追加

**WebSocket メッセージプロトコル:**

デバイス → ホスト:
```json
{ "type": "session.start", "session_id": "...", "device_id": "..." }
{ "type": "audio.chunk", "session_id": "...", "data": "<base64 PCM 16kHz/16bit/mono>" }
{ "type": "audio.end",   "session_id": "..." }
```

ホスト → デバイス:
```json
{ "type": "session.ack",  "session_id": "...", "accepted": true }
{ "type": "stt.partial",  "session_id": "...", "text": "..." }
{ "type": "stt.final",    "session_id": "...", "text": "...", "is_final": true }
```

**実装内容:**
- `AudioSessionService.handle_session_start(session_id, device_id)` — セッション登録
- `AudioSessionService.handle_audio_chunk(session_id, pcm_b64)` — バッファ蓄積
- `AudioSessionService.handle_audio_end(session_id)` — MoonshineAdapter.transcribe_pcm 呼び出し → stt.final 送信
- WebSocket ハンドラを非同期ジェネレータで書き直し

**完了条件:**
- `uv run pytest tests/test_ws_audio.py` が通ること
- session.start → audio.chunk × n → audio.end の流れで stt.final が返ること

**検証コマンド:**
```bash
cd host
uv run pytest tests/test_ws_audio.py -v
```

---

### Session 8: 音声対話フル連携（ファームウェア WebSocket + マイク）

**目標:** ボタン操作で録音 → STT → LLM → TTS → 再生が一巡すること

**前提条件:**
- Session 5（ファームウェア HTTP チャット）完了
- Session 7（ホスト WebSocket STT）完了

**実装ファイル:**
- `firmware/atom/src/main.cpp` — WebSocket クライアント、マイク I2S 録音、対話フロー
- `firmware/atom/platformio.ini` — 確認（WebSockets ライブラリは既存）

**実装内容:**
- WebSocket クライアント初期化（`links2004/WebSockets`）
- Echo Base マイク I2S 録音
  - I2S ピン設定（Echo Base: BCLK=G0, LRCLK=G1, DIN=G2）
  - 16kHz / 16bit / mono で録音バッファに蓄積
- 音声対話フロー:
  1. ボタン A 短押し → voice モードで session.start 送信
  2. I2S 録音 → audio.chunk を逐次 WebSocket 送信
  3. ボタン A 再押し または 2 秒無音 → audio.end 送信
  4. stt.final 受信 → sendChatRequest(text)
  5. wav 受信 → playWavFromBuffer
- LED: 録音中は緑点滅、STT/LLM 待ち中はオレンジ点滅、再生中はシアン点灯

**完了条件:**
- ボタン A を押してから話し、再び押すと音声応答が返ること
- Serial モニタに各ステップのログが出ること

---

### Session 9: Web フロントエンド MVP

**目標:** ブラウザからテキスト問い合わせと状態確認ができること

**前提条件:**
- Session 3（ホスト chat エンドポイント完成）

**実装ファイル:**
- `frontend/index.html` — 静的 HTML
- `frontend/style.css`
- `frontend/app.js`
- `host/app/main.py` — `/frontend` に静的ファイルを serve する設定追加

**実装内容:**
- ステータス表示（GET /api/status を 5 秒ポーリング）
  - LLM / TTS / STT の OK / NG 表示
- テキスト入力フォーム
  - POST /api/chat → `llm_text` をテキスト表示
  - `audio.data` があれば Web Audio API で再生
- 再生停止ボタン → POST /api/playback/stop

**完了条件:**
- `http://localhost:8000/frontend/index.html` を開いてテキスト入力できること
- LLM 応答テキストが画面に出ること
- TTS 有効時に音声が再生されること

---

### Session 10: 安定化

**目標:** 再接続・エラー・ログを整えて継続動作できること

**前提条件:**
- Session 5 以降が完了していること

**実装ファイル:**
- `host/app/middleware/logging_middleware.py` — リクエストログ（新規）
- `host/app/main.py` — middleware 登録
- `firmware/atom/src/main.cpp` — 再接続強化

**実装内容:**

ホスト側:
- リクエスト単位の構造化ログ（session_id, latency_ms, status_code）
- TTS / LLM タイムアウト設定の環境変数化
- エラー応答の形式統一 `{ "error": { "code": "...", "message": "..." } }`

ファームウェア側:
- WebSocket 切断時の自動再接続（指数バックオフ）
- LLM / TTS エラー時のエラー音 + 赤 LED フィードバック

**完了条件:**
- ホストを再起動してもデバイスが自動復帰すること
- LLM が応答しない場合にエラーフィードバックが出ること

---

## 3. 実装順序と依存関係

```
Session 1: TTS アダプタ ────────────────────┐
Session 2: LLM アダプタ ──── Session 3 ──── Session 5 ──── Session 8
                                            Session 4 ──────────────┘
Session 6: Moonshine ──── Session 7 ────────────────────────────────┘
Session 3 ──── Session 9
Session 5 以降完了 ──── Session 10
```

| # | セッション | 前提 | 難易度 | 状態 |
|---|---|---|---|---|
| 1 | Irodori-TTS アダプタ | なし | ★★☆ | [済]（+常駐サイドカー・参照音声） |
| 2 | LLM アダプタ | なし | ★☆☆ | [済] |
| 3 | chat エンドポイント完成 | 1, 2 | ★★☆ | [済]（+ /api/chat/audio 追加） |
| 4 | Echo Base wav 再生 | なし | ★★★ | [済]（external_speaker.atomic_echo） |
| 5 | HTTP チャット送受信 | 3, 4 | ★★☆ | [済]（ストリーミング再生） |
| 6 | Moonshine アダプタ | なし | ★★☆ | [済] |
| 7 | WebSocket STT | 6 | ★★☆ | [済]（stt.final のみ・partial 未） |
| 8 | マイク録音 + 音声対話 | 5, 7 | ★★★ | [済]（ビルド確認・実機検証待ち） |
| 9 | Web フロントエンド | 3 | ★☆☆ | [済] |
| 10 | 安定化 | 5 以降 | ★★☆ | [一部]（ホストログ済 / FW 再接続強化は未） |

Session 1〜9 は完了（Session 8 は提案 B として実装済み）。その後の追加機能は 6 章末尾の「その後の実装」を参照。

---

## 6. 次に実装するとよい内容（提案）

優先度順。Session 8 が「音声対話デバイス」としての最後の大きな未完部分。

### 提案 A（最優先）: 実機ボタン操作の整理とモード/フィードバック（FW・小） — [済]

**状態: 実装済み（2026-06-20）。** その後ボタン操作は「クリック=操作 / 長押し=モード切替」に改め、
モードも 4 つ（web / voice / relay / recorder）に増えた。現行仕様は 1 章と `firmware/atom/README.md` を参照。以下は当時の記録。

- ボタン操作の再定義: 短押し（`wasClicked`）=送信 / ダブルクリック（`wasDoubleClicked`）=モード切替 / 長押し（`wasHold`、`setHoldThresh(kLongPressMs)`）=停止
- モード状態（web / voice）の保持と LED 色の切替（`toggleMode` → `updateLedForState`）、モード切替時の効果音（`playModeChime`: voice=高め 2 音 / web=低め 1 音）
- エラー時のエラー音（`playErrorChime`: 赤 LED + 断続 3 音）を未接続時・HTTP 非 200 時に鳴らす
- 残: 実機での書き込み・動作確認（VS Code PlatformIO Upload、シリアルモニターを閉じてから）

### 提案 B: 実機マイク録音 + 音声対話フル連携（= Session 8、FW・大） — [済]

**状態: 実装済み・実機確認済み。** 録音操作はその後「クリックで開始 → もう一度クリックで停止」のトグルに改めた。以下は当時の記録。

設計（確定事項）: 録音は **PTT（押している間だけ録音）** / **voice モード時のみ** / stt.final 後は
**既存 `/api/chat/audio` を再利用**。ホスト側は `/ws/audio`・`/api/chat/audio` とも実装済みのため変更なし。

- Echo Base マイク I2S 録音（16kHz / 16bit / mono）。`M5.Mic` と `M5.Speaker` は I2S 共有のため録音/再生で排他切替（`M5.Speaker.end()` ↔ `M5.Mic.begin()`）
- WebSocket クライアント（`WebSocketsClient`）で session.start → audio.chunk（100ms/1600サンプルを base64 で逐次送信）→ audio.end
- stt.final 受信 → そのテキストで `sendChatRequest`（`POST /api/chat/audio`）→ ストリーミング再生
- ボタン: voice モードは押下 `kPttArmMs`(180ms) 超で録音開始（ダブルクリックと両立）、離して送信。録音に至らない素早いダブルクリックで web へ戻る
- LED: 録音中=緑点滅、stt 待ち=オレンジ、再生中=マゼンタ
- 残: 実機での書き込み・動作確認（特に Echo Base マイクの `M5.Mic` 動作、録音/再生の I2S 排他切替、WS 経由の文字起こし精度）

### 提案 C: WebSocket での逐次フィードバック（ホスト・中）

`backend-api.md` が定義する `stt.partial` / `llm.started` / `llm.result` を未実装。低遅延 UX と
デバッグのために、audio.end 後に LLM/TTS まで WebSocket 内で完結させ進捗イベントを返す。

- AudioSessionService に LLM/TTS 連携を追加し、`stt.final` → `llm.result` → 音声取得先（または base64）を返す
- ストリーミング STT（partial）は Moonshine の対応次第で段階導入
- 完了条件: 1 本の WebSocket セッションで録音〜応答音声取得まで完結する

### 提案 D: 会話履歴 / 文脈保持（ホスト・中, UC-19） — [済]

**状態: 実装済み（2026-06-20、pytest 23 件パス）**

- `ChatService._ConversationStore`: session_id ごとに直近 `history_max_turns` 往復を保持。`history_ttl_sec` 超過で破棄、`max_turns<=0` で無効化
- `POST /api/chat` / `/api/chat/audio` の両経路で履歴を前置して LLM へ送信し、応答後に追記
- 設定: `AI_VOICE_ATOM_HISTORY_MAX_TURNS`(既定 6) / `AI_VOICE_ATOM_HISTORY_TTL_SEC`(既定 1800)
- テスト: 履歴前置 / session 分離 / 無効化 / 上限トリミングを `tests/test_chat.py` に追加
- 補足: プロセス内メモリ保持のため、ホスト再起動で履歴は消える（永続化は将来課題）

### 提案 E: 設定・運用の底上げ（横断, UC-13/21）

- `GET /api/status` に network / audio（is_playing, volume）/ active_session_id を反映（現状は backend のみ）
- エラー応答形式の統一（`{ "error": { "code", "message" } }`、backend-api.md §7 のコード体系）
- FW の WebSocket 切断時 自動再接続（指数バックオフ）
- ドキュメント `verification-guide.md` の整備（手動検証手順）

> 推奨着手順: **A → B → C → D → E**。A は小さく実機体験を底上げでき、B/C が音声対話の本丸。

### その後の実装（2026-06〜09）

| 機能 | 状態 | 記録 |
|---|---|---|
| マルチデバイス（デバイス別ペルソナ・声色・履歴分離） | [済] | `CLAUDE.md`「マルチデバイス」、`host/README.md` |
| Web 自動会話（画面でデモ）とフロント刷新 | [済] | `docs/plans/frontend-auto-conversation-plan.md`、`DESIGN.md` |
| 実機リレー会話（ホスト統括、Phase B-1） | [済] | `docs/backend-api.md` §5.8 / §6.7 |
| ペルソナ会話の自然さ改善（相手認識・フェーズ指示・反復ガード等） | A/B 群 [済]・C/D 群 [未] | `docs/plans/persona-conversation-improvements.md` |
| モード切替の音声案内・単体録音再生モード | [済] | `docs/specs/mode-guidance-recorder-spec.md` |
| 単体のオウム返し・タイマーモード | [済] | `docs/specs/parrot-timer-spec.md` |
| 実機 1 台での 2 体会話・Web からの話しかけ | [済] | `docs/backend-api.md` §5.8.1 / §5.8.1b |
| セットアップ支援（setup-guide・check_setup・Moonshine ライブラリ取得） | [済] | `docs/setup-guide.md` |

### 今後の候補

- 提案 C（WebSocket での逐次フィードバック）、提案 E の残り（`/api/status` の拡充）
- 音響ループ（Phase B-2）の自動化: VAD による自動聞き取りとターン制御（現状 voice は手動 PTT）
- LLM 応答のストリーミング（文ごとに合成して再生開始を早める）
- Bluetooth オーディオ: AtomS3（ESP32-S3）は Bluetooth Classic 非対応のため不可。やるなら ESP32 搭載の別ハードで別計画

---

## 4. 技術スタック

### ホスト

| 用途 | 採用 |
|---|---|
| 言語 | Python 3.12 |
| 環境管理 | uv |
| フレームワーク | FastAPI + Uvicorn |
| スキーマ | Pydantic v2 |
| LLM クライアント | openai SDK（base_url 差し替え） |
| TTS | Irodori-TTS（third_party/irodori-tts, subprocess 経由） |
| STT | Moonshine（third_party/moonshine/python, source dependency） |
| テスト | pytest + httpx |

### ファームウェア

| 用途 | 採用 |
|---|---|
| ビルドシステム | PlatformIO（VS Code 拡張） |
| フレームワーク | Arduino（ESP32-S3） |
| デバイス抽象 | M5Unified |
| HTTP | HTTPClient（Arduino） |
| WebSocket | links2004/WebSockets |
| JSON | bblanchon/ArduinoJson（Session 5 から使用） |

### フロントエンド

| 用途 | 採用 |
|---|---|
| 初期 | 静的 HTML + vanilla JS |
| 将来 | Vite + React + TypeScript（必要に応じて） |

---

## 5. 環境設定チートシート

初めての人向けの詳しい手順は [setup-guide.md](../setup-guide.md)。

```bash
# 初回セットアップ
git clone --recurse-submodules https://github.com/kou1-310/ai-voice-atom.git
cd ai-voice-atom/host && cp .env.example .env && uv sync --extra dev

# Irodori-TTS セットアップ（NVIDIA GPU。GPU なしは --extra cpu）
cd third_party/irodori-tts && uv sync --extra cu128

# Moonshine ネイティブライブラリ（モデル本体は初回に自動取得）
cd host && uv run python scripts/fetch_moonshine_native.py

# 準備状況の点検 → ホスト起動
cd host && uv run python scripts/check_setup.py
cd host && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# テスト実行
cd host && uv run pytest -q

# （任意）モード切替の音声案内
cd host && uv run python scripts/generate_mode_prompts.py

# ファームウェア書き込み
# 1. firmware/atom/.env.template を .env にコピーし WIFI_SSID / WIFI_PASSWORD / HOST_BASE_URL / DEVICE_ID を設定
# 2. VS Code → PlatformIO アイコン → env:m5stack-atoms3 → Upload
```
