# host — ai-voice-atom のホストサーバー

AtomS3 とブラウザからの要求を受け、LLM（返事の文章）→ Irodori-TTS（音声合成）→ Moonshine（音声認識）を
まとめて処理する FastAPI サーバーです。Python 環境は uv で管理します。

はじめて準備する場合は、手順を順番に説明した **[docs/setup-guide.md](../docs/setup-guide.md)** を参照してください。
ここでは、準備済みの人向けの要点をまとめます。実機と Web 画面の使い方は [docs/manual.md](../docs/manual.md)、
サーバーの中のしくみは [docs/architecture.md](../docs/architecture.md) にあります。

## 起動

```bash
cp .env.example .env                       # 初回のみ。LLM の項目を編集する
uv sync --extra dev
uv run python scripts/check_setup.py       # 準備状況の点検（NG があれば直し方を表示）
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- `.env` は `app/config.py` が読み込み時に自動で読みます（既存の環境変数が優先）。設定項目はすべて
  `AI_VOICE_ATOM_` で始まり、意味は [.env.example](.env.example) のコメントにあります。
- 動作確認: `http://localhost:8000/api/status`（`llm`/`tts`/`stt` が `configured`）、
  Web 画面は `http://localhost:8000/frontend/`。
- Moonshine はサブモジュール `../third_party/moonshine/python` を editable で使います（`tool.uv.sources`）。
  ネイティブライブラリはサブモジュールに含まれないので、`scripts/fetch_moonshine_native.py` で取得します。

## 補助スクリプト（`scripts/`）

| スクリプト | 用途 |
|---|---|
| `check_setup.py` | 準備状況（submodule・TTS 環境・Moonshine ライブラリ・LLM 接続・設定ファイル）を点検する。`uv sync` 前でも `python` で実行可 |
| `fetch_moonshine_native.py` | Moonshine のネイティブライブラリを PyPI の wheel から取り出して配置する（STT に必須）。`--force` で取り直し |
| `generate_mode_prompts.py` | ファームのモード切替の音声案内を参照音声の声色で合成し、`firmware/atom/include/mode_prompts.h` を生成する |
| `scenario_conversation.py` | 実 LLM でリレー会話をテキストだけで回し、ペルソナ同士の会話の自然さを確かめる |

## ペルソナ定義（device_profiles.json）

複数の AtomS3 を、別々の性格・声のキャラクターとして動かすための定義です。
[device_profiles.example.json](device_profiles.example.json) を `device_profiles.json` にコピーして編集します
（場所は `AI_VOICE_ATOM_DEVICE_PROFILES` で変更可）。キーはファームウェアの `DEVICE_ID` です。
未登録の ID はグローバル設定（`.env` の system プロンプト・`voices/reference.wav`）で動きます。
読み込みはサーバー起動時なので、**編集したらサーバーを再起動**してください。

| 項目 | 内容 |
|---|---|
| `display_name` | Web 画面に出す名前 |
| `ref_wav` | 声色の参照音声（`host/` 基準の相対パス。例 `voices/genki.wav`）。実在するときだけ使う |
| `voice_caption` | 参照音声を使わず、文章で声を指定する（例「落ち着いた女性の声で、やわらかく読み上げて」）。`ref_wav` と併用すると「その声＋このスタイル」。初回は専用モデルのロードで遅い |
| `system_prompt` | LLM への指示を直接書く（あれば最優先） |
| `persona` / `speaking_style` / `first_person` / `interests` | `system_prompt` を省いたとき、これらから指示を組み立てる（性格・口調・一人称・好きな話題の配列） |
| `temperature` / `presence_penalty` / `frequency_penalty` | このキャラクターだけの生成パラメータ（省略時は `.env` の値） |
| `opening_line` | リレー会話でこのキャラクターが最初に話すときの一言 |

## API の概要

詳細は [docs/backend-api.md](../docs/backend-api.md) を参照してください。

| エンドポイント | 用途 |
|---|---|
| `GET /api/health` / `GET /api/status` | 死活確認 / LLM・TTS・STT の設定状況 |
| `GET /api/devices` | ペルソナ一覧（Web 画面の選択肢） |
| `POST /api/chat/audio` | 実機の主経路。返事を 16kHz/mono/16-bit の WAV で返す（本文はヘッダ `X-LLM-Text-B64`） |
| `POST /api/chat` | JSON 版のチャット |
| `POST /api/chat/say` | 指定テキストをペルソナの声で合成（LLM 不使用。リレー会話で実機が使う） |
| `POST /api/tts` | TTS 単体 |
| `POST /api/playback/stop` | 再生停止の通知 |
| `WS /ws/audio` | 音声認識（`session.start`/`audio.chunk`/`audio.end`）と、実機の登録・再生指示（`register`/`play`/`played`） |
| `POST /api/conversation/start` / `stop` / `GET status` | リレー会話の開始・停止・状態（出力先に同じ実機を指定すれば 1 台で 2 体が話す） |
| `POST /api/conversation/chat` | Web から実機のキャラクターに話しかける（実機の再生完了後に返事の文章を返す） |

## テスト・Lint

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

テストは実際の GPU・TTS・STT を起動せず、サービスのコンストラクタにフェイクを渡して検証します
（`tests/test_chat.py` の `_FakeOpenAIClient` / `_FakeIrodoriAdapter` が見本）。
