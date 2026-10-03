---
description: 機能追加の Spec を雛形から起こす（実装はしない）
argument-hint: <機能名 / やりたいこと>
---

`docs/ai-native-rules.md` 第1章に従い、機能「$ARGUMENTS」の Spec を作成する。**この段階では実装しない。** 人間が確定するまでが目的。

手順:

1. `docs/templates/spec-template.md` を読み、構造を踏襲する。
2. 関連コード・仕様を実コードで確認してから埋める（記憶はヒント、事実は実コード）。影響しそうな層を `host/app/api → services → adapters` や `firmware/atom/`、`third_party/` から特定する。
3. 6要素をすべて埋める。空欄を憶測で埋めない — 不明点は人間に質問する。
   - Outcomes / Scope境界（触ってよいファイル＝所有権）/ Constraints（音声 16k mono・エラー契約・設定は env のみ・日本語）/ 既決事項 / タスク分解 / 検証基準。
4. 要件は **EARS 形式**（ユビキタス／状態駆動／イベント駆動）で単一・検証可能に書く。
5. タスク分解は **ファイル所有権が重ならない**単位にし、各タスクを SPEC テスト（Scope/Precondition/Expected/Criteria）で検算する。
6. 成果物を `docs/specs/<機能名>.md` として書き出し、要点と**人間に確認したい決定事項**を提示する。

Spec が固まったら、承認後に PLAN→IMPLEMENT へ進む（`docs/ai-native-rules.md` 付録の標準フロー）。
