# ペルソナ設定 / AI 同士の会話 自然化 — 改善ロードマップ

> **この文書の位置づけ**: キャラクター同士の会話を自然にする改善の計画と進み具合の記録です。
> ほかの文書は [docs/README.md](../README.md) から探せます。

複数 AtomS3 を別個の AI として会話させる際の「ペルソナ設定」と「AI 同士の会話の自然さ」を
段階的に改善するための計画と進捗。対象経路は **実機リレー（Phase B-1）優先**。

## 進捗サマリ

| 群 | 項目 | 状態 |
| --- | --- | --- |
| A1 | 構造化ペルソナ（persona / speaking_style / first_person / interests から system 合成） | ✅ 実装済 |
| A2 | ペルソナ別サンプリング（temperature / presence_penalty / frequency_penalty 上書き） | ✅ 実装済 |
| A3 | ペルソナ別の口火 `opening_line` | ✅ 実装済 |
| B1 | 相手認識（partner のペルソナ名・性格を system に注入し名前で呼びかけ） | ✅ 実装済 |
| B2 | オープニング行を話者の履歴に記録（記憶の非対称を解消） | ✅ 実装済 |
| B3 | 会話フェーズ指示（残りターン数から導入／締めを動的生成） | ✅ 実装済 |
| B4 | 反復ガード（自分の前回発言と違う切り口を促す） | ✅ 実装済 |
| C1 | TTS 前の文整形（マークダウン/絵文字/URL 除去・記号連続の畳み・改行→句点） | ✅ 実装済 |
| C2 | 感情・速度ヒント（irodori-tts の対応範囲・要調査） | ⬜ 未着手 |
| D1 | プロファイル検証 + 声色プレビュー導線 | ⬜ 未着手 |
| D2 | フロントのペルソナ編集 UI | ⬜ 未着手 |

第1弾（A 群 + B 群）は実装・テスト済み。第2弾候補は C 群（TTS 自然化）/ D 群（設定体験）。

## 実装済みの設計（A 群 + B 群）

### device_profiles.json スキーマ（拡張・すべて後方互換）

`device_id → プロファイル`。項目はすべて任意。雛形は `host/device_profiles.example.json`。

- `display_name` — フロント表示名 / `ref_wav` — 声色リファレンス wav。
- `system_prompt` — 明示の system プロンプト（**あれば最優先**）。
- 構造化ペルソナ（`system_prompt` 省略時に system を合成。`config.COMMON_VOICE_STYLE` の読み上げ制約を自動付与）:
  `persona`（性格）/ `speaking_style`（口調）/ `first_person`（一人称）/ `interests`（話題の配列）。
- ペルソナ別サンプリング: `temperature` / `presence_penalty` / `frequency_penalty`（未指定はグローバル `llm_*`）。
- `opening_line` — リレー会話でこのペルソナが最初の話者のときに使う口火。

### コードの要点

- `host/app/device_profiles.py` — `DeviceProfile` 拡張 + ローダ（`_clean_str` / `_clean_interests` /
  `_clean_float`）。不正値は警告ログを出して None（= グローバルにフォールバック）。
- `host/app/services/chat_service.py`
  - `_persona_base_prompt` — ①明示 system ②構造化合成 ③グローバル既定 の優先順位。
  - `_synthesize_persona_prompt` — 構造化フィールド → 読み上げ向け system（A1）。
  - `_partner_block` — 相手認識ブロック（B1）。
  - `_resolve_system_prompt(device_id, partner_id, directive)` — ペルソナ本体 + 相手認識 + 対話ガイダンス
    + ターン指示 を空行連結。非リレー経路は後者2つを省くので従来出力と同一。
  - `_llm_sampling_kwargs(device_id)` — ペルソナ別 → グローバルの順で解決（A2）。
  - `generate_reply(..., partner_id, directive)` / `relay_opening_line` / `record_relay_opening`。
- `host/app/services/conversation_orchestrator.py` — `_run` でターンごとに partner と
  `_directive`（B3 フェーズ + B4 反復ガード）を渡す。最初の話者の口火（`opening_text` か
  ペルソナ `opening_line`）を逐語再生する場合も `record_relay_opening` で履歴に残す（B2）。
- `host/app/config.py` — `COMMON_VOICE_STYLE`（合成 system に付ける共通の読み上げ制約）。

### テスト

- `host/tests/test_chat.py` — 構造化 system 合成 / ペルソナ別サンプリング / 相手認識・指示注入。
- `host/tests/test_conversation.py` — partner と締め指示の注入 / 口火の逐語使用と履歴記録 /
  口火が無いとき turn0 も生成。
- 既存の後方互換テスト（対話ガイダンス連結・サンプリング・履歴分離）も維持。

### 脱線対策（2026-06-26 改修・gemma4 シナリオ検証）

実機 gemma4 で会話させると話題が毎ターン飛ぶ脱線が観測された。原因はコード側の3点:
①対話ガイダンスが「毎回新しく一つ加える」、②B4 反復ガードが「**違う切り口・新しい話題で**続けて」と
明示的に話題転換を要求（本来はエコー崩壊＝逐語コピー防止が目的）、③中間ターンに話題継続のアンカー無し。

- `config.py::DEFAULT_LLM_DIALOGUE_GUIDANCE` — 「相手のいまの話題に反応し一歩だけ深める／話題を変えるのは
  一区切りしたときだけ／一度に新しい話題をいくつも持ち出さない」へ改稿。
- `conversation_orchestrator.py::_directive` — 中間ターンに「相手の直前の話題を引き継ぎ、新しい話題に飛ばず
  一歩だけ掘り下げる」アンカーを追加。B4 は「自分の言い回しの逐語反復だけを避ける」へ限定し話題転換は求めない。
- 検証ツール `host/scripts/scenario_conversation.py`（後述）で改修前後を比較。天気→星空→パフェ→カメラ…の
  全脱線が、天気→お茶→紅茶→…と軸を保つ会話に改善。`test_conversation.py` は「導入」「最後の発言」依存のみで非破壊。

**残課題**: `device_profiles.json` の `げんき` の persona「新しい話題に飛びつく」自体が脱線を誘発する。
キャラ設計の判断なので未変更。落ち着かせたい場合はこの一文を緩めると効果が高い。

### シナリオ検証ツール

- `host/scripts/scenario_conversation.py` — 実 LLM（Ollama）でリレー会話をテキストだけ回し会話ログを出力。
  本番の `ConversationOrchestrator._directive` と `ChatService.generate_reply` を流用（TTS/実機は不使用）。
  例: `AI_VOICE_ATOM_LLM_BASE_URL=http://<host>:11434/v1 PYTHONPATH=. python scripts/scenario_conversation.py
  --scenario genki_ottori --turns 8`（`--all` で全シナリオ）。WSL2 からは Windows ホストの Ollama を
  ゲートウェイ IP（`ip route` の default、例 `172.19.224.1`）経由で叩く。

### C1 の実装（済）

- `host/app/utils/text_normalize.py` の純関数 `normalize_for_tts(text)`。URL・行頭の箇条書き記号・
  マークダウン装飾（`* _ \` ~ # > |`）・絵文字/矢印を除去し、改行を句点で連結（直前が句読点なら補わない）、
  `！！/？？/。。/、、` などの連続を 1 つに畳み、連続空白を整理する。**音声合成へ渡す直前にだけ**適用し、
  表示用テキスト（`X-LLM-Text` / `llm_text`）は元のまま。全消去され得るので呼び出し側は `... or text` で
  元文へフォールバックする。
- `chat_service.py` の `_synthesize_audio_16k` / `_synthesize_audio` の冒頭で適用（実機リレー含む全合成経路）。
- テスト `host/tests/test_text_normalize.py`（純関数・submodule 不要、11 件）。

## 次にやるなら（第2弾の入口）

- **C2**: `IrodoriTtsAdapter.synthesize` に感情/速度を渡せるか submodule の `infer.py` / `tts_server.py`
  を調査。対応すればプロファイルに `emotion` / `speed` を追加。
- **D1**: 起動時のプロファイル検証ログ強化 + フロントから `/api/chat/say` で声色を試聴する導線。
- **D2**: フロントにペルソナ編集 UI（`/api/devices` の拡張 + 書き込み API が必要）。

## 開発メモ（環境）

- WSL2 から `host/.venv`（Windows 生成・`Scripts/` `Lib/` 構成）は I/O エラーで `uv run` が失敗する。
  Linux 側 tmp に venv を作って検証する: `uv venv /tmp/ava-venv --python 3.12` →
  `uv pip install --python /tmp/ava-venv/bin/python fastapi pydantic openai numpy pytest httpx` →
  `/tmp/ava-venv/bin/python -m pytest tests/test_chat.py tests/test_conversation.py`。
  moonshine submodule が要るテスト（`test_devices` / `test_say_endpoint` 等）は Windows 側の
  `uv run pytest` で確認する。
