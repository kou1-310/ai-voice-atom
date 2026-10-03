# はじめてのセットアップガイド

> **この文書の位置づけ**: はじめて準備する人向けの手順書です。準備が済んだあとの使い方は [manual.md](manual.md)、
> ほかの文書は [docs/README.md](README.md) から探せます。

ソースコードをダウンロードしてから、AtomS3 がしゃべるまでの手順を、最初から順番に説明します。
プログラミングに詳しくなくても進められるよう、コマンドはそのまま貼り付けて使える形で載せています。

> 所要時間の目安: 2〜3 時間（大半はソフトとAIモデルのダウンロード待ちです）
>
> 手順の途中で `host/scripts/check_setup.py` を実行すると、どこまで準備できたかを点検できます（[3-7](#3-7-準備状況を点検する)）。

---

## 目次

1. [全体像 — 何を用意するのか](#1-全体像--何を用意するのか)
2. [ソフトをインストールする](#2-ソフトをインストールする)
3. [ホスト PC を準備する](#3-ホスト-pc-を準備する)
4. [AtomS3 にファームウェアを書き込む](#4-atoms3-にファームウェアを書き込む)
5. [動かしてみる](#5-動かしてみる)
6. [2 台目以降を追加する](#6-2-台目以降を追加する)
7. [困ったとき](#7-困ったとき)
8. [最新版に更新するとき](#8-最新版に更新するとき)

---

## 1. 全体像 — 何を用意するのか

このプロジェクトは「小さなデバイス」と「頭脳役の PC」の 2 つでできています。

```
 ┌─────────────────────┐        Wi-Fi         ┌──────────────────────────────────────┐
 │ AtomS3-Lite          │  ◀───────────────▶  │ ホスト PC（このリポジトリの host/）       │
 │  + Atomic Echo Base  │   HTTP / WebSocket  │  ・サーバー（FastAPI, ポート 8000）       │
 │  ボタン・マイク・スピーカー │                      │  ・LLM（Ollama など）: 返事の文章を考える   │
 └─────────────────────┘                      │  ・Irodori-TTS: 文章を声にする            │
                                              │  ・Moonshine: 声を文章にする（音声認識）   │
          ブラウザ（スマホでも可） ──────────▶  │  ・Web 画面（/frontend）                 │
                                              └──────────────────────────────────────┘
```

- **AtomS3（デバイス）** はボタンを押されたらホストに問い合わせ、返ってきた音声を再生します。
  「録音再生」「オウム返し」「タイマー」の 3 モードは、ホスト無しで単体で遊べます。
- **ホスト PC** は AI の処理をすべて担当します。AtomS3 と**同じ Wi-Fi（同じネットワーク）**につなぎます。

### 用意するもの

| 種類 | 内容 | 補足 |
|---|---|---|
| デバイス | M5Stack **AtomS3-Lite** | 複数台で会話させるなら台数分 |
| デバイス | M5Stack **Atomic Echo Base** | マイクとスピーカー。AtomS3-Lite を上に差し込む |
| ケーブル | **USB Type-C ケーブル（データ通信対応）** | 充電専用ケーブルでは書き込めません |
| Wi-Fi | **2.4GHz 帯**の Wi-Fi | AtomS3 は 5GHz 帯に接続できません |
| ホスト PC | Windows 10/11（推奨）、Linux、macOS（Apple Silicon） | 下記の性能メモを参照 |

**ホスト PC の性能メモ**

- **NVIDIA の GPU を強く推奨**します。GPU があれば返事の音声は 10〜20 秒ほどで作れます。
  GPU が無くても動きますが、1 回の返事に数十秒〜数分かかります。
- AI モデルのダウンロードで数 GB〜十数 GB の空き容量を使います。
- この文書のコマンドは **Windows の PowerShell** を基本にしています。macOS / Linux でもほぼ同じですが、
  フォルダの区切り `\` は `/` に読み替えてください（例 `cd ..\third_party` → `cd ../third_party`）。

> **WSL2 でホストを動かす場合の注意**: WSL2 の既定の設定（NAT）では、同じ Wi-Fi にいる AtomS3 から
> WSL2 内のサーバーに届きません。はじめての方は **Windows 上で直接ホストを動かす**ことをおすすめします。

---

## 2. ソフトをインストールする

以下の 4 つをホスト PC に入れます。Windows では **PowerShell** を開いて（スタートメニューで「PowerShell」と検索）、
1 行ずつ貼り付けて Enter を押します。

| ソフト | 用途 | Windows（PowerShell） | macOS / Linux |
|---|---|---|---|
| Git | ソースコードの取得 | `winget install --id Git.Git -e` | macOS: `xcode-select --install` / Linux: `sudo apt install git` |
| uv | Python と部品の管理 | `winget install --id astral-sh.uv -e` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Ollama | LLM（返事の文章を考える AI）を動かす | `winget install --id Ollama.Ollama -e` | https://ollama.com/download から入手 |
| VS Code | ファームウェアの書き込み | `winget install --id Microsoft.VisualStudioCode -e` | https://code.visualstudio.com/ から入手 |

インストールが終わったら **PowerShell を一度閉じて開き直して**ください（新しいコマンドが使えるようになります）。
確認のため、次のコマンドでそれぞれバージョンが表示されれば OK です。

```powershell
git --version
uv --version
ollama --version
```

> `winget` が使えない場合は、各ソフトの公式サイトからインストーラーを入手してください
> （uv: https://docs.astral.sh/uv/getting-started/installation/ ）。

---

## 3. ホスト PC を準備する

### 3-1. ソースコードを取得する

好きな場所（例: ドキュメントフォルダ）で次を実行します。`--recurse-submodules` を付けると、
音声合成（Irodori-TTS）と音声認識（Moonshine）の部品（サブモジュール）も一緒に取得できます。

```powershell
cd ~\Documents
git clone --recurse-submodules https://github.com/kou1-310/ai-voice-atom.git
cd ai-voice-atom
```

> `--recurse-submodules` を付け忘れた場合は、`ai-voice-atom` フォルダで
> `git submodule update --init --recursive` を実行すれば同じ状態になります。

以降、**「リポジトリ直下」**はこの `ai-voice-atom` フォルダのことを指します。

### 3-2. ホストの Python 環境を作る

```powershell
cd host
uv sync --extra dev
```

必要な Python（3.11 以上）とライブラリを uv が自動で用意します。数分かかることがあります。

### 3-3. 音声合成（Irodori-TTS）の環境を作る

Irodori-TTS はホストとは別の Python 環境で動きます。`host` フォルダから、Irodori-TTS のフォルダへ移動して実行します。

```powershell
cd ..\third_party\irodori-tts
uv sync --extra cu128      # NVIDIA GPU がある場合
# uv sync --extra cpu      # GPU が無い場合はこちら（上の行の代わりに実行）
```

> AMD GPU は `--extra rocm`、Intel GPU は `--extra xpu` です。macOS は `--extra cpu` を使います。

続けて、**音声合成モデルのダウンロード**を兼ねた試し合成をしておきます。初回はモデル（数 GB）の
ダウンロードで数分〜十数分かかります。ここで一度済ませておくと、サーバーの初回応答がタイムアウトしにくくなります。

```powershell
uv run --no-sync python infer.py --text "テストです" --output-wav outputs/warmup.wav --hf-checkpoint Aratako/Irodori-TTS-500M-v3 --no-ref
```

最後に `outputs/warmup.wav` ができていれば成功です（再生すると「テストです」と聞こえます）。

### 3-4. 音声認識（Moonshine）の部品を取得する

音声認識には、OS ごとのプログラム部品（ネイティブライブラリ）が必要です。サブモジュールには
含まれていないので、専用のスクリプトで取得します。**`host` フォルダ**で実行します。

```powershell
cd ..\..\host
uv run python scripts/fetch_moonshine_native.py
```

`配置しました` と表示されれば成功です（60〜90MB のダウンロードがあります）。
音声認識のモデル本体は、初めて使うときに自動でダウンロードされます。

### 3-5. LLM（返事を考える AI）を用意する

Ollama でモデルをダウンロードします。モデルは好みで選べます。

```powershell
ollama pull gemma4          # 例: このプロジェクトで使っているモデル
# ollama pull qwen3:1.7b    # 例: PC の性能が低いときの軽いモデル
```

Ollama は通常、インストール後に自動で起動しています（Windows ではタスクトレイにラマのアイコン）。
起動していない場合は `ollama serve` を実行するか、スタートメニューから Ollama を起動してください。

### 3-6. 設定ファイル（.env）を作る

`host` フォルダで、見本の設定ファイルをコピーします。

```powershell
cp .env.example .env
notepad .env                # macOS/Linux はお好みのエディタで開く
```

最低限、次の 3 行を確認・編集して保存します。

| 項目 | 設定する値 | 説明 |
|---|---|---|
| `AI_VOICE_ATOM_LLM_BASE_URL` | `http://localhost:11434/v1` | Ollama を同じ PC で動かすならこのまま |
| `AI_VOICE_ATOM_LLM_API_KEY` | `dummy` | Ollama では何でもよいが**空にしない**（空だと LLM が「未設定」扱いになる） |
| `AI_VOICE_ATOM_LLM_MODEL` | `gemma4` など | 3-5 でダウンロードしたモデル名 |

ほかの項目は、まずは見本のままで動きます（各項目の意味は `.env.example` のコメントを参照）。

#### （任意）声とキャラクターを設定する

- **声色**: 真似したい声の wav ファイル（数秒〜30 秒、1 人の声）を `host/voices/reference.wav` として置くと、
  その声で話します。置かなければ既定の声です。**他人の声や著作物の音声を使う場合は、その利用規約を必ず確認してください。**
- **キャラクター（ペルソナ）**: デバイスごとに性格・口調・声を変えられます。

  ```powershell
  cp device_profiles.example.json device_profiles.json
  notepad device_profiles.json
  ```

  `"atoms3-001"` などのキーがデバイスの ID です（[4-1](#4-1-デバイスの設定ファイルを作る) の `DEVICE_ID` と合わせます）。
  各項目の意味は [host/README.md の「ペルソナ定義」](../host/README.md#ペルソナ定義device_profilesjson) にまとまっています。
  見本に書かれた `voices/genki.wav` などは同梱していないので、自分で用意するか、その行を消して
  `voice_caption`（「落ち着いた女性の声で」のような文章で声を指定する項目）を使ってください。

### 3-7. 準備状況を点検する

`host` フォルダで点検スクリプトを実行します。

```powershell
uv run python scripts/check_setup.py
```

出力例:

```
[OK] Python 3.11 以上 — 3.12.3
[OK] uv コマンド — C:\Users\you\.local\bin\uv.exe
[OK] サブモジュール（irodori-tts / moonshine）
[OK] Irodori-TTS の Python 環境 — ...\third_party\irodori-tts\.venv
[OK] Moonshine（STT）のネイティブライブラリ — moonshine.dll
[OK] host/.env — ...\host\.env
[OK] LLM サーバー（OpenAI 互換） — http://localhost:11434/v1（モデル gemma4）
[注意] ペルソナ定義（device_profiles.json） — ありません（全デバイスが共通の設定・既定の声で動きます）
[注意] ファームウェアの設定（firmware/atom/.env） — ありません（実機に書き込む前に必要）

必須の準備はそろっています。uv run uvicorn app.main:app --host 0.0.0.0 で起動できます。
```

`[NG]` の行があれば、その下の `→` に書かれた手順で直してから、もう一度実行してください。
`[注意]` は必須ではありません（ファームウェアの設定は 4 章で作ります）。

### 3-8. サーバーを起動する

`host` フォルダで次を実行します。**このウィンドウは閉じずに置いたまま**にします（閉じるとサーバーが止まります）。

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`Uvicorn running on http://0.0.0.0:8000` と表示されれば起動しています。

> **Windows でファイアウォールの確認画面が出たら「プライベート ネットワーク」を許可**してください。
> 許可しないと AtomS3 からサーバーに届きません。後から許可する場合は、管理者の PowerShell で:
>
> ```powershell
> New-NetFirewallRule -DisplayName "ai-voice-atom 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
> ```

#### 動作を確かめる

1. ブラウザで http://localhost:8000/api/status を開き、`llm`・`tts`・`stt` がすべて `"configured"` なら OK。
2. ブラウザで http://localhost:8000/frontend/ を開き、実行モード「画面でデモ」で「デモを再生する」を押します。
   2 体の AI が交互に話し、PC のスピーカーから声が出れば、ホストの準備は完了です。

### 3-9. ホスト PC の IP アドレスを調べる

AtomS3 にホストの場所を教えるため、PC の IP アドレスを調べます。

- Windows: PowerShell で `ipconfig` → Wi-Fi（または イーサネット）の **IPv4 アドレス**（例 `192.168.1.10`）
- macOS: `ipconfig getifaddr en0`　/　Linux: `hostname -I`

> PC の IP アドレスは再起動などで変わることがあります。変わると AtomS3 がつながらなくなるので、
> ルーターの設定で PC の IP を固定（DHCP 予約）しておくと安心です。

---

## 4. AtomS3 にファームウェアを書き込む

### 4-1. デバイスの設定ファイルを作る

`firmware/atom` フォルダで、見本をコピーして編集します。

```powershell
cd ..\firmware\atom          # host フォルダから移動する場合
cp .env.template .env
notepad .env
```

| 項目 | 設定する値 |
|---|---|
| `WIFI_SSID` | Wi-Fi の名前（**2.4GHz 帯**のもの） |
| `WIFI_PASSWORD` | Wi-Fi のパスワード |
| `HOST_BASE_URL` | `http://<3-9 で調べた IP>:8000`（例 `http://192.168.1.10:8000`） |
| `DEVICE_ID` | `atoms3-001`（複数台なら機体ごとに変える。[6 章](#6-2-台目以降を追加する)） |
| `SPEAKER_VOLUME` | 音量 0〜255（既定 100） |

> この `.env` は Git に含まれません（パスワードが入るため）。

### 4-2. （任意）モード切替の音声案内を作る

長押しでモードを切り替えたときに「チャットモードですわ。」のように声で案内させられます。
作らない場合は「ピッ」というチャイム音で知らせます（動作に支障はありません）。

1. 案内に使いたい声の wav を用意します（既定の置き場所は `host/voices/shikoku_metan-normal.wav`）。
2. `host` フォルダで次を実行します（5 文を合成するので 5〜10 分かかります）。

   ```powershell
   uv run python scripts/generate_mode_prompts.py
   # 別の声を使う場合: uv run python scripts/generate_mode_prompts.py --ref-wav voices/my_voice.wav
   ```

3. `firmware/atom/include/mode_prompts.h` ができれば成功です。この後の書き込みで自動的に組み込まれます。

> 四国めたん（VOICEVOX）の声を使う場合は、クレジット「VOICEVOX:四国めたん」を表記してください。
> 生成した音声は Git に含まれない設定になっています。

### 4-3. VS Code と PlatformIO で書き込む

1. VS Code を起動し、左の「拡張機能」（四角が 4 つのアイコン）で **PlatformIO IDE** を検索してインストールします。
   インストール後、VS Code の再起動を求められたら再起動します。
2. VS Code の「ファイル」→「フォルダーを開く」で **`firmware/atom` フォルダ**を開きます
   （リポジトリ直下ではなく、`platformio.ini` があるこのフォルダを開くのがポイントです）。
3. AtomS3-Lite を Atomic Echo Base に差し込み、USB ケーブルで PC につなぎます。
4. 画面左の PlatformIO（アリの顔のアイコン）→ **`m5stack-atoms3`** → **Upload** をクリックします。
   - 初回はビルド用の道具のダウンロードで 5〜10 分かかります。
   - 下のターミナルに `SUCCESS` と出れば書き込み完了です。
5. 同じメニューの **Monitor** を押すと、AtomS3 の動作ログ（シリアルモニター）が見られます。
   **次に書き込むときは、先にモニターを閉じてください**（開いたままだと書き込めません）。

> コマンドで書き込みたい場合: `uv tool install platformio` で PlatformIO を入れ、`firmware/atom` で
> `pio run --target upload`（ログは `pio device monitor`）。WSL2 から書き込む場合は
> [firmware/atom/UPLOAD_GUIDE.md](../firmware/atom/UPLOAD_GUIDE.md) を参照してください。

#### 書き込めないとき

- **充電専用ケーブル**を使っていないか確認します（別のケーブルで試す）。
- シリアルモニターが開いていたら閉じます。
- AtomS3 を**書き込みモード**にしてから Upload します: 側面のリセットボタンを約 2 秒長押しし、
  本体内部の緑色の LED が点いたら離します。書き込み後はリセットボタンを 1 回押して再起動します。

### 4-4. 起動を確認する

書き込みが終わると AtomS3 が再起動し、LED の色で状態を知らせます。

| LED | 状態 | 対処 |
|---|---|---|
| オレンジ | Wi-Fi に接続中 | しばらく待つ。ずっとオレンジなら `WIFI_SSID`・パスワード・2.4GHz 帯かを確認 |
| 赤 | Wi-Fi にはつながったがホストに届かない | サーバーの起動、`HOST_BASE_URL` の IP、ファイアウォール（3-8）を確認 |
| 青 | 準備完了（チャットモード） | 5 章へ |
| 暗い紫 | `WIFI_SSID` が空のまま書き込まれている | 4-1 の `.env` を確認して書き込み直す |

---

## 5. 動かしてみる

> ここでは最初の動作確認に必要なことだけを説明します。各モードの手順や LED・音の意味など、
> 詳しい使い方は [manual.md（操作マニュアル）](manual.md) にまとめています。

### 5-1. ボタンの基本操作

AtomS3-Lite の上面全体が 1 つのボタン（BtnA）になっています。

- **クリック（短く押す）**: モードごとの操作（下の表）
- **ダブルクリック（クリックを 2 回続ける）**: 録音再生モードの再生、タイマーの時間切替
- **長押し（1.2 秒以上）**: 次のモードへ切り替え。切替時に音声案内（またはチャイム）が流れます。
  最後に選んだモードは記憶され、次に電源を入れたときもそのモードで始まります。

| モード（LED） | クリック | ダブルクリック | ホスト |
|---|---|---|---|
| **チャット（web・青）** | 「こんにちは」と話しかけ、AI の返事を再生。再生中なら停止 | － | 必要 |
| **音声会話（voice・緑）** | 1 回目で録音開始（緑が点滅）→ 話す → 2 回目で終了。AI が返事をする | － | 必要 |
| **リレー会話（relay・シアン）** | 再生中なら停止（会話はブラウザから操作） | － | 必要 |
| **録音再生（recorder・白）** | 録音開始（赤が点滅）→ もう一度押して停止 | 最後の録音を再生 | **不要** |
| **オウム返し（parrot・黄）** | 声の種類を切り替え（高い声 → ふつう → 低い声） | － | **不要** |
| **タイマー（timer・紫）** | スタート／動作中は中止／鳴っていれば止める | 時間を切り替え（1・3・5・10・25 分） | **不要** |

- 返事の音声は、LLM と音声合成の処理で 10〜30 秒ほどかかります（GPU の有無や文の長さで変わります）。
- 録音再生モードの録音は最大 30 秒で、電源を切っても残ります（新しく録音すると上書き）。
  クリックから約 0.5 秒後の「ピッ」で録音が始まります。ダブルクリックは 1 回目を離してから 0.5 秒以内に 2 回目を押します。
- オウム返しモードはボタン不要です。話しかけると自動で録音し（黄色が点滅）、黙ると言い返します。
  勝手に反応する／反応しないときは、`firmware/atom/.env` の `PARROT_MIN_RMS` を上げ下げして書き込み直します。
- タイマーは残り時間を LED の色（緑 → 黄 → 赤）で表し、残り 1 分と時間切れを声で知らせます。
- 録音再生・オウム返し・タイマーは、Wi-Fi やホストが無い場所でも単体で遊べます。

### 5-2. ブラウザから操作する

サーバーを起動した PC、または同じ Wi-Fi のスマホ・PC のブラウザで `http://<ホストの IP>:8000/frontend/` を開きます。
実機を使う操作では、**AtomS3 を長押しで relay モード（LED シアン）にしておきます**。
画面に「接続中の実機: atoms3-001」のように表示されれば準備完了です。

| 実行モード | できること | 必要な実機 |
|---|---|---|
| 画面でデモ | 2 体の AI の会話をブラウザで再生 | なし |
| 実機で会話 | 2 体の AI の会話を実機で再生。「スピーカー（実機）」で **2 台に分けるか、1 台で両方の声を出すか**を選ぶ | 1 台 または 2 台 |
| 実機に話しかける | 入力した言葉に選んだキャラクターが答え、実機がその声で話す | 1 台 |

---

## 6. 2 台目以降を追加する

複数の AtomS3 を、別々のキャラクター（性格・声）として会話させられます。

1. 2 台目用に `firmware/atom/.env` の `DEVICE_ID` を `atoms3-002` に変え、2 台目をつないで書き込みます
   （1 台目を書き込み直す必要はありません）。
2. `host/device_profiles.json` に `atoms3-002` のキャラクターを書きます（3-6 参照）。サーバーの再起動が必要です。
3. 2 台とも relay モード（シアン）にし、ブラウザの「実機で会話」→ スピーカー「各ペルソナの実機（2台）」で会話を始めます。

---

## 7. 困ったとき

まず `host` フォルダで `uv run python scripts/check_setup.py` を実行し、`[NG]` が無いか確認してください。

| 症状 | 原因と対処 |
|---|---|
| `/api/status` で `llm` が `not-configured` | `host/.env` の `AI_VOICE_ATOM_LLM_BASE_URL` と `AI_VOICE_ATOM_LLM_API_KEY`（空にしない）を確認し、サーバーを再起動 |
| 返事が来ず、エラー音（低い音 3 回）が鳴る | サーバーのウィンドウのログを見る。LLM（Ollama）が起動しているか、モデル名が正しいか確認 |
| 初回だけ音声合成が失敗する（503） | 音声合成モデルのダウンロードに時間がかかっている。3-3 の試し合成を済ませてから、もう一度試す |
| 音声会話モードで認識されない | `check_setup.py` で Moonshine のネイティブライブラリが OK か確認（3-4）。マイクに近づいてはっきり話す |
| `Failed to load Moonshine library` がログに出る | 3-4 を実行してから、**サーバーを再起動**（再起動しないと直らない） |
| LED がずっと赤 | サーバー未起動／`HOST_BASE_URL` の IP 誤り／ファイアウォールでポート 8000 が閉じている／PC の IP が変わった |
| ブラウザの「実機」に何も表示されない | 実機が relay モード（シアン）になっているか、LED が赤でないかを確認 |
| 声がキャラクターごとに変わらない | `device_profiles.json` の `ref_wav` のファイルが実在するか確認（無いと既定の声になる）。変更後はサーバーを再起動 |
| 全体が重い・時間がかかる | [docs/performance-tuning.md](performance-tuning.md) を参照（GPU メモリの節約設定など） |

より細かい確認手順は [docs/verification-guide.md](verification-guide.md) にあります。

---

## 8. 最新版に更新するとき

リポジトリ直下で次を実行します。

```powershell
git pull
git submodule update --init --recursive
cd host
uv sync --extra dev
uv run python scripts/fetch_moonshine_native.py --force   # Moonshine の版が変わったとき
cd ..\third_party\irodori-tts
uv sync --extra cu128                                      # 3-3 と同じ extra を指定
```

ファームウェアが変わっていたら、4-3 の手順で AtomS3 に書き込み直します。
