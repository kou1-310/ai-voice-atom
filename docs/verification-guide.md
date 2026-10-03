# 動作確認手順書

> **この文書の位置づけ**: どこまで動いているかを順番に確かめ、原因を切り分けるための手順書です。
> ふだんの使い方は [manual.md](manual.md)、ほかの文書は [docs/README.md](README.md) から探せます。

最終更新: 2026-09-23

この手順書は **「どの手順で・どこまで・何が確認できるか」** を先に示し、上から順に
実行すれば疎通範囲が少しずつ広がるように構成しています。各ステップには次を明記します。

- 🎯 **確認できること** … このステップを通すと何が保証されるか
- ✅ **前提** … このステップを実行するために必要な状態
- ⛔ **これだけでは分からないこと** … 次のステップに進む必要がある範囲

> コマンドは **Windows PowerShell** を基準に記載します。macOS / Linux は差分のみ
> 「💻 他OS」として併記します。

---

## 確認レイヤー早見表

| レイヤー | ステップ | 確認できること | 前提 | 実機 |
|---|---|---|---|---|
| **L1 ホスト単体** | 1.0 自動テスト | ホスト側ロジックの回帰 | uv 同期済み | 不要 |
| | 1.1 起動 | プロセスが立ち上がる（`.env` 自動ロード） | Step 0 完了 | 不要 |
| | 1.2 health | ホストが応答する | 1.1 | 不要 |
| | 1.3 status | LLM/TTS/STT の構成状態 | 1.1 | 不要 |
| | 1.4 chat(text) | **LLM 疎通**（実モデル応答） | LLM サーバー稼働 | 不要 |
| | 1.5 tts | **TTS 音声生成** | irodori-tts 導入 | 不要 |
| | 1.6 chat/audio | **LLM→TTS 連携**（実機と同じ WAV 経路） | 1.4 + 1.5 | 不要 |
| | 1.7 ws/audio | **STT（WebSocket）** | Moonshine ネイティブライブラリ配置 | 不要 |
| | 1.8 playback/stop | 停止 API が応答する | 1.1 | 不要 |
| | 1.9 frontend | ブラウザUIの一連動作 | 1.1〜1.6 | 不要 |
| **L2 実機＋スタブ** | Phase A | **実機のWAV再生経路**（Wi-Fi→HTTP→WAV ストリーム→I2S） | スタブ起動 | **必要** |
| **L3 実機＋本番** | L3 | **フルE2E（web）**（ボタン短押し→LLM→TTS→実機発話） | L1 全通過 + 実機 | **必要** |
| | L3b | **フルE2E（voice 録音）**（録音→STT→LLM→TTS→発話） | L3 + STT 導入 | **必要** |
| | L3c | **リレー会話・話しかけ**（ホスト統括。実機 2 台、または 1 台で 2 体） | L3 | **必要（1〜2 台）** |
| | L3d | **録音再生（単体）・音声案内** | 書き込み済み実機 | **必要** |
| | L3e | **オウム返し・タイマー（単体）** | 書き込み済み実機 | **必要** |

**読み方の例**
- 「実機がホストに届くか」だけ見たい → Phase A まで（LLM/TTS 不要）。
- 「実機がちゃんと喋るか」を見たい → L1 を全部通してからレイヤー3。
- 実機が無い → L1 だけで API は全部確認できる。

---

## Step 0 — 前提条件チェックリスト

初めて準備する場合は [docs/setup-guide.md](setup-guide.md) の手順で揃えてください。
`cd host && uv run python scripts/check_setup.py` で下記の大半を自動で点検できます。

```
[ ] git clone --recurse-submodules 済み
[ ] host/.env を .env.example からコピーして編集済み
[ ] cd host && uv sync --extra dev 済み
[ ] LLM サーバー（Ollama / LM Studio など）が起動している         … 1.4 以降で必要
[ ] third_party/irodori-tts で uv sync --extra cu128（または cpu）済み … 1.5 以降で必要
[ ] scripts/fetch_moonshine_native.py で Moonshine のライブラリ配置済み … 1.7 で必要
```

### ℹ️ `.env` は自動で読み込まれます

`app/config.py` がモジュール読み込み時に **`host/.env` を自動ロード**します
（既存の環境変数や `uvicorn --env-file` の方が優先され、上書きはしません）。
そのため `uv run uvicorn app.main:app --reload --host 0.0.0.0` だけで `.env` が反映されます。

- 別ファイルを使いたい場合: `AI_VOICE_ATOM_ENV_FILE=path\to\.env` を指定するか、`--env-file` を併用。
- `/api/status` の `llm` が `not-configured` の場合は、`host/.env` が無い／`LLM_BASE_URL`・`LLM_API_KEY` が
  未設定／別の環境変数で上書きされている、のいずれか（Step 1.3 参照）。

---

## レイヤー1 — ホスト単体（実機不要）

### Step 1.0 — 自動テスト（所要 < 1 分）

🎯 **確認できること:** ホスト側ロジック（API・WAV処理・アダプタ）の回帰がないこと
✅ **前提:** `uv sync --extra dev` 済み

```powershell
Set-Location <リポジトリ>\host
uv run pytest -v
uv run python scripts/check_setup.py   # 準備状況（submodule・TTS 環境・Moonshine・LLM）の点検
```

**期待結果:** すべてグリーン（2026-09-23 時点で 90 件。件数はテスト追加で増える。赤が無いことが要点）。
`check_setup.py` は `[NG]` が無いこと。
⛔ これは外部サービス（実LLM/実機）には触れません。疎通確認は 1.4 以降で行います。

失敗がある場合はここで止まり、`uv sync --extra dev` の実行漏れを疑うこと。

---

### Step 1.1 — ホスト起動

🎯 **確認できること:** ホストプロセスが立ち上がり、`host/.env` が自動反映されること
✅ **前提:** Step 0 完了

```powershell
Set-Location <リポジトリ>\host
uv run uvicorn app.main:app --reload --host 0.0.0.0
```

ターミナルに以下が出れば起動成功:

```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

以降のコマンドはすべて **別のターミナル** で実行する（このターミナルは起動したまま）。

> `host/.env` は自動ロードされます。別ファイルを使う場合のみ `--env-file path\to\.env` を付ける。
> 直後の Step 1.3 で `llm: configured` になっているか必ず確認すること。

---

### Step 1.2 — ヘルスチェック

🎯 **確認できること:** ホストが HTTP 応答を返すこと（実機が見る `/api/health` と同じ）
✅ **前提:** 1.1

```powershell
Invoke-RestMethod http://localhost:8000/api/health | ConvertTo-Json -Depth 5
```

**期待結果:** `status: ok` が返る。`services` に各バックエンドの構成状態が出る。
⛔ `health` は LLM 未構成でも 200 を返します。構成状態の判定は 1.3 で確認。

💻 他OS: `curl -s http://localhost:8000/api/health | python3 -m json.tool`

---

### Step 1.3 — ステータス確認（LLM / TTS / STT）

🎯 **確認できること:** 各バックエンドが `configured` かどうか（=どのステップまで通せるか）
✅ **前提:** 1.1

```powershell
Invoke-RestMethod http://localhost:8000/api/status | ConvertTo-Json -Depth 5
```

| フィールド | `configured` になる条件 |
|---|---|
| `backend.llm` | `host/.env`（自動ロード）か環境変数で `LLM_BASE_URL` と `LLM_API_KEY` が設定済み |
| `backend.tts` | `third_party/irodori-tts/` が存在する |
| `backend.stt` | `third_party/moonshine/` が存在する |

> `llm` が `not-configured` の場合は、`host/.env` が無い／`LLM_BASE_URL`・`LLM_API_KEY` 未設定。
> `tts`/`stt` はパス存在チェックのみで `configured` になります。実際に動くかは 1.5 / 1.7 で確認。

その他のフィールド:

- `network.ip` … ホストの LAN IP。**実機の `HOST_BASE_URL` に設定すべき宛先**がここで分かる（Phase A の IP 確認に流用可）。
- `active_session_id` … 進行中の STT セッション ID。録音していなければ `null`（Step 1.7 / voice モードで使用）。

> 💡 **エラー応答形式（全 API 共通）:** 失敗時は `{"error": {"code": ..., "message": ...}}` で返る。
> 例えば不正な音声形式は `400 INVALID_AUDIO_FORMAT`、バックエンド未構成は `503 BACKEND_UNAVAILABLE`。
> 切り分け時は HTTP ステータスと `error.code` の両方を見ること。

---

### Step 1.4 — LLM のみ疎通確認（音声なし）

🎯 **確認できること:** 実 LLM サーバーが応答テキストを返すこと
✅ **前提:** 1.3 で `llm: configured` ＋ LLM サーバー稼働

```powershell
$body = @{
  device_id="test"; session_id="s1"; mode="web"
  input_text="今日の天気を一言で"; response_format="text"
} | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/api/chat -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 5
```

**期待結果:** `llm_text` に応答が入る。`audio` は `null` でよい。

> 💡 **モデル選定の注意（503 / 空応答の典型原因）**
> ホストは `max_tokens=256` 固定で要求し、応答の `message.content` のみを読みます。
> **推論（thinking）系モデルは思考トークンで 256 を使い切り `content` が空** になることがあり、
> その場合ホストは「空応答」とみなして 503 を返します（症状: `finish_reason=length` かつ本文空）。
> → 本文をそのまま返すモデルを `AI_VOICE_ATOM_LLM_MODEL` に指定してください。
>
> 単体での切り分け（モデル名を差し替えて本文が返るか確認）:
> ```powershell
> $b = @{ model="<モデル名>"; messages=@(@{role="user";content="一言で挨拶して"}); max_tokens=256 } | ConvertTo-Json -Depth 5
> (Invoke-RestMethod http://localhost:11434/v1/chat/completions -Method Post -ContentType "application/json" -Body $b).choices[0].message.content
> ```

---

### Step 1.5 — TTS 疎通確認（音声生成）

🎯 **確認できること:** テキストから WAV を生成して再生できること
✅ **前提:** `third_party/irodori-tts` 導入済み（`uv sync --extra cpu` 実施済み）

```powershell
$body = @{
  device_id="test"; session_id="s1"; text="接続を確認しました"; voice="default"
  audio=@{ format="wav"; sample_rate=16000 }
} | ConvertTo-Json
$r = Invoke-RestMethod http://localhost:8000/api/tts -Method Post -ContentType "application/json" -Body $body
$wav = [Convert]::FromBase64String($r.audio.data)
[IO.File]::WriteAllBytes("$env:TEMP\tts_out.wav", $wav)
"sample_rate=$($r.audio.sample_rate) channels=$($r.audio.channels) bytes=$($wav.Length)"
(New-Object Media.SoundPlayer "$env:TEMP\tts_out.wav").PlaySync()   # 再生
```

**期待結果:** 「接続を確認しました」という音声が再生される。

💻 他OS（再生）: `aplay /tmp/tts_out.wav` または `ffplay -nodisp -autoexit /tmp/tts_out.wav`

---

### Step 1.6 — LLM + TTS 連携確認（チャット + 音声）

🎯 **確認できること:** LLM 応答を TTS で音声化し、実機と同じ形式（16kHz/mono/16-bit の WAV）で返せること
✅ **前提:** 1.4 と 1.5 が成功

実機が使う `POST /api/chat/audio` を直接叩きます（本文はヘッダ `X-LLM-Text-B64`、ボディは WAV そのもの）。

```powershell
$body = @{ device_id="test"; session_id="s2"; mode="web"; input_text="こんにちは。一言で挨拶してください。" } | ConvertTo-Json
Invoke-WebRequest http://localhost:8000/api/chat/audio -Method Post `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
  -OutFile "$env:TEMP\chat_out.wav"
(New-Object Media.SoundPlayer "$env:TEMP\chat_out.wav").PlaySync()
```

💻 他OS:
```bash
curl -s -X POST http://localhost:8000/api/chat/audio -H 'Content-Type: application/json' \
  -d '{"device_id":"test","session_id":"s2","mode":"web","input_text":"こんにちは。一言で挨拶してください。"}' \
  -o /tmp/chat_out.wav && aplay /tmp/chat_out.wav   # macOS は afplay
```

**期待結果:** 挨拶の音声が再生される。
これが通れば **レイヤー3（実機フルE2E）に必要なホスト側はすべて整った** ことになる。

> JSON 版の `POST /api/chat` で音声まで返させたい場合は `AI_VOICE_ATOM_INCLUDE_AUDIO_INLINE=true`（ホスト再起動）
> が必要です（`audio.data` に base64 の WAV が入る）。実機はこの設定を使いません。

---

### Step 1.7 — WebSocket STT 確認

🎯 **確認できること:** 音声チャンクを送って `stt.final` が返る（音声→テキスト経路）
✅ **前提:** `third_party/moonshine` 取得済み ＋ `scripts/fetch_moonshine_native.py` でネイティブライブラリ配置済み

まずテスト用 WAV を生成:

```powershell
Set-Location <リポジトリ>\host
uv run python tests/gen_test_wav.py   # tests/fixtures/ に WAV 群が生成される
```

下記を `host/tests/manual_ws_check.py` として保存し実行（パスは host ディレクトリ基準）:

```python
import asyncio, base64, json, wave
import websockets

async def main():
    with wave.open("tests/fixtures/silence_1s.wav", "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    async with websockets.connect("ws://localhost:8000/ws/audio") as ws:
        await ws.send(json.dumps({"type": "session.start", "session_id": "ws-test", "device_id": "cli"}))
        print("ack:", await ws.recv())
        for i in range(0, len(pcm), 2048):
            await ws.send(json.dumps({"type": "audio.chunk", "session_id": "ws-test",
                                      "data": base64.b64encode(pcm[i:i+2048]).decode()}))
            await ws.recv()  # event.ack を消費
        await ws.send(json.dumps({"type": "audio.end", "session_id": "ws-test"}))
        result = json.loads(await ws.recv())
        print("stt.final:", result)
        assert result["type"] == "stt.final" and result["is_final"] is True

asyncio.run(main())
```

```powershell
Set-Location <リポジトリ>\host
uv run python tests/manual_ws_check.py
```

**期待結果:** `stt.final` が届く（無音 WAV なので `text` は空文字/空白）。
実音声で試す場合はファイルパスを差し替える。

---

### Step 1.8 — 再生停止 API 確認

🎯 **確認できること:** 停止 API が受理を返す（実機の再生停止＝再生中クリックが呼ぶ経路）
✅ **前提:** 1.1

```powershell
$body = @{ device_id="test"; session_id="s1" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/api/playback/stop -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json
```

**期待結果:** `{"status": "accepted"}`

---

### Step 1.9 — フロントエンド確認（ブラウザ）

🎯 **確認できること:** ブラウザ UI から LLM→TTS の一連（2 体の会話）が操作できること
✅ **前提:** 1.4 と 1.5 が成功。`host/device_profiles.json` にペルソナが 2 つ以上あると分かりやすい

ブラウザで開く: `http://localhost:8000/frontend/`

| 項目 | 期待動作 |
|---|---|
| 右上の状態チップ（LLM/TTS/STT） | 5 秒ごとに更新され「稼働」/「未設定」を表示 |
| ペルソナ A / B の選択肢 | `GET /api/devices` のペルソナが並ぶ |
| 実行モード「画面でデモ」→「デモを再生する」 | 2 体が交互に返事を生成し、各ターンの音声がブラウザで再生され、会話ログに吹き出しが並ぶ |
| 停止ボタン | 再生中に押すと止まる |

実機を使う実行モード（「実機で会話」「実機に話しかける」）はレイヤー3c で確認する。

---

## レイヤー2 — 実機 + スタブサーバー（Phase A）

🎯 **確認できること:** 実機の **WAV 再生経路全体**
（Wi-Fi 接続 → `/api/health` → ボタン → `/api/chat` → base64 デコード → I2S 再生）
✅ **前提:** AtomS3-Lite + Atomic Echo Base。**LLM / TTS は不要。**

> Phase A は LLM/TTS が無くても、同梱のテスト音声（ドレミの音階）で再生確認ができます。
> 「実機がホストに届いて音が出る」ところまでをここで切り分け、LLM/TTS はレイヤー3で足します。

### A-1. テスト用 WAV を準備

```powershell
Set-Location <リポジトリ>\host
uv run python tests/gen_test_wav.py   # tests/fixtures/test_melody_16k.wav（ドレミの音階、16kHz/mono）ほかを生成
```

テスト音声はリポジトリに同梱しているので、通常はこの手順を飛ばせます。自分の WAV を鳴らしたいときは
`tests/resample_wav.py <入力.wav> <出力.wav>` で 16kHz / mono に変換し、スタブサーバーの引数に渡します。

### A-2. スタブサーバーを起動

```powershell
Set-Location <リポジトリ>\host
uv run python tests/firmware_test_server.py
```

起動すると、設定すべき `HOST_BASE_URL` と提供エンドポイントが表示される。

> **このPCのIP** の確認（Windows）:
> ```powershell
> Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -ne '127.0.0.1' | Select-Object IPAddress, InterfaceAlias
> ```
> 💻 他OS: `hostname -I` または `ip addr show | grep "inet "`

### A-3. firmware/atom/.env を設定してアップロード

`firmware/atom/.env`（`.env.template` からコピー）の `HOST_BASE_URL` を、A-2 で表示された PC の IP にします。

```env
WIFI_SSID=your-ssid
WIFI_PASSWORD=your-password
HOST_BASE_URL=http://192.168.x.x:8000
```

VS Code → PlatformIO → env:m5stack-atoms3 → **Upload**（ビルド時に `load_env.py` が `.env` をマクロとして注入する）

### A-4. 動作確認（Echo Base 接続済みで）

| 操作 | 期待する LED | Serial ログ（115200 baud） |
|---|---|---|
| 書き込み直後（Wi-Fi 未接続） | オレンジ点灯 | `[wifi] connecting to ...` |
| Wi-Fi 接続 + スタブ OK | **青点灯** | `[health] ... -> 200` |
| ボタン A **クリック** | オレンジ → マゼンタ（再生中）→ 青 | 下記ログ参照 |
| 再生中に **クリック** | 即座に青へ戻る（再生停止） | `[btn] click — stopping playback` / `[playback/stop] -> 200` |

> web モードでの **長押し (1.2s)** は voice モードへの切替に割り当てられている（レイヤー3b 参照）。

**ボタン クリック時の Serial ログ（正常）:**

```
[chat] sending: こんにちは
[chat] status: 200
[chat] llm_text(url-encoded): %E3%81%93%E3%82%8C...
[wav] sr=16000 ch=1 bits=16 dataSize=128000
```

テスト音声（ドレミファソラシド）が Echo Base から聞こえれば、
**WAV ストリーム受信・ヘッダ解析・I2S 再生のパイプラインが正常**。
⛔ ここでは LLM/TTS は検証していません（次のレイヤー3で確認）。

---

## レイヤー3 — 実機 + 本番ホスト（フルE2E）

🎯 **確認できること:** ボタン → LLM → TTS → 実機発話 までの全経路
✅ **前提:** **L1 を全部通過済み**（特に 1.4 LLM・1.5 TTS）＋ Step 1.1 の本番ホストが起動中（`host/.env` は自動ロード）

実機は `POST /api/chat/audio` を叩き、返ってくる WAV（16kHz/mono/16-bit）をチャンク受信しながら再生します
（`AI_VOICE_ATOM_INCLUDE_AUDIO_INLINE` は JSON 版 `/api/chat` 用の設定で、実機には関係しません）。
ホスト側で次の 2 つが満たされていれば実機は喋ります:

1. `/api/status` の `llm`・`tts` が `configured`（Step 1.3）
2. `AI_VOICE_ATOM_LLM_MODEL` が **本文を返すモデル**（推論系で空応答にならない／Step 1.4 で確認）

`firmware/atom/.env` の `HOST_BASE_URL` を本番ホストの IP にしてアップロードします（`http://<ホストIP>:8000`）。
初回は [setup-guide 3-9](setup-guide.md#3-9-ホスト-pc-の-ip-アドレスを調べる) で IP を調べてください。

| 操作 | 期待結果 |
|---|---|
| 起動 | LED がオレンジ（Wi-Fi 接続中）→ 青（web・ホスト正常）。音声案内（または低音 1 回）が鳴る |
| ボタン A **クリック** | 「こんにちは」への返事を TTS で読み上げる（再生中は LED マゼンタ） |
| 再生中に **クリック** | 再生が即座に停止する |

**Serial ログ（正常）:**

```
[btn] click — sending chat request
[chat] sending: こんにちは
[chat] status: 200
[chat] llm_text(url-encoded): %E3%81%93%E3%82%93...
[wav] sr=16000 ch=1 bits=16 dataSize=XXXXXX
[timing] stream: hdr+first_audio=XX ms, playback_total=XXXX ms
```

> `[chat] status: 503` が出る場合は上の 1〜2 のいずれか未達（`[chat] error body:` に理由が出る）。
> LED が赤のままなら、ホストに届いていない（IP・ファイアウォール・ホスト未起動）。トラブルシューティング表を参照。

---

## レイヤー3b — voice モード（音声対話）

🎯 **確認できること:** マイク録音 → WebSocket STT → LLM → TTS → 実機発話の全経路
✅ **前提:** レイヤー3 が通っていること ＋ Moonshine のネイティブライブラリ配置済み（`scripts/fetch_moonshine_native.py`、`/api/status` の `stt: configured`）
＋ 本番ホスト起動中。Echo Base のマイクを使う。

> web モードが HTTP チャット（ボタン クリック＝固定文「こんにちは」送信）なのに対し、
> voice モードは **クリックで録音開始 → もう一度クリックで停止**し、文字起こし結果でチャットする。

### ボタン操作（共通）

| ジェスチャ | web モード | voice モード | relay モード | recorder モード |
|---|---|---|---|---|
| **クリック**（短押し） | チャット送信 / 再生中は停止 | 録音 開始 ⇄ 停止（トグル） | 再生中なら停止（進行はホスト主導） | 録音 開始 ⇄ 停止 / 再生中は停止 |
| **ダブルクリック** | － | － | － | 最後の録音を再生 |
| **長押し (1.2s)** | 次モードへ切替 | 次モードへ切替 | 次モードへ切替 | 次モードへ切替 |

> 長押し＝モード切替は全モード共通で、`web → voice → relay → recorder → parrot → timer → web` の順に巡回する。切替時に
> 音声案内（`mode_prompts.h` 未生成ならチャイム）が鳴る。選んだモードは保存され、次回起動時に復元される。エラー時は低音3回。

### モードと LED の対応

| モード | 待機 LED | 切替操作 |
|---|---|---|
| web（初回起動時の既定） | **青** | 長押し (1.2s) で voice へ |
| voice | **緑** | 長押し (1.2s) で relay へ |
| relay | **シアン** | 長押し (1.2s) で recorder へ |
| recorder | **白** | 長押し (1.2s) で parrot へ |
| parrot | **黄** | 長押し (1.2s) で timer へ |
| timer | **紫** | 長押し (1.2s) で web へ |

### 操作と期待

| 操作 | 期待する LED | 期待結果 |
|---|---|---|
| 長押し (1.2s)（web→voice） | 青 → 緑 | 「音声会話モードですわ。」（またはチャイム高音2回）。`[btn] hold — toggling mode` |
| voice で **クリック**（録音開始） | 緑の点滅（録音中） | `[btn] click — start recording` → `[voice] capture start` |
| 録音中に発話 | 緑点滅のまま | `audio.chunk` を逐次送信 |
| もう一度 **クリック**（録音停止） | オレンジ（STT/応答待ち）→ マゼンタ（再生）→ 緑 | `[btn] click — stop recording` → `[ws] stt.final` → 応答を発話 |

**Serial ログ（正常・voice）:**

```
[btn] hold — toggling mode
[mode] switched to voice
[btn] click — start recording
[ws] connecting to ws://192.168.x.x:8000/ws/audio
[ws] connected /ws/audio
[voice] capture start (session=atom-12345)
[btn] click — stop recording
[voice] audio.end sent — waiting stt.final
[ws] stt.final: 今日の天気は
[chat] sending: 今日の天気は
[chat] status: 200
```

> - voice モードは **クリックで録音開始 → もう一度クリックで停止・送信**（押しっぱなしではない）。録音中は LED が緑に点滅する。
> - `[voice] ws not connected — abort`（低音3回）が出る場合は host 未起動／IP 誤り／WiFi 瞬断。
>   ファームウェアは ping/pong で半開を検知し自動再接続するので、ホスト復帰後にもう一度クリックすれば繋がる。
> - `[voice] empty transcript — skip chat` は無音または認識ゼロ。マイク位置・発話音量を確認。

⛔ ここまでで voice/web 両モードのフル E2E が確認できる。

---

## レイヤー3c — relay モード（リレー会話・実機への話しかけ）

🎯 **確認できること:** 2 体のペルソナをホスト統括で交互に会話させ、実機で再生する全経路（実機 2 台、または 1 台で 2 体）。
Web から実機のキャラクターへ話しかける経路。
✅ **前提:** レイヤー3 通過済み ＋ 本番ホスト起動中 ＋ AtomS3 が 1 台以上（2 台なら別 `DEVICE_ID`）。
空気越し STT は介さない（ホストがテキストでターンを受け渡す）ので moonshine は不要。

### 準備

1. 2 台使う場合は、各機体のファームに**別々の `DEVICE_ID`** を設定して書き込む（`.env` の `DEVICE_ID`、例 `atoms3-001` / `atoms3-002`）。
2. ホストに `host/device_profiles.json` を置き、各 `device_id` にペルソナと声色を割り当てる（項目は [host/README.md](../host/README.md#ペルソナ定義device_profilesjson)）。
   実機 1 台でも、ペルソナは 2 つ以上定義しておく（1 台が両方の声を出す）。
3. 実機を **relay モード**にする（長押しで巡回、待機 LED が**シアン**）。relay では `/ws/audio` へ接続し `register` を送る。

### 接続確認

```bash
# 両デバイスが WS 登録できているか
curl -s http://<ホストIP>:8000/api/conversation/status | jq .connected_devices
# => ["atoms3-001","atoms3-002"]

# 登録済みペルソナ一覧
curl -s http://<ホストIP>:8000/api/devices | jq .
```

### 会話を開始

```bash
curl -s -X POST http://<ホストIP>:8000/api/conversation/start \
  -H 'Content-Type: application/json' \
  -d '{"device_a":"atoms3-001","device_b":"atoms3-002","opening_text":"今日はいい天気だね。","max_turns":6}'
```

**実機 1 台で 2 体を話させる場合**は、出力先に同じ実機を指定する:

```bash
curl -s -X POST http://<ホストIP>:8000/api/conversation/start \
  -H 'Content-Type: application/json' \
  -d '{"device_a":"atoms3-001","device_b":"atoms3-002","max_turns":4,"output_device_a":"atoms3-001","output_device_b":"atoms3-001"}'
```

フロント（`/frontend/` の「実機で会話」）からも開始できる。「スピーカー（実機）」で「各ペルソナの実機（2台）」か
「1台で両方: 実機 atoms3-001」を選ぶ。

| 操作 | 期待結果 |
|---|---|
| `/api/conversation/start` | device_a → device_b の順に、ホストが生成した台詞を出力先の実機がそのペルソナの声色で発話して交互に会話する |
| 各ターン再生中 | 話者機が**マゼンタ**（再生中）、ターン完了で**シアン**（relay 待機）に戻る |
| `/api/conversation/stop` | 進行中の会話が止まる |

**Serial ログ（正常・relay 話者側）:**

```
[ws] play(turn=0, persona=atoms3-001): 今日はいい天気だね。
[relay] say(turn=0): 今日はいい天気だね。
[relay] /api/chat/say -> 200
[wav] sr=16000 ch=1 bits=16 dataSize=XXXXXX
```

### 実機に話しかける

フロントの「実機に話しかける」で、話し相手（ペルソナ）とスピーカー（実機）を選び、文章を入力して「話しかける」。
API で試す場合:

```bash
curl -s -X POST http://<ホストIP>:8000/api/conversation/chat \
  -H 'Content-Type: application/json' \
  -d '{"persona_id":"atoms3-002","text":"今日はどうだった？","output_device":"atoms3-001","session_id":"test"}'
```

| 期待結果 |
|---|
| 実機 atoms3-001 が atoms3-002 のペルソナの声色で返事を話し、再生が終わってから `{"text": "..."}` が返る |
| 同じ `session_id` で続けて話しかけると、前の発言を踏まえて答える |

> - `connected_devices` に片方しか出ない → その機が relay モードか／WS 接続できているか（IP・WiFi）を確認。
> - `start`・`chat` が **409** → 出力先の実機が未接続。**400** → 既に会話・チャットの実行中（`/stop` してから）か入力不正。
> - `chat` が **503** → LLM の失敗（Ollama 未起動・初回のモデルロードでタイムアウト等）か、実機の再生完了 ack がタイムアウト。
> - 後半ターンで止まる場合は ack タイムアウト。`AI_VOICE_ATOM_RELAY_ACK_TIMEOUT`（既定 180s）が
>   デバイス側 `/api/chat/say` のタイムアウトより長いか確認（GPU 競合で TTS が遅延しうる）。

⛔ ここまでで relay モード（リレー会話・実機への話しかけ）の E2E が確認できる。

---

## レイヤー3d — recorder モード（単体録音再生）と音声案内

🎯 **確認できること:** ホスト・Wi-Fi なしでの録音・保存・再生、モード切替の音声案内
✅ **前提:** 実機に書き込み済み。音声案内を確かめる場合は `host/scripts/generate_mode_prompts.py` で
`firmware/atom/include/mode_prompts.h` を生成してから書き込む（[setup-guide 4-2](setup-guide.md#4-2-任意モード切替の音声案内を作る)）。

| 操作 | 期待結果 |
|---|---|
| 長押しで recorder へ | 「録音再生モードですわ。」（またはチャイム下降2音）、LED **白** |
| 録音が無い状態で**ダブルクリック** | 「まだ録音がありませんわ。」（案内未生成ならエラー音） |
| **クリック** → 話す → **クリック** | 開始時「ピッ」＋ LED 赤点滅、停止時に低い「ピッ」。`[rec] saved ... bytes (X.X s)` |
| **ダブルクリック** | 録音を再生（LED マゼンタ）。再生中のクリックで停止 |
| ゆっくりめの**ダブルクリック**（間が 0.5 秒超） | 「ピッ」と鳴って録音が始まりかけても、前の録音は消えずに再生される（`[rec] pressed during start beep` か `[rec] too short ... discarded`） |
| 録音中に短く**クリック** | 1 回で止まる（取りこぼさない） |
| 30 秒録音し続ける | 自動で停止・保存（`[rec] limit reached — auto stop`） |
| 電源を切って入れ直す | recorder モードで起動し、ダブルクリックで前の録音が再生される |
| Wi-Fi 圏外・ホスト停止中 | 上記がすべて同じように動く（LED は白のまま） |

> `[boot] LittleFS: ng` の場合は録音できない。`pio run --target erase` で消去してから書き込み直す（録音とモード記憶も消える）。

---

## レイヤー3e — parrot（オウム返し）と timer（タイマー）

🎯 **確認できること:** ホスト・Wi-Fi なしでのオウム返しとタイマー
✅ **前提:** 実機に書き込み済み。案内音声を確かめる場合は最新の `mode_prompts.h`（版数 2 以上）を生成して書き込む
（古いヘッダのままだとビルド時に警告が出て、案内はチャイムで代用される）。

### parrot

| 操作 | 期待結果 |
|---|---|
| 長押しで parrot へ | 「オウム返しモードですわ。」、LED **黄** |
| 黙って数秒待つ | 反応しない。シリアルに `[parrot] rms=… noise=… threshold=…` が 1 秒ごとに出る |
| 「こんにちは」と話して黙る | 話し始めで LED が点滅（`[parrot] voice start`）→ 黙って約 0.7 秒後に高い声で言い返す（LED マゼンタ）→ 黄に戻る |
| 机を軽くたたく | 0.3 秒未満の音は無視（`[parrot] too short`） |
| **クリック** | 「ふつうの声にしましたわ。」→ 次の言い返しはふつうの声。もう一度で「低い声」、さらに「高い声」 |
| 電源を入れ直す | parrot モードで起動し、選んだ声の種類が保たれている |

> 静かな部屋で勝手に言い返すなら `firmware/atom/.env` の `PARROT_MIN_RMS` を上げ、話しても反応しないなら
> 下げて書き込み直す。シリアルの `rms`（話したとき）と `threshold` の値を見比べて決める。

### timer

| 操作 | 期待結果 |
|---|---|
| 長押しで timer へ | 「タイマーモードですわ。」、LED **紫** |
| **ダブルクリック** | 「5分にしましたわ。」（既定 3 分の次）。繰り返すと 10 → 25 → 1 → 3 分 |
| 1 分にして **クリック** | 約 0.4 秒後に「スタートですわ。」。LED が緑で 1 秒ごとに短く消える → 黄 → 赤 |
| 1 分待つ | 「時間ですわ！」が 4 秒ごとに鳴り、LED が赤白に点滅。**クリック**で止まる（止めなくても約 30 秒で止まる） |
| 3 分でスタート | 残り 1 分で「残り1分ですわ。」 |
| 動作中に **クリック** | 「タイマーを止めましたわ。」、LED 紫に戻る |
| 動作中に長押しで別モードへ | タイマーは中止される |

---

## トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| テストが全グリーンにならない | `uv sync --extra dev` 未実施 |
| まず何を疑えばよいか分からない | `cd host && uv run python scripts/check_setup.py` で `[NG]` を確認 |
| `/api/status` で `llm: not-configured` | `host/.env` が無い／`LLM_BASE_URL`・`LLM_API_KEY` 未設定。別の環境変数で上書きされていないかも確認（既存env優先） |
| `/api/chat` が 503 を返す | ①LLM 未構成（上記） ②LLM サーバー未起動 ③**モデルが空応答**（推論系で `finish_reason=length` かつ本文空）→ 本文を返すモデルへ変更 |
| チャット成功するが `audio: null` | `AI_VOICE_ATOM_INCLUDE_AUDIO_INLINE=true` にして**ホスト再起動** |
| TTS が `RuntimeError` を返す | `cd third_party/irodori-tts && uv sync --extra cu128`（GPU なしは `--extra cpu`）未実施。初回はモデル DL でタイムアウトしやすいので [setup-guide 3-3](setup-guide.md#3-3-音声合成irodori-ttsの環境を作る) の試し合成を先に |
| STT が import エラー | `uv sync --extra dev` で moonshine-voice 未インストール |
| STT が `Failed to load Moonshine library` | ネイティブライブラリ未配置。`uv run python scripts/fetch_moonshine_native.py` の後、**ホストを再起動**（失敗状態がキャッシュされるため） |
| スタブ/本番に実機が繋がらない | PC の IP 確認、ファイアウォールでポート 8000 を開放 |
| 実機 LED が赤のまま | バックエンド不健全。`HOST_BASE_URL` の IP 誤り、またはホスト未起動 |
| voice モードでクリックしても録音されない | LED が緑＝voice モードか確認（web は青／長押し 1.2s で切替）。`stt: configured` か `/api/status` で確認 |
| モードが切り替わらない | 長押しは **1.2s 以上**保持する（途中で離すとクリック扱い）。`[btn] hold — toggling mode` がログに出るか確認 |
| `[voice] ws not connected — abort`（低音3回） | host 未起動／IP 誤り／WiFi 瞬断。ホスト復帰後にもう一度クリック（自動再接続される） |
| voice で `[voice] empty transcript` | 無音／認識ゼロ。マイク位置・発話音量、`third_party/moonshine` 導入を確認 |
| 実機で音が出ない（status 200 なのに） | Serial の `[wav]` ログ有無を確認。`[wav] unexpected format` なら 16kHz/mono/16-bit 以外の WAV |
| `[wav] invalid WAV header` | スタブのテスト音声（`test_melody_16k.wav`）が無い、または壊れたファイル指定 |
| `[chat] OOM` | WAV が大きすぎる。`resample_wav.py` で 16kHz 変換済みか確認 |
| relay で `connected_devices` が揃わない | その機が relay モード（待機シアン）か／WS 接続できているか（IP・WiFi）。`register` がログに出るか確認 |
| `/api/conversation/start`・`/chat` が 409 | 出力先の実機が未接続（relay モードか・WS 接続できているか） |
| `/api/conversation/start`・`/chat` が 400 | 既に会話・チャットを実行中（`/stop` してから）、`device_a`/`device_b` が同一、`max_turns<=0`、空テキスト |
| relay 会話が後半ターンで止まる | ack タイムアウト。`AI_VOICE_ATOM_RELAY_ACK_TIMEOUT`（既定 180s）をデバイス側 `/api/chat/say` のタイムアウトより長く |
| relay で全機が同じ声・同じ性格 | `host/device_profiles.json` 未配置／`device_id` 不一致。`ref_wav` は実在ファイルのみ採用される |
