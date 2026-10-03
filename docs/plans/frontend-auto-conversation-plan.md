# 実装計画 — フロントエンド自動会話 ＋ コーポレート基調リデザイン

> **この文書の位置づけ**: Web 画面の自動会話とデザイン刷新を行ったときの計画の記録です（実装済み）。
> 現在の使い方は [manual.md の「Web 画面の使い方」](../manual.md#5-web-画面の使い方)、ほかの文書は [docs/README.md](../README.md) から探せます。

> **状態（2026-09-23）**: Phase A は実装済み（実行モード「画面でデモ」）。Phase B は、ここで想定した
> 「録音開始の指示」ではなく、**ホスト統括のリレー会話**（`play` を push して実機が再生する方式）として
> 実装済み（実行モード「実機で会話」、実機 2 台または 1 台で 2 体）。さらに「実機に話しかける」を追加した。
> 現行の仕様は `docs/backend-api.md` §5.8 と `README.md` を参照。以下は計画時の記録。

## 背景・目的

マルチデバイス対応（`device_id` ごとのペルソナ・声色・履歴分離）が入り、実機2台の音声モードで
手動の会話が成立することは確認済み。しかし手動トリガーは手間が大きい。そこで **Web 画面から
自動で2体の AI を会話させて眺められる**ようにする。あわせてフロントエンドを企業向けコーポレート
サイト基調に**全面リデザイン**する（デザイン言語は [DESIGN.md](../../DESIGN.md)）。

会話の実現方式は2段階（ユーザー合意済み）:

- **Phase A（今回）: ブラウザ内シミュレーション** — ホストが2ペルソナの LLM 対話を1ターンずつ
  生成し、各ターンの TTS 音声をブラウザのスピーカーで再生する。**実機デバイス不要・完全自動**。
  既存の `/api/chat/audio` と `device_profiles` をそのまま流用する。実機の音響ループ（手動）は
  現状のまま残す。
- **Phase B（将来・本計画では設計メモのみ）: 実機2台の自動制御** — ホスト→デバイスのプッシュ
  経路（WS 常時接続で「録音開始／これを再生」を指示）をファームに新設して実機を自動制御する。
  ハウリング・自己音声取り込み対策が前提。今回は実装しない。

## Phase A の会話モデル

ブラウザがターン進行を駆動するクライアント主導オーケストレーション（ホスト側に新規の常駐
ループを持たせない＝既存のステートレス設計を維持）。

```
[開始] 話題（最初の一言）を seed として話者 = A から開始
 繰り返し（停止 or 最大ターンまで）:
   1. POST /api/chat/audio { device_id: <現話者>, session_id: <会話ID>, input_text: <直前の相手の発話> }
   2. レスポンス: ボディ= WAV バイナリ / ヘッダ X-LLM-Text-B64 = 応答テキスト
   3. ログにバブル追加（話者色）＋ Dialogue Bridge を現話者向きに
   4. WAV を再生し、再生終了を待つ（再生中は波形アニメ）
   5. 話者を交代（A↔B）。直前のテキストを次の input_text にする
```

- **履歴の自然な分離**: 既存の複合キー `device_id::session_id` により、同じ会話 ID でも A と B は
  別スレッドの履歴を持つ。各ペルソナは「相手の発話(user) / 自分の発話(assistant)」という自分視点の
  文脈で応答する。**バックエンド改修なしで会話が成立する**（`chat_service.py` の既存ロジック）。
- 既存 `/api/chat/audio` は WAV ＋ `X-LLM-Text` / `X-LLM-Text-B64` ヘッダを返す（`api/chat.py:25-47`）。
  ブラウザは `fetch` で `arrayBuffer()`（再生用）と `headers.get('X-LLM-Text-B64')`（表示用）を取得。
  同一オリジン配信なのでカスタムヘッダ読み取りに CORS 設定は不要。

## 変更内容

### バックエンド（最小）

1. **新規 `GET /api/devices`** — UI のペルソナ選択肢用に、登録済みプロファイルを返す。
   - 返却: `[{ "device_id": "...", "display_name": "..." }, ...]`（`display_name` 無しは device_id で代替）。
   - 実装: `api/chat.py` の `chat_service` インスタンスが持つ `chat_service.device_profiles`
     （`dict[str, DeviceProfile]`）を読むだけ。新ルーター or `api/device.py` に1エンドポイント追加。
     `main.py:create_app` に `include_router` を1行追加（`device.py` 拡張なら不要）。
   - プロファイルが空（device_profiles.json 未配置）の場合は、フォールバックとして
     `settings.device_id` 1件を返す（UI が必ず1体は選べるように）。
2. それ以外のバックエンド改修は不要（会話ループはフロント側、TTS/LLM/履歴は既存経路）。

### フロントエンド（全面刷新 — `frontend/`）

DESIGN.md のトークン／コンポーネント指針に従って3ファイルを書き換える。既存の状態ポーリングと
再生停止の挙動は維持する。

3. **`frontend/index.html`** — 構造を刷新:
   - 細いヘッダ（プロダクトマーク＋ LLM/TTS/STT 状態チップ）。
   - ヒーロー（eyebrow / h1 / lede）。
   - **会話ステージ**: ペルソナA・Bカード＋中央の Dialogue Bridge（シグネチャ）。
   - コントロールバー: ペルソナA/B セレクト（`/api/devices` から生成）、話題入力、最大ターン数、
     「会話をはじめる」「停止」。
   - 会話ログ（交互バブル）。
   - Google Fonts（Zen Kaku Gothic New / Noto Sans JP / IBM Plex Mono）の `<link>`。
4. **`frontend/app.js`** — ロジック:
   - `loadDevices()`: `/api/devices` を取得しセレクトと初期ペルソナ（A=1件目, B=2件目）を設定。
   - `runConversation()`: 上記オーケストレーションループ。`AbortController` で停止可能に。
     再生は `AudioContext.decodeAudioData`（既存 `playBase64Wav` を ArrayBuffer 版に拡張）し、
     `onended` を `await` してから次ターンへ。最大ターン数で自動終了。
   - `appendBubble(speaker, text, ts)`: 話者色のバブルをログに追加し自動スクロール。
   - ステージ更新: アクティブ話者カードの点灯・Bridge の向き・波形の on/off。
   - 既存の `refreshStatus()`（5秒ポーリング）と状態チップ更新は流用。状態は人間語に変換表示。
   - エラー処理: ターン失敗時はループ停止＋原因表示（503=バックエンド未設定/失敗、400=入力不正）。
   - 単発テキスト送信（既存 `/api/chat`）は会話機能に統合（任意の1ターン実行として残すか、削除）。
5. **`frontend/style.css`** — DESIGN.md のトークンを `:root` のカスタムプロパティとして定義し、
   全レイアウト・コンポーネント・モーションを実装。`prefers-reduced-motion` 対応、レスポンシブ
   （ステージ縦積み）、フォーカス可視。

### ドキュメント

6. **`docs/use-cases.md`** に「2体の AI を Web から自動会話させる（Phase A）」ユースケースを追記
   （任意・軽微）。

## 関係する既存資産（再利用）

- `POST /api/chat/audio`（`host/app/api/chat.py:25-47`）— WAV＋テキストヘッダ。会話ループの主経路。
- `ChatService`（`host/app/services/chat_service.py`）— `device_id::session_id` 複合キー履歴、
  `device_profiles` によるペルソナ／声色出し分け。**改修不要**。
- `GET /api/status`（`host/app/api/device.py`）— 状態チップのデータ源。
- `frontend/app.js` の `playBase64Wav` / `refreshStatus` / `stopPlayback` — 拡張して流用。

## 検証

1. **バックエンド**: `host/` で `uv run pytest`（既存緑を維持）＋ `GET /api/devices` の手動確認
   （`device_profiles.json` あり／なし両方でレスポンスを確認）。
2. **フロント（自動会話）**: uvicorn 起動 → ブラウザで `/frontend` を開く →
   - ペルソナA/B を選び話題を入れて「会話をはじめる」→ A→B→A… とテキストが交互に出て、各ターンの
     音声が**別の声色**で再生されること。
   - 「停止」で進行中ターンの後に止まること。最大ターン数で自動終了すること。
   - LLM/TTS が未設定/失敗のとき、原因が画面に出てループが安全に止まること。
3. **デザイン**: デスクトップ／モバイル幅でレイアウト崩れが無いこと、キーボードフォーカスが見えること、
   OS の「視差効果を減らす」設定でアニメが止まること。可能ならスクリーンショットで DESIGN.md の
   意図（白基調・ブルーアクセント・ステージのシグネチャ）と照合。

## スコープ外（次フェーズ・メモ）

- **Phase B 実機自動制御**: ファームに WS コマンド経路（`play.request` / `record.request`）を新設し、
  ホストがターンを駆動。自己音声の取り込み防止（再生中マイク停止）・ハウリング対策・2台の時刻同期。
- 会話の保存／書き出し、ペルソナのその場編集、3体以上の多者会話。
