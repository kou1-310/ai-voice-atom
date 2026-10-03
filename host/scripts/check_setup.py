"""ホストの準備状況をまとめて点検し、足りないものと直し方を表示する。

初めてセットアップする人が「どこまで終わったか」「次に何をすればよいか」を確かめるための
スクリプト。標準ライブラリだけで動くので、``uv sync`` の前でも実行できる。

使い方（host/ から）::

    python scripts/check_setup.py        # uv sync 前でも可（python は 3.11 以上）
    uv run python scripts/check_setup.py

[NG] が 1 つでもあれば終了コード 1。[注意] は動作に必須ではない項目。
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = HOST_DIR.parent
# uv sync 前でも app パッケージ（標準ライブラリのみで書かれた設定読み込み）を使えるようにする。
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))

OK, NG, WARN = "OK", "NG", "注意"


@dataclass
class Check:
    status: str
    title: str
    detail: str = ""
    fix: str = ""


def check_python() -> Check:
    version = ".".join(map(str, sys.version_info[:3]))
    # 古い Python で直接実行した人に知らせるための分岐（pyproject の下限とは別に必要）
    if sys.version_info >= (3, 11):  # noqa: UP036
        return Check(OK, "Python 3.11 以上", version)
    return Check(NG, "Python 3.11 以上", version, "uv が自動で用意します: uv python install 3.12")


def check_uv() -> Check:
    path = shutil.which("uv")
    if path:
        return Check(OK, "uv コマンド", path)
    return Check(
        NG,
        "uv コマンド",
        "見つかりません（TTS の起動にも使います）",
        "https://docs.astral.sh/uv/ の手順でインストールし、ターミナルを開き直してください",
    )


def check_submodules(repo_root: Path) -> Check:
    missing = [
        name
        for name, marker in (
            ("irodori-tts", repo_root / "third_party" / "irodori-tts" / "infer.py"),
            ("moonshine", repo_root / "third_party" / "moonshine" / "python" / "pyproject.toml"),
        )
        if not marker.exists()
    ]
    if not missing:
        return Check(OK, "サブモジュール（irodori-tts / moonshine）")
    return Check(
        NG,
        "サブモジュール（irodori-tts / moonshine）",
        f"未取得: {', '.join(missing)}",
        "リポジトリ直下で: git submodule update --init --recursive",
    )


def check_irodori_env(repo_root: Path) -> Check:
    venv = repo_root / "third_party" / "irodori-tts" / ".venv"
    if venv.is_dir():
        return Check(OK, "Irodori-TTS の Python 環境", str(venv))
    return Check(
        NG,
        "Irodori-TTS の Python 環境",
        "third_party/irodori-tts/.venv がありません",
        "cd third_party/irodori-tts && uv sync --extra cu128（NVIDIA GPU）/ --extra cpu（GPU なし）",
    )


def check_moonshine_native(repo_root: Path, system: str | None = None) -> Check:
    system = system or platform.system()
    name = {"Windows": "moonshine.dll", "Linux": "libmoonshine.so", "Darwin": "libmoonshine.dylib"}
    lib = repo_root / "third_party" / "moonshine" / "python" / "src" / "moonshine_voice"
    lib = lib / name.get(system, "libmoonshine.so")
    if lib.is_file():
        return Check(OK, "Moonshine（STT）のネイティブライブラリ", lib.name)
    return Check(
        NG,
        "Moonshine（STT）のネイティブライブラリ",
        f"{lib.name} がありません（音声認識が動きません）",
        "host/ で: uv run python scripts/fetch_moonshine_native.py",
    )


def check_env_file(path: Path) -> Check:
    if path.is_file():
        return Check(OK, "host/.env", str(path))
    return Check(
        WARN,
        "host/.env",
        "ありません（すべて既定値で動きます。LLM の設定は必要です）",
        "host/ で: cp .env.example .env して LLM の項目を編集",
    )


def parse_model_ids(payload: dict) -> list[str]:
    """OpenAI 互換 /models 応答から model id の一覧を取り出す。"""
    return [str(m.get("id", "")) for m in payload.get("data", []) if isinstance(m, dict)]


def check_llm(
    base_url: str | None, api_key_present: bool, model: str, timeout: float = 5.0
) -> Check:
    title = "LLM サーバー（OpenAI 互換）"
    if not base_url:
        return Check(
            NG, title, "AI_VOICE_ATOM_LLM_BASE_URL が未設定", "host/.env に設定してください"
        )
    if not api_key_present:
        return Check(
            NG,
            title,
            "AI_VOICE_ATOM_LLM_API_KEY が未設定（Ollama でも何か文字列が必要）",
            "host/.env に AI_VOICE_ATOM_LLM_API_KEY=dummy のように設定してください",
        )
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            ids = parse_model_ids(json.load(response))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Check(
            NG,
            title,
            f"{url} に接続できません（{exc}）",
            "Ollama 等の LLM サーバーを起動し、AI_VOICE_ATOM_LLM_BASE_URL を確認してください",
        )
    # Ollama は "gemma3:4b" のように tag 付きで返すので、tag 省略時は ":latest" とも照合する。
    if model in ids or f"{model}:latest" in ids:
        return Check(OK, title, f"{base_url}（モデル {model}）")
    return Check(
        NG,
        title,
        f"モデル {model} がありません（ある: {', '.join(ids[:5]) or 'なし'}）",
        f"ollama pull {model} を実行するか、AI_VOICE_ATOM_LLM_MODEL を上の一覧の名前にしてください",
    )


def check_profiles(path: Path) -> list[Check]:
    from app.device_profiles import load_device_profiles

    title = "ペルソナ定義（device_profiles.json）"
    if not path.is_file():
        return [
            Check(
                WARN,
                title,
                "ありません（全デバイスが共通の設定・既定の声で動きます）",
                "host/ で: cp device_profiles.example.json device_profiles.json",
            )
        ]
    profiles = load_device_profiles(path)
    if not profiles:
        return [Check(NG, title, "読み込めませんでした（JSON の書式を確認してください）")]
    checks = [Check(OK, title, f"{len(profiles)} 件: {', '.join(profiles)}")]
    for device_id, profile in profiles.items():
        if profile.ref_wav is not None and not profile.ref_wav.exists():
            checks.append(
                Check(
                    WARN,
                    f"声色ファイル（{device_id}）",
                    f"{profile.ref_wav} がありません（既定の声で話します）",
                    "wav を置くか、ref_wav を消して voice_caption で声を指定してください",
                )
            )
    return checks


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE 形式の .env を読む（# コメント・空行は無視、値の前後の引用符は外す）。"""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check_firmware_env(path: Path) -> Check:
    title = "ファームウェアの設定（firmware/atom/.env）"
    if not path.is_file():
        return Check(
            WARN,
            title,
            "ありません（実機に書き込む前に必要）",
            "firmware/atom で: cp .env.template .env して Wi-Fi とホストの URL を記入",
        )
    values = read_env_file(path)
    missing = [k for k in ("WIFI_SSID", "HOST_BASE_URL") if not values.get(k)]
    if missing:
        return Check(WARN, title, f"未記入: {', '.join(missing)}", "firmware/atom/.env を編集")
    return Check(OK, title, f"SSID={values['WIFI_SSID']} / HOST={values['HOST_BASE_URL']}")


def run_checks() -> list[Check]:
    checks = [
        check_python(),
        check_uv(),
        check_submodules(REPO_ROOT),
        check_irodori_env(REPO_ROOT),
        check_moonshine_native(REPO_ROOT),
        check_env_file(HOST_DIR / ".env"),
    ]
    # app.config は import 時に host/.env を読み込む（既存の環境変数は上書きしない）。
    from app.config import get_settings

    settings = get_settings()
    checks.append(
        check_llm(settings.llm_base_url, settings.llm_api_key_present, settings.llm_model)
    )
    checks.extend(check_profiles(settings.device_profiles_path))
    checks.append(check_firmware_env(REPO_ROOT / "firmware" / "atom" / ".env"))
    return checks


def main() -> int:
    checks = run_checks()
    for check in checks:
        line = f"[{check.status}] {check.title}"
        if check.detail:
            line += f" — {check.detail}"
        print(line)
        if check.fix and check.status != OK:
            print(f"      → {check.fix}")
    failed = sum(c.status == NG for c in checks)
    print()
    if failed:
        print(f"NG が {failed} 件あります。→ の手順で直してから、もう一度実行してください。")
        return 1
    print("必須の準備はそろっています。uv run uvicorn app.main:app --host 0.0.0.0 で起動できます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
