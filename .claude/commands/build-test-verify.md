---
description: 品質ゲート（Done の定義）を全部走らせて緑を証明する
---

`docs/ai-native-rules.md` 2.2 の品質ゲートを順に実行し、結果を報告する。**落ちたら止まらず原因を読んで修正**し、再実行して緑にする。すべて `host/` から実行する。

> 注意: WSL で `host/.venv` が Windows 側だと `uv run` が I/O エラーになることがある（既知）。その場合は `MEMORY.md` の「WSL venv の uv I/Oエラー」回避策（tmp に Linux venv）を使う。

1. **submodule 初期化確認**
   ```bash
   git submodule update --init --recursive
   ```
2. **テスト**（フェイク注入。実 GPU/TTS/STT は起動しない）
   ```bash
   cd host && uv run pytest
   ```
   - 特定ファイル: `uv run pytest tests/test_chat.py`
   - バグ修正なら、先に再現テストが `tests/` にあることを確認する。
3. **Lint**
   ```bash
   cd host && uv run ruff check .
   ```
   - 自動修正可能なものは `uv run ruff check --fix .`。意図しない変更がないか diff を確認。
4. **整形チェック**
   ```bash
   cd host && uv run ruff format --check .
   ```
   - 差分があれば `uv run ruff format .` で整形。
5. **Constitution 点検** — `/self-review` を併用し、第1章 EARS 要件への違反がないか確認。

最後に各ゲートの合否を箇条書きで報告する。**1つでも赤なら「完了」と言わない。** 実機/実音声が要る変更は `docs/verification-guide.md` の該当手順を人間に依頼する。
