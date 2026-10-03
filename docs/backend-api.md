# バックエンド API 仕様

> **この文書の位置づけ**: ホストの API（HTTP・WebSocket）の仕様です。全体のしくみは [architecture.md](architecture.md)、
> ほかの文書は [docs/README.md](README.md) から探せます。

## 1. 目的

本仕様は、AtomS3-Lite とホストマシン間の通信仕様を定義する。

前提は以下の通り。

- AtomS3-Lite は Irodori-TTS、Moonshine、LLM を実行しない
- AtomS3-Lite は録音、送受信、再生、状態通知のみを担当する
- 制御系は HTTP API を利用する
- 音声対話系は WebSocket ストリームを利用する

## 2. 通信方式

### 2.1 採用方式

- HTTP: 設定取得、状態取得、テキスト問い合わせ、再生制御、ヘルスチェック、リレー会話制御
- WebSocket: 音声入力ストリーム、逐次 STT 結果、対話状態通知、リレー会話の `play` push ダウンリンク

### 2.2 接続先の想定

- ホストベース URL: http://HOST_IP:PORT
- WebSocket URL: ws://HOST_IP:PORT/ws/audio

HTTPS と WSS は将来対応とし、初期フェーズでは LAN 内利用を前提に HTTP と WebSocket を採用する。

## 3. 認証方針

初期フェーズでは LAN 内の閉域利用を前提とし、最低限の共有トークン方式を許容する。

- HTTP ヘッダー: X-Device-Token
- WebSocket 接続時: query またはヘッダーで token を送る

例:

- GET /api/status
- Header: X-Device-Token: local-dev-token

## 4. デバイス識別

すべての主要リクエストには以下を含める。

- device_id: デバイス固有 ID
- session_id: 会話または処理単位の ID
- mode: voice / web / relay

device_id は AtomS3-Lite の固定 ID を利用し、session_id は処理開始時に生成する。`device_id` は
`device_profiles.json` でペルソナ（system プロンプト・声色 ref_wav・表示名）の出し分けにも使われ、
会話履歴は `device_id::session_id` の複合キーで分離される。`relay` モードは実機2台のホスト統括
リレー会話（§5.8 / §6.7）で使う。

## 5. HTTP API

> **実装状況の凡例:** [実装] = 実装・テスト済み / [計画] = 本仕様の設計だが未実装。
> ドキュメントとコードが食い違う場合はコードを正とする。

### 5.1 GET /api/health  [実装]

用途:

- ホスト側の疎通確認

実際のレスポンス例:

```json
{
  "status": "ok",
  "timestamp": "2026-06-20T10:00:00+00:00",
  "services": { "llm": "configured", "tts": "configured", "stt": "configured" },
  "paths": {
    "irodori_root": "/.../third_party/irodori-tts",
    "moonshine_root": "/.../third_party/moonshine"
  }
}
```

`services` の各値は `configured` / `not-configured`。LLM は base_url + api_key、TTS/STT は
submodule のパス存在で判定する。

### 5.2 GET /api/status  [実装（一部）]

用途:

- 現在状態の取得
- Web UI での表示
- デバイスのポーリング確認

実際のレスポンス例:

```json
{
  "device_id": "atoms3-001",
  "mode": "web",
  "network": { "wifi_connected": true, "ip": "192.168.1.10" },
  "audio": { "is_playing": false, "volume": 70 },
  "backend": {
    "llm": "configured",
    "tts": "configured",
    "stt": "configured"
  },
  "active_session_id": null
}
```

各フィールドの実装状況:

- `backend.{llm,tts,stt}` **[実装]** — `configured` / `not-configured`（判定条件は §5.1 と同じ）。
- `network.ip` **[実装]** — ホストの LAN IP（実機が接続すべき宛先の確認用。取得不能時は `127.0.0.1`）。
  `wifi_connected` はホストが応答できている＝常に `true`。
- `active_session_id` **[実装]** — 進行中（`session.start` 済みで未 `audio.end`）の STT セッション ID。無ければ `null`。
- `audio.{is_playing,volume}` **[計画]** — 再生はデバイス側が担うため、ホストは進行状況を保持しない（現状は既定値）。

### 5.3 POST /api/chat  [実装]

用途:

- テキストベース問い合わせ
- Web UI からの問い合わせ
- 音声対話後の最終問い合わせ実行

リクエスト例:

```json
{
  "device_id": "atoms3-001",
  "session_id": "sess-20260606-001",
  "mode": "web",
  "input_text": "今日の予定を教えて",
  "response_format": "text+audio",
  "audio": {
    "format": "wav",
    "sample_rate": 16000
  },
  "context": {
    "conversation_id": "conv-001"
  }
}
```

レスポンス例:

```json
{
  "session_id": "sess-20260606-001",
  "message_id": "msg-001",
  "llm_text": "本日の予定は3件あります。",
  "audio": {
    "format": "wav",
    "encoding": "base64",
    "sample_rate": 16000,
    "channels": 1,
    "data": "UklGR..."
  }
}
```

注記:

- `audio` が返るのは `response_format: "text+audio"` かつ `AI_VOICE_ATOM_INCLUDE_AUDIO_INLINE=true` のときのみ。既定では `audio` は `null`（テキストのみ）。
- このエンドポイントは主に Web UI / デバッグ用。実機は §5.3.1 の `/api/chat/audio` を使う。
- 長い音声は署名付き URL または別取得 API に切り替えてよい。

### 5.3.1 POST /api/chat/audio  [実装]（実機の主経路）

用途:

- AtomS3-Lite からのテキスト問い合わせ
- 応答を JSON/base64 を介さず **生の `audio/wav` バイナリ**で返し、デバイスがチャンク受信しながら逐次再生する（応答全体を RAM に保持しない）

リクエスト: §5.3 と同じ `ChatRequest`（`input_text` 必須）。

レスポンス:

- `Content-Type: audio/wav`、ボディは 16kHz / mono / 16-bit PCM の正規 WAV
- LLM 応答テキストはヘッダで返す
  - `X-LLM-Text`: URL エンコード済みテキスト
  - `X-LLM-Text-B64`: base64(UTF-8) テキスト
  - `Content-Length`: WAV バイト数

実装メモ:

- TTS は常駐サイドカー（`AI_VOICE_ATOM_TTS_SERVER_URL`）優先、不調時は `infer.py` の subprocess へフォールバック。
- `host/voices/reference.wav` があればその声色で合成する（話者リファレンス音声）。
- 失敗は LLM/TTS 利用不可 → HTTP 503、空入力 → HTTP 400。

### 5.3.2 POST /api/chat/say  [実装]（実機リレー会話の音声取得）

用途:

- リレー会話（マルチデバイス）で、ホストから `play` を push されたデバイスが**自分のターンの音声**を取得する
- 指定テキストを `device_id` のペルソナ声色で TTS した WAV を返す。**LLM は通さない**（テキストはホスト側で生成済み）

リクエスト例（`SayRequest`）:

```json
{
  "device_id": "atoms3-001",
  "text": "こんにちは、よろしくね。",
  "persona_id": "atoms3-002"
}
```

- `persona_id` 省略可。指定するとそのペルソナの声色で合成する（実機 1 台で 2 体を鳴らす場合に、
  ホストが `play` の `persona` で指示した値をデバイスがそのまま載せる）。省略時は `device_id` の声色。

レスポンス:

- `Content-Type: audio/wav`、ボディは 16kHz / mono / 16-bit PCM の正規 WAV（`/api/chat/audio` と同形式）
- `Content-Length`: WAV バイト数

実装メモ:

- 声色は `device_profiles.json` の `device_id → ref_wav` を採用（未登録ならグローバル既定 `tts_ref_wav` へフォールバック）。
- 失敗は TTS 利用不可 → HTTP 503、空入力 → HTTP 400。

### 5.4 POST /api/tts  [実装]

用途:

- テキストから音声だけを生成する
- デバッグ時の TTS 単体確認

リクエスト例:

```json
{
  "device_id": "atoms3-001",
  "session_id": "sess-20260606-002",
  "text": "接続を確認しました",
  "voice": "default",
  "audio": {
    "format": "wav",
    "sample_rate": 16000
  }
}
```

### 5.5 POST /api/playback/stop  [実装]

用途:

- 再生停止
- Web UI からの停止命令

リクエスト例:

```json
{
  "device_id": "atoms3-001",
  "session_id": "sess-20260606-001"
}
```

レスポンス例:

```json
{
  "status": "accepted"
}
```

### 5.6 POST /api/device/mode  [計画]（未実装）

用途:

- モード変更通知
- Web UI 側との状態同期

リクエスト例:

```json
{
  "device_id": "atoms3-001",
  "mode": "voice",
  "reason": "button_long_press"
}
```

### 5.7 GET /api/devices  [実装]（マルチデバイス）

用途:

- 登録済みデバイスプロファイル（ペルソナ）の一覧取得
- フロントの会話相手選択肢、リレー会話の `device_a` / `device_b` 選択に使う

レスポンス例（`DevicesResponse`）:

```json
{
  "devices": [
    { "device_id": "atoms3-001", "display_name": "あおい" },
    { "device_id": "atoms3-002", "display_name": "そら" }
  ]
}
```

注記:

- 中身は `device_profiles.json`（`AI_VOICE_ATOM_DEVICE_PROFILES`）の `device_id` と `display_name`。
- プロファイル未配置で空のときは、UI が必ず1体は選べるよう既定の `settings.device_id` 1件にフォールバックする。

### 5.8 リレー会話 API  [実装]（Phase B-1・ホスト統括会話。実機 2 台、または 1 台で 2 体）

ホストがターンを統括し、各ターンのテキストを LLM で生成して話者ペルソナの出力先の実機へ WebSocket で
`play` を push、実機の `played` ack を待って次のターンへ進む。空気越し STT を介さないので会話内容が安定する。
同時に動く会話（リレー or `/chat`）は1つ（プロセス内の単一インスタンス）。詳細な WS プロトコルは §6.7。

#### 5.8.1 POST /api/conversation/start  [実装]

リクエスト例（`ConversationStartRequest`）:

```json
{
  "device_a": "atoms3-001",
  "device_b": "atoms3-002",
  "opening_text": "今日はいい天気だね。",
  "max_turns": 6,
  "output_device_a": null,
  "output_device_b": null
}
```

- `device_a` / `device_b` は話者のペルソナ（`device_profiles.json` のキー）。別 ID であること。
- `output_device_a` / `output_device_b` はそのペルソナの発話を鳴らす実機。省略時はペルソナと同じ ID の実機
  （= 実機 2 台構成）。**両方に同じ実機を指定すると 1 台で 2 体の会話**を再生する。
- `opening_text` 省略可。省略時は最初の話者に既定のキックオフ文を渡して LLM に始めさせる。
- `max_turns` 既定 6。
- 出力先の実機が `/ws/audio` に `register` 済み（接続中）であること。

レスポンス例（`ConversationStartResponse`）:

```json
{
  "status": "started", "device_a": "atoms3-001", "device_b": "atoms3-002", "max_turns": 6,
  "output_device_a": "atoms3-001", "output_device_b": "atoms3-002"
}
```

エラー: 入力不正（同一ペルソナ・`max_turns<=0`）・会話/チャット実行中 → 400、出力先の実機が未接続 → 409。

#### 5.8.1b POST /api/conversation/chat  [実装]（Web から実機に話しかける）

`persona_id` のペルソナが `text` に応答し、`output_device`（省略時は `persona_id` と同じ ID の実機）が
その声色で再生する。**再生完了（`played` ack）まで待ってから**応答テキストを返す。`session_id` ごとに
会話履歴が続く（履歴キーは `persona_id::session_id`）。リレー会話の実行中は受け付けない。

```json
{ "persona_id": "atoms3-001", "text": "今日はどうだった？", "output_device": "atoms3-001", "session_id": "webchat-1" }
```

レスポンス例（`ConversationChatResponse`）:

```json
{ "persona_id": "atoms3-001", "output_device": "atoms3-001", "text": "今日はね、…" }
```

エラー: 空テキスト・会話/チャット実行中 → 400、出力先が未接続 → 409、LLM 失敗・再生 ack タイムアウト → 503。
`POST /api/conversation/stop` で再生待ちを打ち切れる。

#### 5.8.2 POST /api/conversation/stop  [実装]

進行中のリレー会話を停止する。レスポンス: `{ "status": "stopped" }`。

#### 5.8.3 GET /api/conversation/status  [実装]

レスポンス例（`ConversationStatusResponse`）:

```json
{
  "running": true,
  "chatting": false,
  "device_a": "atoms3-001",
  "device_b": "atoms3-002",
  "output_device_a": "atoms3-001",
  "output_device_b": "atoms3-001",
  "turn": 3,
  "max_turns": 6,
  "connected_devices": ["atoms3-001"]
}
```

- `connected_devices` は現在 `/ws/audio` に接続中（push 可能）なデバイス ID 一覧。
- `chatting` は `/api/conversation/chat` の処理中（再生完了待ちを含む）か。
- `output_device_a` / `output_device_b` は実行中（または直前）の会話で各ペルソナを鳴らした実機。

## 6. WebSocket API

> **実装状況:** `/ws/audio` のセッション制御（`session.start` / `audio.chunk` / `audio.end`）と
> 最終文字起こし `stt.final` は **[実装]**。`audio.chunk` のフィールド名は実装では `data`（下記 JSON 例の
> `payload` / `seq` ではない）。`stt.partial` / `llm.started` / `llm.result` / `tts.ready` および
> 音声取得 API `/api/audio/result/{session_id}`（§6.6）は **[計画]** で未実装。未知のメッセージには
> `event.ack` を、エラーには `{ "type": "error", "session_id", "message" }` を返す。

### 6.1 エンドポイント

- GET /ws/audio

用途:

- マイク音声チャンクの送信
- 逐次 STT 結果の受信
- 処理状態のイベント受信
- 音声対話セッション制御

### 6.2 接続開始時のハンドシェイク

接続直後、デバイスは最初に start イベントを送る。

```json
{
  "type": "session.start",
  "device_id": "atoms3-001",
  "session_id": "sess-20260606-003",
  "mode": "voice",
  "audio": {
    "codec": "pcm_s16le",
    "sample_rate": 16000,
    "channels": 1,
    "chunk_ms": 100
  }
}
```

ホストは ack を返す。

```json
{
  "type": "session.ack",
  "session_id": "sess-20260606-003",
  "accepted": true
}
```

### 6.3 音声チャンク送信

音声チャンクは 2 方式を許容する。

1. バイナリフレーム
2. JSON + base64

初期実装では扱いやすさを優先して JSON + base64 を許容し、性能要件が厳しくなったらバイナリフレームへ移行できるようにする。

JSON 例（**実装はフィールド名 `data`**。`seq` / `payload` は計画段階の表記）:

```json
{
  "type": "audio.chunk",
  "session_id": "sess-20260606-003",
  "data": "base64-encoded-pcm"
}
```

### 6.4 録音終了通知

```json
{
  "type": "audio.end",
  "session_id": "sess-20260606-003"
}
```

### 6.5 サーバー送信イベント

`stt.final` のみ実装。以下 `stt.partial` / `llm.started` / `llm.result` / `tts.ready` は **[計画]**。

#### stt.partial  [計画]

```json
{
  "type": "stt.partial",
  "session_id": "sess-20260606-003",
  "text": "きょうの",
  "is_final": false
}
```

#### stt.final  [実装]

```json
{
  "type": "stt.final",
  "session_id": "sess-20260606-003",
  "text": "今日の予定を教えて",
  "is_final": true
}
```

#### llm.started  [計画]

```json
{
  "type": "llm.started",
  "session_id": "sess-20260606-003"
}
```

#### llm.result  [計画]

```json
{
  "type": "llm.result",
  "session_id": "sess-20260606-003",
  "text": "本日の予定は3件あります。"
}
```

#### tts.ready  [計画]

```json
{
  "type": "tts.ready",
  "session_id": "sess-20260606-003",
  "audio": {
    "format": "wav",
    "fetch": "/api/audio/result/sess-20260606-003"
  }
}
```

#### error

```json
{
  "type": "error",
  "session_id": "sess-20260606-003",
  "code": "STT_TIMEOUT",
  "message": "STT processing timed out"
}
```

### 6.6 音声取得 API  [計画]（未実装）

WebSocket で wav 本体を直接返さず、取得先だけ返す構成を標準とする。

- GET /api/audio/result/{session_id}

レスポンス:

- Content-Type: audio/wav

理由は以下の通り。

- 実装が単純
- 再取得しやすい
- Web UI とデバイスの双方で共用しやすい

### 6.7 リレー会話の push プロトコル  [実装]（Phase B-1）

`/ws/audio` は STT アップリンク（§6.2–6.5）と、ホスト→デバイスの **`play` push ダウンリンク**を兼ねる。
`relay` モードのデバイスはこの接続でターン指示を受け取り、自分の音声を `/api/chat/say`（§5.3.2）で取得・再生する。
会話全体の開始/停止は HTTP の §5.8 で行う。

#### register / registered  [実装]

デバイスは接続後、自身を push 対象として登録する。`session.start`（§6.2）でも `device_id` があれば
同時に登録される（後方互換）。

```json
// デバイス → ホスト
{ "type": "register", "device_id": "atoms3-001" }
// ホスト → デバイス
{ "type": "registered", "device_id": "atoms3-001" }
```

#### play  [実装]（ホスト → デバイス）

ホストが話者デバイスへ自分のターンのテキストを push する。デバイスはこの `text` を `/api/chat/say` に
送って WAV を取得・再生する。`persona` は声色を借りるペルソナで、デバイスは `/api/chat/say` の
`persona_id` にそのまま載せる（実機 1 台で 2 体の会話・Web からの話しかけで自分以外の声になる）。
Web チャットの `turn` は 10000 以上（リレーのターン番号と衝突しない）。

```json
{ "type": "play", "text": "こんにちは、よろしくね。", "turn": 0, "persona": "atoms3-001" }
```

#### played  [実装]（デバイス → ホスト）

再生完了の ack。ホストはこれを待って次のターンへ進む。`turn` は受け取った `play` と同じ値を返す。

```json
{ "type": "played", "device_id": "atoms3-001", "turn": 0 }
```

待ち上限は `AI_VOICE_ATOM_RELAY_ACK_TIMEOUT`（既定 180 秒。TTS が GPU 競合で遅延しうるため、デバイス側
`/api/chat/say` のタイムアウトより長く取る）。ack が来ないと会話を中断し状態を戻す。デバイスは再生中も WS を
回し続け、合成後に切断した場合は再接続してから `played` を返すので ack の取りこぼしに強い。

## 7. エラー応答形式とエラーコード

### 7.1 応答形式  [実装]

すべての HTTP エラー応答は以下の統一形式で返す（`app/errors.py`）。

```json
{
  "error": {
    "code": "INVALID_AUDIO_FORMAT",
    "message": "Only wav output is supported"
  }
}
```

バリデーション失敗（422）は原文の詳細を `error.errors` に添える。

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Request validation failed",
    "errors": [ { "loc": ["body", "audio"], "msg": "...", "type": "..." } ]
  }
}
```

### 7.2 HTTP ステータス → コードの既定マッピング  [実装]

ルーターは従来どおり `HTTPException`（`ValueError` → 400 / `RuntimeError` → 503）を投げ、
ハンドラがステータスを `code` へ変換する。個別コードを明示したい箇所は `ApiError` を使う
（例: TTS の形式不正は `INVALID_AUDIO_FORMAT`）。

| HTTP | code | 意味 |
| --- | --- | --- |
| 400 | INVALID_REQUEST | 不正な入力（空入力など） |
| 401 | UNAUTHORIZED | 認証失敗 |
| 403 | FORBIDDEN | 権限なし |
| 404 | NOT_FOUND | 対象なし |
| 409 | PLAYBACK_BUSY | 再生中のため要求を処理できない |
| 422 | INVALID_REQUEST | リクエスト検証失敗 |
| 500 | INTERNAL_ERROR | 内部エラー |
| 503 | BACKEND_UNAVAILABLE | バックエンド（LLM/TTS/STT）利用不可 |
| 504 | TIMEOUT | タイムアウト |

### 7.3 個別コード（`ApiError` で明示）  [一部実装]

| code | 意味 | 状況 |
| --- | --- | --- |
| INVALID_AUDIO_FORMAT | 音声形式不正 | [実装] `POST /api/tts` で wav 以外 |
| INVALID_MODE | 不正なモード | [計画] |
| STT_TIMEOUT | STT タイムアウト | [計画] WebSocket の `error` イベント |
| LLM_TIMEOUT | LLM タイムアウト | [計画] |
| TTS_TIMEOUT | TTS タイムアウト | [計画] |

## 8. 推奨状態遷移

デバイスのセッション状態は以下を基本とする。

- idle
- recording
- streaming
- transcribing
- generating_response
- synthesizing
- playing
- completed
- error

WebSocket と HTTP のレスポンスは、可能な限り現在状態を返せるようにする。

## 9. 実装メモ

- HTTP は制御系に限定する
- 音声対話の低遅延要件は WebSocket で満たす
- Moonshine の partial 結果は UI 表示とデバッグに利用できる
- Irodori-TTS の結果音声は wav を標準とする
- AtomS3-Lite では再生前に wav ヘッダーとサンプルレートを検証する