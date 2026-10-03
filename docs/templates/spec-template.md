# Spec: <機能名>

> `docs/ai-native-rules.md` 第1章に従う。6要素＋EARS＋受け入れ基準が揃って初めて実装に着手する。
> 欠けた要素を AI に「埋めさせない」こと（ドリフトの起点になる）。

## Outcomes（成功の定義 / ユーザー・デバイス視点）

- <何が達成できたら成功か。例: atoms3-002 が atoms3-001 と 6 ターンのリレー会話を ack 落ちなく完走する>

## Scope（範囲境界）

- やること: <...>
- やらないこと: <...>
- 触ってよいファイル（所有権）: <例: host/app/services/conversation_orchestrator.py, host/tests/>
- 触ってはいけない: <例: utils/wav.py の出力フォーマット、third_party/ の固定先>

## Constraints（制約・本プロジェクト固有）

- 音声: デバイス経路は 16kHz/mono/16-bit WAV を厳守（`utils/wav.py::to_pcm16_mono`）。
- エラー契約: 利用不可/失敗→`RuntimeError`(503)、不正入力→`ValueError`(400)。
- 設定: `AI_VOICE_ATOM_` 環境変数のみ。外部設定ライブラリ追加禁止。標準ライブラリで解析。
- 言語: コメント・文言・docs は日本語。Python は 3.11+ 型ヒント付き。
- 性能: 単一 GPU 競合に配慮（`docs/performance-tuning.md`）。
- 互換: 未登録 device_id はグローバル設定へフォールバック。

## Prior decisions（既決事項と理由）

- <なぜこの設計か。過去の判断との整合。例: リレーを既定にしたのは空気越し STT が不安定だから>

## Requirements（EARS 形式 / 単一・検証可能）

- システムは … しなければならない。
- … のとき、システムは … しなければならない。
- … である間、システムは … してはならない。

## Task breakdown（並行委譲単位 / ファイル所有権が重ならないこと）

1. <...>  owner-files: <...>
2. <...>  owner-files: <...>

各タスクは SPEC テストを満たすか検算する: **S**cope一意 / **P**recondition明確 / **E**xpected定義済 / **C**riteria（緑の条件）あり。

## Verification criteria（Done の条件 / `docs/ai-native-rules.md` 2.2）

- [ ] `uv run pytest` 全パス（この機能の再現/受け入れテストを含む）
- [ ] `uv run ruff check .` / `uv run ruff format --check .` クリーン
- [ ] Constitution（EARS 要件）非違反（/self-review）
- [ ] 必要なら `docs/verification-guide.md` の実機/実音声手順を人間が実施
- [ ] <この機能固有の受け入れ条件>
