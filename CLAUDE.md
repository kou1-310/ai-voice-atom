# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **これは生きたドキュメント。** 同じ誤りを2回踏んだら規約へ還元する。記憶はヒントであって事実ではない
> — ファイル・関数・フラグ名は行動前に実コードで検証すること。

## AI Native 開発ルール

機能追加の進め方・品質ゲート・Spec の書き方は **`docs/ai-native-rules.md`**（本リポジトリ適応版の憲法）に従う。
要点: ①Spec 駆動（`docs/templates/spec-template.md`・`/spec`）→ ②検証ファースト（`/build-test-verify` が全緑で初めて Done）
→ ③並行作業は worktree で所有権を分離。逸脱・指摘は `docs/templates/lessons-learned.md` に絶対日付で還元。
一次レビューは `/self-review`。

## 概要

`ai-voice-atom` は M5Stack **AtomS3-Lite + Atomic Echo Base** デバイスを LLM/TTS/STT バックエンドに
接続するプロジェクト。構成は3層:

- **`firmware/atom/`** — ESP32-S3 向け C++ ファームウェア（PlatformIO）。ボタン押下 → HTTP チャット
  リクエスト → WAV 応答をストリーミング受信し I2S スピーカーで再生する。
- **`host/`** — FastAPI サービス（Python、`uv` で管理）。LLM → TTS → STT を統括する。
- **`third_party/`** — host が利用する git submodule: `irodori-tts`（日本語 TTS）と `moonshine`（STT）。
  どちらも初期化が必須で、submodule が無いと host の TTS/STT は動作しない。

`docs/` の入口は `docs/README.md`（全文書の案内）。意図された仕様は `specification.md`、`backend-api.md`、
`use-cases.md`、`verification-guide.md` にあり、機能ごとの Spec は `docs/specs/`、実装計画と経緯の記録は
`docs/plans/` に置く。ドキュメントとコードが食い違う場合はコードを正とする。
利用者向けの入口は `README.md` → `docs/setup-guide.md`（初回セットアップ）→ `docs/manual.md`（操作マニュアル）、
読み手向けのしくみの解説は `docs/architecture.md`、ほかに `host/README.md`、`firmware/atom/README.md`。
操作方法（ボタン・モード・LED）を変えたら `docs/manual.md`・`README.md` のモード表・`firmware/atom/README.md` を揃える。

## セットアップとコマンド

Windows リポジトリ（パスはバックスラッシュ）だが、以下のコマンドはクロスプラットフォームで通用する。

```bash
git submodule update --init --recursive          # 必須 — host は submodule に依存する

# Host (FastAPI)
cd host
cp .env.example .env                              # LLM の値を記入する（API_KEY は Ollama でも空にしない）
uv sync --extra dev
uv run python scripts/check_setup.py              # 準備状況の点検（NG なら直し方を表示）
uv run uvicorn app.main:app --reload --host 0.0.0.0

# テスト（host/ から実行）
uv run pytest                                     # 全件
uv run pytest tests/test_chat.py                  # ファイル単位
uv run pytest tests/test_chat.py::test_name       # テスト単位

# Irodori-TTS submodule の環境（独立した uv プロジェクト）
cd third_party/irodori-tts && uv sync --extra cpu # GPU: --extra cu128 / rocm / xpu

# Moonshine のネイティブライブラリ（host/ から実行。submodule に同梱されないため必須）
uv run python scripts/fetch_moonshine_native.py   # モデル本体は初回の文字起こし時に .cache/moonshine_voice へ自動取得
```

初めての人向けの手順書は `docs/setup-guide.md`（ユーザー向け。手順を変えたらここも更新する）。
Moonshine は submodule を editable で使うため、ネイティブライブラリ（Windows: `moonshine.dll`/`onnxruntime.dll`、
Linux: `libmoonshine.so` と `python/src/moonshine_voice.libs/`）が無いと `Failed to load Moonshine library` で落ち、
失敗が singleton にキャッシュされるので配置後はホストの再起動が必要。

**ファームウェア**（PlatformIO、env `m5stack-atoms3`）: `firmware/atom/.env.template` を `.env` に
コピーして（`WIFI_SSID`、`WIFI_PASSWORD`、`HOST_BASE_URL`）からビルド/書き込みする。`load_env.py` が
`.env` の値をビルドフラグのマクロとして注入する。書き込み前にシリアルモニターを閉じること。詳細は
`firmware/atom/README.md` と `UPLOAD_GUIDE.md`（WSL2）を参照。`HOST_BASE_URL` は mDNS
（`http://atoms3-host.local:8000`）も使用可能で、ファームウェア側が `.local` ホスト名を自前で解決する。

## Host アーキテクチャ

リクエストの層: `api/`（FastAPI ルーター） → `services/`（統括処理） → `adapters/`（外部システム）。
Pydantic モデルは `models/schemas.py`、設定は `config.py`。

- **アダプターが3つの外部システムをラップする。** `IrodoriTtsAdapter` は submodule ディレクトリで
  `uv run --no-sync python infer.py` をサブプロセス実行する（低速、タイムアウトあり）。
  `MoonshineAdapter` は `moonshine-voice` をライブラリとして使う（transcriber は遅延ロードしモデルを
  キャッシュ）。`OpenAIClient` は OpenAI 互換のチャットエンドポイント（Ollama 等）を呼ぶ。
- **ルーターはモジュール import 時にサービス/アダプターを生成する**（例: `chat.py` はモジュールレベルで
  `ChatService` を生成）。`get_settings()` は `@lru_cache` 付き。結果として設定/アダプターは import 時に
  固定された実質シングルトンになる。テストは環境変数ではなくサービスのコンストラクタ経由でフェイクを
  注入する（`tests/test_chat.py` の `_FakeOpenAIClient` / `_FakeIrodoriAdapter` を参照）。

### 主要なフロー

- **`POST /api/chat/audio`** はデバイスの主経路で、JSON 出力では*ない*。生の `audio/wav` ボディ
  （16 kHz / mono / 16-bit）を返し、LLM テキストはヘッダ `X-LLM-Text`（URL エンコード）と
  `X-LLM-Text-B64`（base64 UTF-8）に入れる。これによりファームウェアは応答全体を RAM に保持せず
  チャンク再生できる。`POST /api/chat` は JSON 版（音声インラインは
  `AI_VOICE_ATOM_INCLUDE_AUDIO_INLINE=true` のときのみ）。
- **`WS /ws/audio`**（`AudioSessionService`）は JSON の STT プロトコル: `session.start` →
  `audio.chunk`（base64 PCM、セッション単位で蓄積） → `audio.end`（文字起こしし `stt.final` を返す）。
  16 kHz PCM を前提とする。
- `GET /api/status` は各バックエンドが "configured" かを報告する（LLM = base_url + api_key、
  TTS/STT = submodule のパスが存在するか）。
- `frontend/`（静的な `index.html`/`app.js`/`style.css`）は `/frontend` にマウントされる。

### 音声の取り決め

デバイスのパイプラインは **16 kHz / mono / 16-bit PCM WAV**（正規の 44 バイトヘッダ）で統一する。
`utils/wav.py::to_pcm16_mono` が TTS 出力をこの形式へリサンプル/ダウンミックスし、デバイスのメモリ
制約に収める。ファームウェアの WAV パーサは 16-bit mono 以外を拒否する。

**話者リファレンス音声**: `host/voices/reference.wav`（`AI_VOICE_ATOM_TTS_REF_WAV` で変更可）に
wav を置くと、その声色で合成する（無ければ既定の声）。合成のたびに存在確認するので差し替えに再起動は
不要。常駐サイドカー経路でもこのリファレンスを渡して合成する（`infer.py` の `--ref-wav` 相当）。

### モード案内と単体動作（recorder）

- **音声案内**: `host/scripts/generate_mode_prompts.py` が参照音声（既定 `host/voices/shikoku_metan-normal.wav`）の
  声色で案内文を合成し、`firmware/atom/include/mode_prompts.h`（Git 管理外）へ C 配列として書き出す。
  ファームは `__has_include` で任意に取り込み、無ければチャイムで代用する（未生成でもビルド可）。
  Irodori-TTS は文末の無音のあとに余計な音（「はぁ」等）を足すことがあるため、`trim_and_normalize` が
  発話後 300ms 以上の無音で打ち切る。配列名はファームと共有しているので変更時は両方を揃える。
- **recorder モード**: ホスト・Wi-Fi 不要。クリック=録音開始/停止、ダブルクリック（自前判定。1 回目を離してから
  0.5 秒以内に 2 回目を**押した**時点で確定。M5Unified 標準判定は長押し閾値 1.2s を待つため不採用）=再生。
  録音は `/rec.tmp` へ書き、0.5 秒以上録れてから `/rec.pcm` と入れ替える（遅いダブルクリックが「録音開始→停止」に
  なっても前の録音を消さない。0.5 秒未満の録音は捨てて前の録音を再生する）。録音は LittleFS の `/rec.pcm`
  （ヘッダ無し 16k/mono/16-bit、最大 30 秒、1 スロット）。このモード中は Wi-Fi 再接続・ヘルスチェック・WS を
  止める（ブロッキング HTTP で録音が途切れるため）。Spec: `docs/specs/mode-guidance-recorder-spec.md`。
- **parrot / timer モード**（Spec: `docs/specs/parrot-timer-spec.md`）: parrot は `beginMicCapture(false)` で常時聞き取り
  （`recording` を立てないので長押し切替が効く）、100ms ごとの RMS が「雑音 EMA × 3」と `PARROT_MIN_RMS`（`.env`・
  `load_env.py` の数値キー）の大きい方を超えたら直前 200ms ごと `/parrot.pcm` に録音し、0.7 秒の無音で再生
  サンプルレートを変えて言い返す（マイクとスピーカーは I2S 共有で同時不可なので、聞く→止めて再生→聞くを交互に）。
  timer は millis ベースで `timerTick()` が残り 1 分・時間切れを案内。単体モード判定は `isStandaloneMode()`、
  モードの出入りの後始末は `enterMode()`/`leaveMode()`。案内ヘッダは `MODE_PROMPTS_VERSION` が
  `REQUIRED_PROMPTS_VERSION` 未満なら使わない（案内を増やしたら両方を上げる）。生成スクリプトは
  `firmware/atom/prompts/` に文面キー付きで WAV を残し、同じ文面は再合成しない。
- **Bluetooth オーディオは不可**: AtomS3 の ESP32-S3 は Bluetooth Classic 非搭載で A2DP を使えない（BLE のみ）。

### マルチデバイス（複数 AI の会話）

複数の AtomS3 を**別個の AI**（別ペルソナ・別声色）として動かせる。会話のさせ方は2系統:
**リレー（ホスト統括・既定）** と **音響ループ（空気経由・実験）**。ファームの長押しでモードを
`web → voice(音響) → relay → recorder → parrot → timer` と巡回する（モードは NVS に保存され次回起動時に復元）。

- **デバイス識別**: ファームウェアの `DEVICE_ID`（ビルドフラグ、`.env` で設定、既定 `atoms3-001`）を
  機体ごとに変える。この `device_id` が `/api/chat/audio` と WS `session.start` に乗る。チャットの
  `session_id` も `sess-<DEVICE_ID>` に派生させている。
- **ペルソナ/声色の割り当て**: `host/device_profiles.json`（`AI_VOICE_ATOM_DEVICE_PROFILES` で変更可）に
  `device_id → プロファイル` を定義する。`device_profiles.example.json` が雛形。`ChatService` が
  `device_id` でこれを引き、system プロンプトと TTS の ref_wav を出し分ける
  （`device_profiles.py::load_device_profiles`）。未登録の `device_id` はグローバル設定
  （`llm_system_prompt` / `tts_ref_wav`）にフォールバックする。`ref_wav` は実在するファイルのときだけ採用。
  プロファイルの項目（すべて任意）:
  - `display_name` — フロント表示名。`ref_wav` — 声色リファレンス wav。
  - `voice_caption` — 参照音声を使わずに声色を指定する自然文（VoiceDesign チェックポイントへ
    `--caption` として渡る。例:「落ち着いた女性の声で、やわらかく読み上げて」）。`ref_wav` 無しでも
    性別・トーンを指定でき、`ref_wav` と併用すると「その声＋このスタイル」のクローンになる。
    指定時は常駐サイドカーを使わず毎回 `infer.py` を起動するため初回は低速（600M モデルのロード）。
  - `system_prompt` — 明示の system プロンプト（あれば最優先）。
  - **構造化ペルソナ**（`system_prompt` 省略時にこれらから system を合成。`config.COMMON_VOICE_STYLE`
    の読み上げ向け制約を自動付与）: `persona`（性格）/ `speaking_style`（口調）/ `first_person`（一人称）/
    `interests`（話題の配列）。
  - **ペルソナ別サンプリング**: `temperature` / `presence_penalty` / `frequency_penalty`。未指定はグローバル
    （`llm_*`）にフォールバック。AI ごとに生成の温度を変えて個性を出せる。
  - `opening_line` — リレー会話でこのペルソナが最初の話者のときに使う口火の一言。
- **履歴の分離**: `ChatService` の会話履歴は `device_id::session_id` の複合キーで保持する。ファームが
  固定 `session_id` を送っても device 単位で履歴が分離する。
- **Web からの自動会話（Phase A）**: `frontend/` のコンソールから2体のペルソナを選び、ブラウザが
  `/api/chat/audio` をターン交代で叩いて交互に会話させ、各ターンの TTS をブラウザで再生する
  （実機不要のシミュレーション）。選択肢は `GET /api/devices`（`chat_service.device_profiles` を返す）
  から生成。会話ループはクライアント主導でホストはステートレスのまま。詳細は
  `docs/plans/frontend-auto-conversation-plan.md` / `DESIGN.md`。実機での再生は下記のリレー（Phase B-1）で実装済み。
- **対話の自然さ**: 小型モデルは相手の発言を逐語コピーしやすい（エコー崩壊）。`llm_dialogue_guidance`
  を system プロンプト末尾に連結して「繰り返さず話を前に進める」よう促し、`llm_temperature` /
  `llm_presence_penalty` / `llm_frequency_penalty` で反復を抑える（`chat_service.py` / `openai_client.py`）。
  リレー会話ではさらに `ChatService.generate_reply(..., partner_id, directive)` で次を効かせる:
  **相手認識**（`partner_id` のペルソナ名・性格を system に注入し、名前で呼びかけさせる）、
  **会話フェーズ指示**（`ConversationOrchestrator._directive` が残りターン数から導入／締めの指示を動的生成）、
  **反復ガード**（自分の前回発言と違う切り口を促す）。`_resolve_system_prompt` がペルソナ本体＋相手認識＋
  ガイダンス＋ターン指示を空行連結する。オーケストレータは最初の話者の口火（`opening_text` か
  ペルソナ `opening_line`）を逐語再生する場合も `record_relay_opening` で話者の履歴に残し、記憶の非対称を防ぐ。
  改善の全体計画と進捗（実装済み/未着手）は `docs/plans/persona-conversation-improvements.md`。
- **実機リレー（Phase B-1・既定経路）**: ホストがターンを統括する。デバイスは `relay` モードで
  `/ws/audio` に接続し `{"type":"register","device_id":...}` を送る。`ConnectionManager`
  （`services/connection_manager.py`）が `device_id → WebSocket` を保持し、`ConversationOrchestrator`
  （`services/conversation_orchestrator.py`）が各ターンのテキストを `ChatService.generate_reply` で
  生成して話者デバイスへ `{"type":"play","text","turn"}` を push、デバイスは `/api/chat/say`
  （`{device_id,text}` → ペルソナ声色の 16k/mono WAV、LLM 不使用）で自分の声を取得・再生し、
  `{"type":"played","turn"}` を返す。ホストはこの ack を待って次のターンへ進む（上限は
  `AI_VOICE_ATOM_RELAY_ACK_TIMEOUT`、既定 180s。合成が GPU 競合で遅延しうるため、デバイス側
  `/api/chat/say` のタイムアウトより長く取る。デバイスは再生中も WS を回し、合成後の切断時は
  再接続してから played を返すので ack の取りこぼしに強い）。空気越し STT を
  介さないので会話内容が安定する。フロントから `POST /api/conversation/start`（device_a/device_b/
  opening_text/max_turns）で開始、`/stop`・`/status`（接続中デバイス含む）。`/ws/audio` は STT 経路
  （`session.start`/`audio.chunk`/`audio.end`）と push を兼ねる（後方互換: `session.start` でも登録する）。
- **ペルソナと出力実機の分離（実機1台でも可）**: `play` に `persona` を載せ、デバイスはそれを
  `/api/chat/say` の `persona_id` に渡してそのペルソナの声色で鳴らす。`/api/conversation/start` の
  `output_device_a/b`（省略時はペルソナと同 ID の実機）に同じ実機を指定すれば 1 台で 2 体の会話になる。
  `POST /api/conversation/chat`（`persona_id`/`text`/`output_device`）は Web から 1 往復話しかけ、
  実機の再生 ack まで待って応答テキストを返す（ack の turn は `CHAT_TURN_BASE`=10000 以上、リレーと排他）。
  フロントの実行モードは「画面でデモ／実機で会話（スピーカー選択で 2 台 or 1 台）／実機に話しかける」。
- **音響ループ（Phase B-2・未実装）**: `voice` モードは現状**手動 PTT**。自動聞き取り（VAD/連続録音）と
  ターン制御は未実装で、空気越し STT 精度・ハウリングに依存し不安定なため実験扱い。

## 設定

Host の設定はすべて `AI_VOICE_ATOM_` 接頭辞の環境変数（`config.py` / `.env.example` を参照）。
`config.py` は import 時に `host/.env` を自動ロードするが、**既存の変数は上書きしない**（`uvicorn
--env-file` や実環境変数が優先される）。注目点: `AI_VOICE_ATOM_LLM_REASONING_EFFORT=none` は
reasoning 系モデルの思考出力を抑止し `content` を確実に埋める。空にするとパラメータ自体を送らない。
`AI_VOICE_ATOM_LLM_TEMPERATURE` / `_PRESENCE_PENALTY` / `_FREQUENCY_PENALTY` は生成の反復抑制
（空文字でそのパラメータを送らずバックエンド既定に従う）、`AI_VOICE_ATOM_LLM_DIALOGUE_GUIDANCE` は
対話誘導文（空文字で連結しない）。submodule の場所は `AI_VOICE_ATOM_IRODORI_ROOT` /
`_MOONSHINE_ROOT` で上書き可能。

**リソース最適化**: LLM(Ollama) と TTS サイドカーを同一PCで動かすと会話中に重くなりやすい
（1枚のGPUを画面描画・LLM・TTSで奪い合う）。`AI_VOICE_ATOM_TTS_PRECISION`（既定 `fp32`／高精度、
`bf16` で GPU の VRAM・演算を約半減）、`AI_VOICE_ATOM_TTS_TORCH_THREADS`（サイドカーの torch CPU
スレッド上限、空で無制限）、`AI_VOICE_ATOM_TTS_DEVICE`（デバイス上書き）で調整する。これらは
`config.py` が `.env` を `os.environ` へ載せるため、`IrodoriTtsAdapter` が起動するサイドカー子プロセス
（`tts_server.py`）にも継承される。Ollama 側設定（`OLLAMA_KEEP_ALIVE` / 量子化モデル等）含め詳細は
`docs/performance-tuning.md`。

## 規約

- Python ≥ 3.11、型ヒント付き。外部の設定ライブラリは使わない（環境変数の解析は標準ライブラリのみで自前実装）。
- コメントやユーザー向けの文言・ドキュメントは日本語。編集時もこのスタイルを維持する。
- アダプターは「バックエンド利用不可/失敗」に `RuntimeError`、不正入力に `ValueError` を投げ、ルーターが
  それぞれ HTTP 503 / 400 にマッピングする。このマッピングを維持すること。
- ファームの `loop()` から呼ぶネットワーク処理は、ホストに届かないときの待ち時間を抑える（接続タイムアウトを
  短くし、失敗が続く間は間引く）。ブロック中は `M5.update()` が回らずクリック・長押しを取りこぼすため。
  ホスト不在（PC の電源断・USB 給電のみ）でも単体モードへ切り替えられることを保つ。
- M5Unified（0.2.x）の `M5.Mic.record()` と `M5.Speaker.playRaw(..., stop_current_sound=false)` は、キューが空くまで
  内部で待つ（最大 1 チャンクぶん）。待ちの間は `M5.update()` が回らないので、録音は `micChunkReady()` で空きを
  確かめてから積み、再生はチャンクごとにボタンを見る。「`playRaw` が false を返したら〜」のリトライループは回らない。
- ボタン操作の不具合は実機で再現して直す。GPIO41 をオープンドレイン出力にして別タスクから Low に引けば、
  クリック・ダブルクリック・長押しを任意の間隔で再現できる（手順はメモリ `firmware-button-diagnosis`）。
- `HTTPClient::setTimeout` の引数は `uint16_t`（最大 65535ms）。超える値は桁あふれして短くなる。
  ファームを変更したらビルドログの warning を確認する。
