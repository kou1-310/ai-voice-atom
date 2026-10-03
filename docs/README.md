# ドキュメントの入口

ai-voice-atom のドキュメント一覧です。目的に合う文書から読んでください。

## まず読むもの

| やりたいこと | 読む文書 |
|---|---|
| どんなプロジェクトか知りたい | [リポジトリの README](../README.md) |
| はじめて準備する（インストール〜書き込み） | [setup-guide.md](setup-guide.md) — セットアップガイド |
| **使い方を知りたい**（ボタン操作・各モード・LED・Web 画面） | [manual.md](manual.md) — 操作マニュアル |
| うまく動かない | [manual.md の「困ったとき」](manual.md#10-困ったとき) → [verification-guide.md](verification-guide.md) |
| しくみを知りたい・コードを読みたい | [architecture.md](architecture.md) — しくみとコードの歩き方 |

## 文書の一覧

### 使う人向け

| 文書 | 内容 |
|---|---|
| [setup-guide.md](setup-guide.md) | ソフトのインストール、ホスト PC の準備、AtomS3 への書き込み、最初の動作確認 |
| [manual.md](manual.md) | 毎日の使い方。ボタン操作、6 つのモード、LED と音の意味、Web 画面、困ったとき |
| [verification-guide.md](verification-guide.md) | 動作確認の手順書。ホスト単体 → 実機の順に、どこまで動いているかを切り分ける |
| [performance-tuning.md](performance-tuning.md) | PC が重いときの調整（GPU メモリ、スレッド数、LLM の設定） |

### しくみ・仕様を知りたい人向け

| 文書 | 内容 |
|---|---|
| [architecture.md](architecture.md) | 全体のしくみ、会話 1 回の流れ、フォルダとファイルの役割 |
| [backend-api.md](backend-api.md) | ホストの API 仕様（HTTP と WebSocket） |
| [specification.md](specification.md) | 仕様書。目的、役割分担、機能要件、制約 |
| [use-cases.md](use-cases.md) | ユースケースの一覧と詳細 |

### 開発する人向け

| 文書 | 内容 |
|---|---|
| [ai-native-rules.md](ai-native-rules.md) | 開発の進め方（仕様を先に書く・テストで確かめる・失敗を記録する） |
| [templates/spec-template.md](templates/spec-template.md) | 機能を足すときの仕様書（Spec）のひな形 |
| [templates/lessons-learned.md](templates/lessons-learned.md) | 不具合や失敗の記録と、その後の対策 |
| [specs/](specs/) | 機能ごとの Spec（下の表） |
| [plans/](plans/) | 実装の計画と経緯の記録（下の表） |
| [../CLAUDE.md](../CLAUDE.md) | AI エージェント（Claude Code）向けの規約。設計の要点も詰まっている |

**機能ごとの Spec（`specs/`）** — その機能を作ったときに決めたこと。

| 文書 | 対象 |
|---|---|
| [specs/mode-guidance-recorder-spec.md](specs/mode-guidance-recorder-spec.md) | モード切替の音声案内、録音再生モード |
| [specs/parrot-timer-spec.md](specs/parrot-timer-spec.md) | オウム返しモード、タイマーモード |

**計画と記録（`plans/`）** — 当時の計画と進み具合。現在の動作は上の文書とコードを正とします。

| 文書 | 対象 |
|---|---|
| [plans/implementation-plan.md](plans/implementation-plan.md) | 実装状況の一覧と、これまでの実装計画 |
| [plans/frontend-auto-conversation-plan.md](plans/frontend-auto-conversation-plan.md) | Web 画面の自動会話とデザイン刷新の計画 |
| [plans/persona-conversation-improvements.md](plans/persona-conversation-improvements.md) | キャラクター同士の会話を自然にする改善の記録 |

### docs の外にある文書

| 文書 | 内容 |
|---|---|
| [../host/README.md](../host/README.md) | ホストの起動、補助スクリプト、キャラクター（ペルソナ）の定義 |
| [../firmware/atom/README.md](../firmware/atom/README.md) | ファームウェアの設定項目、書き込み、シリアルログ |
| [../firmware/atom/UPLOAD_GUIDE.md](../firmware/atom/UPLOAD_GUIDE.md) | WSL2 から書き込む手順 |
| [../hardware/figure-case/README.md](../hardware/figure-case/README.md) | フィギュア型ケースの 3D データ |
| [../DESIGN.md](../DESIGN.md) | Web 画面のデザインガイド |

## 用語

| 用語 | 意味 |
|---|---|
| デバイス（実機） | AtomS3-Lite と Atomic Echo Base を重ねたもの。ボタン・マイク・スピーカー・LED を持つ |
| ホスト | AI の処理を受け持つ PC と、その上で動くサーバー（`host/`） |
| LLM | 返事の文章を考える AI。Ollama など、OpenAI 互換の API で呼ぶ |
| TTS（音声合成） | 文章を声にする処理。Irodori-TTS を使う |
| STT（音声認識） | 声を文章にする処理。Moonshine を使う |
| ペルソナ | キャラクターの設定（性格・口調・声）。デバイス ID ごとに割り当てる |
| 参照音声 | 声色の手本にする音声ファイル。この声を真似て合成する |
| モード | デバイスの動作の種類。長押しで切り替える（チャット・音声会話・リレー会話・録音再生・オウム返し・タイマー） |
| 単体モード | ホストも Wi-Fi も使わないモード（録音再生・オウム返し・タイマー） |
| リレー会話 | ホストが進行役になって、キャラクター同士を交互に話させる会話 |
| 音声案内 | モードを切り替えたときに流れる「〇〇モードですわ。」の声 |
| サブモジュール | このリポジトリに取り込んでいる外部のリポジトリ（`third_party/`） |

## 文書を直すとき

- 操作方法（ボタン・モード・LED）を変えたら、[manual.md](manual.md)、[リポジトリの README](../README.md) のモード表、
  [firmware/atom/README.md](../firmware/atom/README.md) をそろえて直します。
- セットアップの手順を変えたら [setup-guide.md](setup-guide.md) を直します。
- API を変えたら [backend-api.md](backend-api.md) を直します。
- 新しい文書を足したら、この一覧に追加します。
