# ai-voice-atom

M5Stack **AtomS3-Lite + Atomic Echo Base** を、PC 上の AI（LLM・音声合成・音声認識）とつないで
しゃべらせるプロジェクトです。ボタンを押すと AI が好きな声で返事をしたり、複数台の AtomS3 に
別々のキャラクターを割り当てて会話させたりできます。

## できること

- **話しかける**: ボタンで録音して話しかけると、AI が文字起こし → 返事 → 音声合成して AtomS3 から答えます。
- **キャラクターと声**: デバイスごとに性格・口調・声色を設定できます（参照音声の声を真似る／文章で声を指定）。
- **AI 同士の会話**: 2 体のキャラクターを交互に話させます。実機 2 台に分けても、**実機 1 台**で両方の声を出しても遊べます。
- **ブラウザから操作**: Web 画面から会話を始めたり、実機のキャラクターに文字で話しかけたりできます。
- **単体で遊ぶ**: ホストも Wi-Fi も無しで、録音再生・オウム返し（話しかけると変な声で言い返す）・
  声で知らせるタイマーが使えます。
- **音声案内**: モードを切り替えると、好きな声で「〇〇モードですわ」と案内します（任意）。

## はじめ方

**➡ [docs/setup-guide.md（はじめてのセットアップガイド）](docs/setup-guide.md)**

準備ができたら、使い方は **[docs/manual.md（操作マニュアル）](docs/manual.md)** を参照してください。

ソフトのインストールから、ホスト PC の準備、AtomS3 への書き込み、動作確認までを順番に説明しています。
準備の途中で `host/scripts/check_setup.py` を実行すると、足りないものと直し方を表示します。

準備済みの人向けの最短手順:

```bash
git clone --recurse-submodules https://github.com/kou1-310/ai-voice-atom.git
cd ai-voice-atom/host
cp .env.example .env                                  # LLM の項目を編集
uv sync --extra dev
uv run python scripts/fetch_moonshine_native.py       # 音声認識のネイティブライブラリ
(cd ../third_party/irodori-tts && uv sync --extra cu128)   # GPU なしは --extra cpu
uv run python scripts/check_setup.py
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
# ファーム: firmware/atom/.env を作成し、VS Code + PlatformIO で env m5stack-atoms3 を Upload
```

## 構成

```
ai-voice-atom/
├── host/            ホストサーバー（FastAPI / Python・uv）          → host/README.md
├── firmware/atom/   AtomS3 のファームウェア（PlatformIO / C++）     → firmware/atom/README.md
├── frontend/        Web 画面（/frontend で配信）
├── third_party/     サブモジュール: irodori-tts（音声合成）、moonshine（音声認識）
├── hardware/        フィギュアケースの 3D データ                    → hardware/figure-case/README.md
└── docs/            ドキュメント（入口は docs/README.md）
```

## デバイスの使い方（モードと操作）

AtomS3-Lite は上面全体が 1 つのボタンです。操作は 3 種類です。

| 操作 | やり方 | 働き |
|---|---|---|
| クリック | 短く押して離す | モードごとの主な操作 |
| ダブルクリック | クリックを 2 回続ける（1 回目を離してから 0.5 秒以内に 2 回目を押す） | 録音の**再生**、タイマーの時間切替 |
| 長押し | 1.2 秒以上押す | 次のモードへ切り替える |

長押しのたびに `チャット → 音声会話 → リレー会話 → 録音再生 → オウム返し → タイマー → チャット …` と切り替わり、
声でモード名を案内します。最後に選んだモードは記憶され、電源を入れ直しても同じモードで始まります。

| モード（待機中の LED） | どんなモードか | クリック | ダブルクリック | ホスト |
|---|---|---|---|---|
| **チャット** web（青） | 決まった一言を AI に送り、返事を聞く | 「こんにちは」と話しかける | － | 必要 |
| **音声会話** voice（緑） | 自分の声で話しかけ、AI の返事を聞く | 録音開始 → もう一度で停止し、AI が返事 | － | 必要 |
| **リレー会話** relay（シアン） | ブラウザから操作して、キャラクター同士の会話や話しかけを実機で再生する | －（Web 画面で操作） | － | 必要 |
| **録音再生** recorder（白） | 最大 30 秒を録って聞き直す。電源を切っても残る | 録音開始 → もう一度で停止 | **録音を再生** | 不要 |
| **オウム返し** parrot（黄） | 話しかけると自動で録音し、高い声・低い声などで言い返す | 声の種類を切り替え | － | 不要 |
| **タイマー** timer（紫） | 時間になると声で知らせる（残り 1 分でも知らせる） | スタート／中止／アラームを止める | 時間を切り替え（1・3・5・10・25 分） | 不要 |

- 「ホスト 必要」のモードは、ホスト PC のサーバーが動いていて同じ Wi-Fi につながっているときに使えます。
  LED が**赤**ならホストに届いていません。**オレンジ**は Wi-Fi に接続中です。
- 「不要」の 3 モードは、Wi-Fi もホストも無い場所で、USB 給電だけで動きます。
- 再生中の LED はマゼンタ、録音中は点滅です。

各モードの手順、LED と音の意味、Web 画面の使い方、困ったときの対処は
**[docs/manual.md（操作マニュアル）](docs/manual.md)** にまとめています。

## ドキュメント

全文書の案内は **[docs/README.md](docs/README.md)** にあります。主なものは次のとおりです。

| 目的 | 文書 |
|---|---|
| はじめて準備する | [docs/setup-guide.md](docs/setup-guide.md) — インストールから書き込み、動作確認まで |
| 使い方を知る | [docs/manual.md](docs/manual.md) — ボタン操作、各モード、LED と音、Web 画面、困ったとき |
| うまく動かないとき | [docs/verification-guide.md](docs/verification-guide.md) — 動作確認と切り分けの手順 |
| 重いとき | [docs/performance-tuning.md](docs/performance-tuning.md) — GPU メモリやスレッド数の調整 |
| しくみを知る・コードを読む | [docs/architecture.md](docs/architecture.md) — 全体のしくみとファイルの役割 |
| ホストを動かす・キャラクターを作る | [host/README.md](host/README.md) |
| ファームを書き込む・設定する | [firmware/atom/README.md](firmware/atom/README.md)（WSL2 からは [UPLOAD_GUIDE.md](firmware/atom/UPLOAD_GUIDE.md)） |
| API を使う | [docs/backend-api.md](docs/backend-api.md) |
| 開発に参加する | [docs/ai-native-rules.md](docs/ai-native-rules.md)、[CLAUDE.md](CLAUDE.md)（AI エージェント向けの規約） |

## ライセンス

このリポジトリのソースコード・ドキュメント・ケースの 3D データは [MIT License](LICENSE) で公開しています。
`third_party/` のサブモジュールと、実行時に取得する AI モデルは対象外で、それぞれのライセンスに従います（下記）。

## 利用上の注意

- 外部の部品はサブモジュールとして取り込んでいます。それぞれのライセンスに従ってください:
  [Irodori-TTS](https://github.com/Aratako/Irodori-TTS)、[Moonshine](https://github.com/moonshine-ai/moonshine)
  （日本語モデルは Moonshine Community License で、商用利用に制限があります）。
- 声色の参照音声（`host/voices/*.wav`）と、そこから生成した音声案内は Git に含めません。
  他人の声やキャラクター音声を使う場合は、その利用規約に従ってください
  （例: VOICEVOX の四国めたんは「VOICEVOX:四国めたん」のクレジット表記が必要）。
- AtomS3（ESP32-S3）は Bluetooth Classic に対応していないため、Bluetooth オーディオ（A2DP）は使えません。
