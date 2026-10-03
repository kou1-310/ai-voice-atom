"""セットアップ補助スクリプト（check_setup / fetch_moonshine_native）の純粋な判定ロジックの検証。

ネットワーク・実ファイルのダウンロードは行わない。
"""

import json
from pathlib import Path

import pytest

from scripts import check_setup, fetch_moonshine_native


def test_platform_key_and_select_wheel() -> None:
    files = [
        {"filename": "moonshine_voice-0.0.62-py3-none-macosx_15_0_arm64.whl"},
        {"filename": "moonshine_voice-0.0.62-py3-none-manylinux_2_34_aarch64.whl"},
        {"filename": "moonshine_voice-0.0.62-py3-none-manylinux_2_34_x86_64.whl"},
        {"filename": "moonshine_voice-0.0.62-py3-none-win_amd64.whl"},
    ]
    pick = fetch_moonshine_native.select_wheel

    assert (
        "win_amd64"
        in pick(files, fetch_moonshine_native.platform_key("Windows", "AMD64"))["filename"]
    )
    assert (
        "x86_64" in pick(files, fetch_moonshine_native.platform_key("Linux", "x86_64"))["filename"]
    )
    assert (
        "aarch64"
        in pick(files, fetch_moonshine_native.platform_key("Linux", "aarch64"))["filename"]
    )
    assert (
        "macosx" in pick(files, fetch_moonshine_native.platform_key("Darwin", "arm64"))["filename"]
    )
    with pytest.raises(RuntimeError):
        fetch_moonshine_native.platform_key("Darwin", "x86_64")


def test_is_native_member_filters_by_os() -> None:
    native = fetch_moonshine_native.is_native_member

    # Windows: dll のみ。他 OS 用・サブディレクトリ・Python ファイルは取らない。
    assert native("moonshine_voice/moonshine.dll", "Windows")
    assert native("moonshine_voice/onnxruntime.dll", "Windows")
    assert not native("moonshine_voice/libmoonshine.dylib", "Windows")
    assert not native("moonshine_voice/assets/x.dll", "Windows")
    assert not native("moonshine_voice/__init__.py", "Windows")
    # Linux: .so と、依存ライブラリを入れた moonshine_voice.libs/ の中身。
    assert native("moonshine_voice/libmoonshine.so", "Linux")
    assert native("moonshine_voice.libs/libonnxruntime-13ab8084.so.1", "Linux")
    assert not native("moonshine_voice/libonnxruntime.1.23.2.dylib", "Linux")
    assert not native("moonshine_voice.libs/", "Linux")
    # macOS: dylib のみ。
    assert native("moonshine_voice/libmoonshine.dylib", "Darwin")
    assert not native("moonshine_voice/libmoonshine.so", "Darwin")


def test_check_moonshine_native_reports_missing_and_present(tmp_path: Path) -> None:
    result = check_setup.check_moonshine_native(tmp_path, system="Windows")
    assert result.status == check_setup.NG
    assert "fetch_moonshine_native" in result.fix

    lib_dir = tmp_path / "third_party" / "moonshine" / "python" / "src" / "moonshine_voice"
    lib_dir.mkdir(parents=True)
    (lib_dir / "moonshine.dll").write_bytes(b"x")
    assert check_setup.check_moonshine_native(tmp_path, system="Windows").status == check_setup.OK


def test_check_llm_requires_url_and_key() -> None:
    assert check_setup.check_llm(None, True, "m").status == check_setup.NG
    no_key = check_setup.check_llm("http://localhost:11434/v1", False, "m")
    assert no_key.status == check_setup.NG
    assert "API_KEY" in no_key.detail


def test_check_llm_matches_model_with_latest_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._body = json.dumps(payload).encode()

        def read(self, *args: object) -> bytes:
            return self._body

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    payload = {"data": [{"id": "gemma4:latest"}, {"id": "qwen3:1.7b"}]}
    monkeypatch.setattr(check_setup.urllib.request, "urlopen", lambda *a, **k: _Resp(payload))

    # tag 省略のモデル名は :latest と照合する
    assert check_setup.check_llm("http://x/v1", True, "gemma4").status == check_setup.OK
    assert check_setup.check_llm("http://x/v1", True, "qwen3:1.7b").status == check_setup.OK
    missing = check_setup.check_llm("http://x/v1", True, "llama9")
    assert missing.status == check_setup.NG
    assert "ollama pull llama9" in missing.fix


def test_check_firmware_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    assert check_setup.check_firmware_env(env).status == check_setup.WARN

    env.write_text('# コメント\nWIFI_SSID="my-wifi"\nHOST_BASE_URL=\n', encoding="utf-8")
    result = check_setup.check_firmware_env(env)
    assert result.status == check_setup.WARN
    assert "HOST_BASE_URL" in result.detail

    env.write_text("WIFI_SSID=my-wifi\nHOST_BASE_URL=http://pc.local:8000\n", encoding="utf-8")
    assert check_setup.check_firmware_env(env).status == check_setup.OK


def test_check_profiles_warns_missing_ref_wav(tmp_path: Path) -> None:
    profiles = tmp_path / "device_profiles.json"
    assert check_setup.check_profiles(profiles)[0].status == check_setup.WARN

    profiles.write_text(
        json.dumps({"atoms3-001": {"display_name": "A", "ref_wav": "voices/__missing__.wav"}}),
        encoding="utf-8",
    )
    checks = check_setup.check_profiles(profiles)
    assert checks[0].status == check_setup.OK
    assert any(c.status == check_setup.WARN and "atoms3-001" in c.title for c in checks[1:])
