# しくみとコードの歩き方

このリポジトリをはじめて読む人が、全体のしくみと「どこに何が書いてあるか」をつかむための文書です。
使い方は [manual.md](manual.md)、準備の手順は [setup-guide.md](setup-guide.md) を参照してください。

> コードと文書が食い違っていたら、コードが正しいものとして扱います。

## 1. 全体像

「小さなデバイス」と「頭脳役の PC」の 2 つでできています。デバイスは録音・再生・ボタン・LED だけを担当し、
AI の処理はすべて PC（ホスト）が受け持ちます。

```
 ┌──────────────────────┐        Wi-Fi          ┌───────────────────────────────────────┐
 │ デバイス（実機）        │  HTTP / WebSocket    │ ホスト PC                               │
 │  AtomS3-Lite          │ ◀─────────────────▶ │  host/      FastAPI サーバー（ポート 8000）│
 │  + Atomic Echo Base   │                      │   ├ LLM（Ollama など）  返事の文章を考える  │
 │  firmware/atom/       │                      │   ├ Irodori-TTS        文章を声にする     │
 └──────────────────────┘                      │   └ Moonshine          声を文章にする     │
                                                │  frontend/  Web 画面（/frontend で配信）   │
        ブラウザ（PC・スマホ） ─────────────────▶ └───────────────────────────────────────┘
```

| フォルダ | 中身 | 言語・道具 |
|---|---|---|
| `firmware/atom/` | デバイスのファームウェア | C++（Arduino / PlatformIO） |
| `host/` | ホストのサーバー | Python 3.11 以上（FastAPI、uv で管理） |
| `frontend/` | Web 画面。ホストがそのまま配信する静的ファイル | HTML / JavaScript / CSS |
| `third_party/` | 外部の部品（git サブモジュール）。`irodori-tts`＝音声合成、`moonshine`＝音声認識 | Python |
| `hardware/` | フィギュア型ケースの 3D データ | Python（生成スクリプト）・STL |
| `docs/` | ドキュメント（[一覧](README.md)） | |

デバイスには 6 つのモードがあります。ホストを使う 3 つ（チャット・音声会話・リレー会話）と、
ホストも Wi-Fi も要らない 3 つ（録音再生・オウム返し・タイマー）です。

## 2. 会話 1 回の流れ

### チャット（web モード）

ボタンを押してから声が出るまでの流れです。デバイスの主な経路で、ほかのモードもこれを土台にしています。

```
デバイス                         ホスト                                   外部
  │ POST /api/chat/audio          │                                        │
  │  {device_id, input_text} ───▶ │ ChatService                            │
  │                               │  1. device_id からキャラクターを引く      │
  │                               │  2. LLM に返事を作らせる ───────────────▶ │ LLM
  │                               │  3. 返事を声にする ────────────────────▶ │ Irodori-TTS
  │                               │  4. 16kHz / mono / 16-bit の WAV に変換  │
  │ ◀─── audio/wav（本文は音声）   │                                        │
  │  受け取りながら再生             │                                        │
```

- 返事は JSON ではなく、**音声そのもの**が返ります。文章はヘッダ（`X-LLM-Text-B64`）に入ります。
  デバイスはメモリが小さいので、受け取りながら少しずつ再生します。
- 会話の履歴は「デバイス ID ＋ セッション ID」ごとに分けて覚えます。

### 音声会話（voice モード）

チャットの前に、声を文章にする手順が入ります。

1. デバイスが WebSocket `/ws/audio` につなぎ、録音した音を小分けにして送ります（`session.start` → `audio.chunk` → `audio.end`）。
2. ホストが Moonshine で文字起こしし、`stt.final` で文章を返します。
3. デバイスはその文章で `/api/chat/audio` を呼びます。以降はチャットと同じです。

### リレー会話（relay モード）

ホストが進行役になって、キャラクター同士を会話させます。Web 画面から始めます。

1. デバイスは `/ws/audio` につなぎ、自分の ID を登録して待ちます。
2. ホスト（`ConversationOrchestrator`）が 1 ターンぶんのセリフを LLM で作り、話す番のデバイスへ `play` を送ります。
3. デバイスは `/api/chat/say` でそのセリフを声にしてもらって再生し、終わったら `played` を返します。
4. ホストは `played` を待ってから次のターンへ進みます。

`play` には「どのキャラクターの声で話すか」が入っているので、実機 1 台で 2 体ぶんの声を出すこともできます。

### 単体モード（録音再生・オウム返し・タイマー）

ホストを使いません。デバイスの中だけで完結します。録音はフラッシュメモリ（LittleFS）に保存します。

## 3. ホスト（`host/`）

要求は「入口 → 段取り → 外部とのやりとり」の 3 層を通ります。

```
app/api/        入口。URL ごとの受け口（FastAPI のルーター）
   │
app/services/   段取り。会話の流れや履歴を管理する
   │
app/adapters/   外部とのやりとり。LLM・音声合成・音声認識を呼ぶ
```

| ファイル | 役割 |
|---|---|
| `app/main.py` | サーバーの起動。ルーターの登録と Web 画面（`/frontend`）の配信 |
| `app/config.py` | 設定の読み込み。すべて `AI_VOICE_ATOM_` で始まる環境変数（`host/.env`） |
| `app/api/chat.py` | `/api/chat`、`/api/chat/audio`（デバイスの主経路）、`/api/chat/say` |
| `app/api/conversation.py` | `/api/conversation/*`（リレー会話の開始・停止・状態、実機への話しかけ） |
| `app/api/ws_audio.py` | `WS /ws/audio`（音声認識と、実機の登録・再生指示） |
| `app/api/device.py` / `health.py` / `tts.py` / `playback.py` | 状態・キャラクター一覧、死活確認、音声合成だけ、再生停止 |
| `app/services/chat_service.py` | 会話の中心。キャラクターの指示文を組み立て、LLM と音声合成を呼び、履歴を持つ |
| `app/services/conversation_orchestrator.py` | リレー会話の進行役 |
| `app/services/connection_manager.py` | つながっている実機（WebSocket）の管理 |
| `app/services/audio_session_service.py` | 音声認識のセッション（届いた音を貯めて文字起こしする） |
| `app/adapters/openai_client.py` | OpenAI 互換の LLM（Ollama など）を呼ぶ |
| `app/adapters/irodori_tts_adapter.py` | Irodori-TTS で声を作る（常駐プロセス `tts_server.py`、または `infer.py` を都度実行） |
| `app/adapters/moonshine_adapter.py` | Moonshine で文字起こしする |
| `app/device_profiles.py` | キャラクター定義（`device_profiles.json`）の読み込み |
| `app/utils/wav.py` | 音声をデバイス向けの形式（16kHz / mono / 16-bit）へ変換する |
| `scripts/` | 補助スクリプト（準備状況の点検、音声案内の生成など。[host/README.md](../host/README.md)） |
| `tests/` | 自動テスト。実物の AI は動かさず、偽物（フェイク）を差し込んで確かめる |

API の詳細は [backend-api.md](backend-api.md) にあります。

## 4. デバイス（`firmware/atom/`）

ファームウェアの本体は `src/main.cpp` の 1 ファイルです。上から次の順に並んでいます。

| まとまり | 内容 |
|---|---|
| 定数・状態 | モード名、LED の色、時間のしきい値、各モードの状態変数 |
| LED・効果音・モード切替 | `setLedColor`、`announceMode`、`toggleMode` |
| Wi-Fi・ホスト確認 | `connectWiFiIfNeeded`、`checkBackendHealthIfNeeded` |
| 再生 | `streamWavPlayback`（ホストから受け取りながら再生）、`playPcmFile`（保存した録音を再生） |
| チャット・音声会話・リレー会話 | `sendChatRequest`、`startVoiceCapture` / `finishVoiceCapture`、`playRelayTurn` |
| 録音再生・オウム返し・タイマー | `startRecorderCapture` ほか、`parrotStep`、`timerTick` |
| `setup()` / `loop()` | 起動処理と、ボタン操作を各モードへ振り分けるメインループ |

読むときに知っておくとよいこと:

- **マイクとスピーカーは同時に使えません**（同じ I2S を共有）。録音の前にスピーカーを止め、再生の前にマイクを止めます。
- **メモリが小さい**（PSRAM 無し）ので、音声は一度に全部持たず、少しずつ受け取り・再生・保存します。
- **`loop()` を止めない**ことが大事です。止まっているあいだはボタンを見られず、クリックを取りこぼします。
  ネットワーク処理やライブラリ呼び出しが待ちに入らないよう工夫しています。
- 設定（Wi-Fi、ホストの URL、デバイス ID、音量など）は `.env` に書き、ビルド時に `load_env.py` が埋め込みます。
- モード切替の音声案内は `host/scripts/generate_mode_prompts.py` が作り、`include/mode_prompts.h` として取り込みます
  （無くてもビルドでき、その場合はチャイムで代用します）。

操作方法・LED・設定項目は [firmware/atom/README.md](../firmware/atom/README.md) にあります。

## 5. Web 画面（`frontend/`）

`index.html`・`app.js`・`style.css` だけの静的なページで、ホストが `/frontend` で配信します。
画面からホストの API を呼んで、キャラクター同士の会話や実機への話しかけを操作します。
デザインの決まりは [DESIGN.md](../DESIGN.md) にあります。

## 6. 全体で守っている取り決め

| 取り決め | 内容 |
|---|---|
| 音声の形式 | デバイスとやりとりする音声は **16kHz / mono / 16-bit の WAV**（44 バイトのヘッダ）に統一する |
| キャラクターの出し分け | デバイス ID（`DEVICE_ID`）で、性格・声・会話履歴を分ける。未登録の ID は共通設定で動く |
| エラーの扱い | 外部の部品が使えない・失敗したときは HTTP 503、入力が正しくないときは HTTP 400 を返す |
| 設定 | ホストの設定は `AI_VOICE_ATOM_` で始まる環境変数だけで行う |
| 言葉 | コメント・画面の文言・ドキュメントは日本語で書く |

## 7. 変更するときの入り口

| やりたいこと | 見る場所 |
|---|---|
| キャラクターを増やす・変える | `host/device_profiles.json`（[host/README.md](../host/README.md)） |
| ボタン操作やモードを変える | `firmware/atom/src/main.cpp` の `loop()` と各モードの関数 |
| モード案内の文面を変える | `host/scripts/generate_mode_prompts.py` の `PROMPTS` |
| API を足す・変える | `host/app/api/` → `services/` → `adapters/`、あわせて [backend-api.md](backend-api.md) |
| Web 画面を変える | `frontend/`（[DESIGN.md](../DESIGN.md)） |
| 動作を確かめる | ホストは `cd host && uv run pytest`、実機は [verification-guide.md](verification-guide.md) |

機能を足すときの進め方（仕様を先に書く、テストが通るまで完了としない、など）は
[ai-native-rules.md](ai-native-rules.md) にまとめています。過去の仕様は [specs/](specs/)、
実装の経緯は [plans/](plans/) にあります。
