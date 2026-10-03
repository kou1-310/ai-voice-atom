# AtomS3 ファームウェアアップロード手順（WSL2環境）

WSL2からPlatformIOでAtomS3にアップロードするための手順です。
Windows 上の VS Code（PlatformIO IDE）から書き込む場合、この手順は不要です
（[docs/setup-guide.md](../../docs/setup-guide.md) の 4 章を参照）。

---

## 前提条件

- Windows 10/11
- WSL2（Ubuntu）インストール済み
- AtomS3をUSBケーブルでPCに接続済み

---

## 手順1: udevルールのインストール（WSL2 — 初回のみ）

WSL2ターミナルで実行します。

```bash
curl -fsSL https://raw.githubusercontent.com/platformio/platformio-core/develop/platformio/assets/system/99-platformio-udev.rules \
  | sudo tee /etc/udev/rules.d/99-platformio-udev.rules

sudo udevadm control --reload-rules && sudo udevadm trigger
```

インストール確認：

```bash
ls /etc/udev/rules.d/99-platformio-udev.rules
```

---

## 手順2: usbipd-win のインストール（Windows — 初回のみ）

**Windows PowerShell（管理者）** で実行します。

```powershell
winget install usbipd
```

インストール後、PowerShellを再起動してください。

---

## 手順3: AtomS3をWSL2にアタッチ

AtomS3をUSB接続した状態で、**Windows PowerShell（管理者）** で実行します。

### 3-1. デバイス一覧を確認

```powershell
usbipd list
```

出力例：
```
BUSID  VID:PID    DEVICE                                                        STATE
2-1    303a:1001  USB シリアル デバイス (COM5)                                    Not shared
3-2    046d:c548   ...
```

AtomS3は `303a:1001`（ESP32S3ネイティブUSB）として表示されることが多いです。

### 3-2. WSL2にアタッチ

BUSIDを確認してから実行します（下記の `2-1` は実際の値に置き換えてください）。

```powershell
usbipd attach --wsl --busid 2-1
```

> **注意:** WSLを再起動するたびに、このコマンドを再実行する必要があります。

---

## 手順4: WSL2でポートを確認

WSL2ターミナルで実行します。

```bash
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

出力例：
```
/dev/ttyACM0
```

AtomS3（ESP32S3ネイティブUSB）は `/dev/ttyACM0` として認識されるのが一般的です。

---

## 手順5: platformio.ini にポートを設定（自動検出できないときのみ）

通常は PlatformIO がポートを自動検出します。複数のシリアル機器をつないでいて誤検出する場合だけ、
`platformio.ini` の `[env:m5stack-atoms3]` セクションで `upload_port` を指定します
（リポジトリにはコメントアウトした `#upload_port = /dev/ttyACM0` があります）。

```ini
[env:m5stack-atoms3]
platform = espressif32
board = esp32-s3-devkitc-1
framework = arduino
monitor_speed = 115200
upload_speed = 1500000
upload_port = /dev/ttyACM0   ← 追記
```

---

## 手順6: アップロード

```bash
~/.platformio/penv/bin/pio run --target upload
```

シリアルモニターで動作確認する場合：

```bash
~/.platformio/penv/bin/pio device monitor
```

---

## トラブルシューティング

### デバイスが /dev/ttyACM* に見えない

- `usbipd attach` が完了しているか確認してください
- AtomS3をUSBケーブルで繋ぎ直してから手順3をやり直してください

### Permission denied エラー

udevルール適用後もPermission deniedが出る場合は、ユーザーをdialoutグループに追加します。

```bash
sudo usermod -aG dialout $USER
```

ログアウト→ログインし直してから再試行してください。

### usbipd attach に失敗する

管理者権限のPowerShellで実行しているか確認してください。また、usbipd-winのバージョンが古い場合はアップデートします。

```powershell
winget upgrade usbipd
```

### AtomS3がブートローダーモードにならない

アップロードがタイムアウトする場合、AtomS3-Lite を書き込み（ダウンロード）モードに手動で切り替えてください。
1. 側面のリセットボタンを約 2 秒長押しする
2. 本体内部の緑色の LED が点いたら離す（書き込みモード）
3. Upload を実行する。書き込み後はリセットボタンを 1 回押して再起動する

書き込みモードでは USB の認識が変わるため、`usbipd attach` をやり直す必要がある場合があります。
