"""
PlatformIO pre-build script.
プロジェクトルートの .env ファイルを読み込み、ビルドマクロとして注入する。
.env が存在しない場合はフォールバック値を使用する。
"""
import os
Import("env")

# 文字列マクロとして注入するキー（"..." で囲んで定義）
KEYS = (
    "WIFI_SSID",
    "WIFI_PASSWORD",
    "WIFI_STATIC_IP",
    "WIFI_GATEWAY",
    "WIFI_SUBNET",
    "WIFI_DNS",
    "HOST_BASE_URL",
    "DEVICE_ID",
)

# 数値マクロとして注入するキー（囲まずそのまま定義）
NUMERIC_KEYS = (
    "SPEAKER_VOLUME",
    "PARROT_MIN_RMS",
)

DEFAULTS = {
    "WIFI_SSID":      "",
    "WIFI_PASSWORD":  "",
    # 固定IP設定（空の場合はファームウェア側で DHCP にフォールバックする）
    "WIFI_STATIC_IP": "",
    "WIFI_GATEWAY":   "",
    "WIFI_SUBNET":    "",
    "WIFI_DNS":       "",
    "HOST_BASE_URL":  "http://192.168.1.10:8000",
    # デバイス識別子。複数台運用時は機体ごとに変える（ホスト側でペルソナ・声色・履歴を出し分ける）。
    "DEVICE_ID":      "atoms3-001",
    # スピーカー音量 (0-255)。M5.Speaker.setVolume に渡す。
    "SPEAKER_VOLUME": "100",
    # オウム返しモードが声とみなす音量の下限。誤反応するなら上げ、反応しないなら下げる。
    "PARROT_MIN_RMS": "400",
}

values = dict(DEFAULTS)

env_path = os.path.join(env["PROJECT_DIR"], ".env")
if not os.path.exists(env_path):
    print("[load_env] .env not found — using built-in defaults")
else:
    print(f"[load_env] loading {env_path}")
    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in KEYS or key in NUMERIC_KEYS:
                values[key] = value

for key, value in values.items():
    if key in NUMERIC_KEYS:
        # 数値は囲まずそのまま注入。空なら DEFAULTS の値にフォールバック。
        numeric = value.strip() or DEFAULTS[key]
        env.Append(CPPDEFINES=[(key, numeric)])
        print(f"[load_env]   {key} = {numeric}")
    else:
        env.Append(CPPDEFINES=[(key, f'\\"{value}\\"')])
        display = "*" * len(value) if "PASSWORD" in key else value
        print(f"[load_env]   {key} = {display}")
