# Spec: モード切替の音声案内 ＋ 単体動作の録音再生モード

> `docs/ai-native-rules.md` 第1章に従う。2026-09-23 作成。

## Outcomes（成功の定義 / ユーザー・デバイス視点）

- 長押しでモードを切り替えると、四国めたん声色の音声で「〇〇モードですわ」と案内される（起動時も現在モードを案内）。
- ホスト・Wi-Fi が無くても「録音再生モード」で遊べる: クリックで録音開始/停止、ダブルクリックで再生。
- 録音は電源を切っても残る。最後に選んだモードで次回起動する。

## Scope（範囲境界）

- やること:
  - ホスト側スクリプト `host/scripts/generate_mode_prompts.py`: Irodori-TTS（参照音声 = 四国めたん）で案内音声を合成し、
    16kHz/mono/16-bit WAV に変換してファーム埋め込み用ヘッダ `firmware/atom/include/mode_prompts.h` を生成する。
  - ファーム: 案内音声の再生（ヘッダが無ければ従来のチャイムへフォールバック）、モードの NVS 永続化、
    `recorder` モード（LittleFS へ録音・再生）の追加。モード巡回は `web → voice → relay → recorder → web`。
- やらないこと:
  - **Bluetooth オーディオ**（AtomS3 の ESP32-S3 は Bluetooth Classic 非搭載で A2DP 不可。別ハード入手時に別 Spec）。
  - 録音の複数スロット管理、ホストへの録音アップロード。
- 触ってよいファイル: `host/scripts/generate_mode_prompts.py`, `host/tests/test_generate_mode_prompts.py`,
  `firmware/atom/src/main.cpp`, `firmware/atom/README.md`, `.gitignore`, `CLAUDE.md`, 本 Spec。
- 触ってはいけない: `utils/wav.py` の出力フォーマット、`third_party/`、ホスト API 契約。

## Constraints（制約・本プロジェクト固有）

- 音声: 案内音声は 16kHz/mono/16-bit（`utils/wav.py::to_pcm16_mono` で変換）。録音も 16kHz/mono/16-bit PCM。
- 著作権: 参照音声とそこから生成した案内音声（WAV・ヘッダ）はコミットしない（`.gitignore`）。
  利用時はクレジット「VOICEVOX:四国めたん」を表記する。
- 実機: AtomS3-Lite は PSRAM 無し（SRAM 約 320KB）。録音は RAM に溜めず LittleFS（`spiffs` パーティション 1.5MB）へ逐次書き込む。
- 単体動作: `recorder` モード中は Wi-Fi 再接続・ヘルスチェック（ブロッキング HTTP）・WebSocket を行わない（録音の取りこぼし防止）。
- 言語: コメント・文言・docs は日本語。Python は 3.11+ 型ヒント付き。

## Prior decisions（既決事項と理由）

- Bluetooth は今回見送り（ユーザー決定 2026-09-23。ESP32-S3 は A2DP 非対応）。
- 録音再生は 1 モードに統合（ユーザー決定）。クリック = 録音開始/停止、ダブルクリック = 再生、再生中クリック = 停止。
- 長押し閾値 1.2s に M5Unified 標準のダブルクリック判定窓が連動し録音開始が遅れるため、自前判定にする。
  判定窓は「1 回目を離してから 2 回目を押すまで 500ms」（2026-10-03 改訂。当初は 2 回目を離すまで 400ms で、
  少し遅いダブルクリックが「録音開始→即停止」になり前の録音を 0.1 秒の録音で上書きしていた）。
- 録音は `/rec.tmp` へ書き、0.5 秒以上録れた時点で前の録音を消して、停止時に `/rec.pcm` へ改名する。
  0.5 秒未満で止めた録音は誤操作として捨て、前の録音を再生する（2026-10-03 追加）。
- 案内音声は LittleFS でなくファーム埋め込み（`uploadfs` が録音を消すのを避け、書き込み手順を 1 回にするため）。
  ヘッダは `__has_include` で任意とし、未生成でもビルドできる。

## Requirements（EARS 形式）

- モードが切り替わったとき、システムはそのモードの案内音声を再生しなければならない。案内音声が埋め込まれていない場合は従来のチャイムを鳴らさなければならない。
- 起動したとき、システムは NVS に保存された前回のモードで開始し、そのモードを案内しなければならない。
- `recorder` モードである間、システムは Wi-Fi・ホストの状態に関係なく録音・再生できなければならない。
- `recorder` モードでクリックしたとき、録音中でなければ録音を開始し、録音中なら停止して保存しなければならない。
- 録音が最大長（30 秒）またはストレージ空き容量に達したとき、システムは自動で録音を停止し保存しなければならない。
- `recorder` モードでダブルクリックしたとき、システムは保存済みの録音を再生しなければならない。録音が無ければ「まだ録音がありませんわ」を案内しなければならない。
- 生成スクリプトは各案内文を 16kHz/mono/16-bit WAV に変換し、前後の無音を詰めてヘッダへ書き出さなければならない。

## Task breakdown

1. 生成スクリプト＋テスト  owner-files: `host/scripts/generate_mode_prompts.py`, `host/tests/test_generate_mode_prompts.py`
2. ファーム: 案内音声・モード永続化  owner-files: `firmware/atom/src/main.cpp`
3. ファーム: recorder モード  owner-files: `firmware/atom/src/main.cpp`
4. ドキュメント  owner-files: `firmware/atom/README.md`, `CLAUDE.md`

## Verification criteria（Done の条件）

- [ ] `uv run pytest` 全パス（生成スクリプトのフェイク TTS テストを含む）
- [ ] `uv run ruff check .` / `uv run ruff format --check .` クリーン
- [ ] `pio run`（env `m5stack-atoms3`）がヘッダ有り・無しの両方でビルド成功
- [ ] 実機（人間）: モード巡回で案内が流れる／Wi-Fi 無しで録音→ダブルクリック再生→電源再投入後も再生できる
