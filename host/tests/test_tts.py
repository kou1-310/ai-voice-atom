import base64
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.adapters.irodori_tts_adapter import (
    DEFAULT_CHECKPOINT,
    VOICE_DESIGN_CHECKPOINT,
    IrodoriTtsAdapter,
)
from app.main import app

client = TestClient(app)


def _write_test_wav(path: Path, sample_rate: int = 22050, channels: int = 1) -> bytes:
    frames = b"\x00\x00" * 32 * channels
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)
    return path.read_bytes()


def test_synthesize_runs_infer_with_default_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_path = tmp_path / "irodori"
    root_path.mkdir()
    (root_path / "infer.py").write_text("", encoding="utf-8")
    output_path = tmp_path / "generated.wav"
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        captured["command"] = command
        output_index = command.index("--output-wav") + 1
        _write_test_wav(Path(command[output_index]))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.adapters.irodori_tts_adapter.subprocess.run", fake_run)

    adapter = IrodoriTtsAdapter(root_path=root_path)
    result = adapter.synthesize(text="こんにちは", output_path=output_path)

    command = captured["command"]
    assert result == output_path
    assert command[:5] == ["uv", "run", "--no-sync", "python", "infer.py"]
    assert "--no-ref" in command
    assert command[command.index("--hf-checkpoint") + 1] == DEFAULT_CHECKPOINT


def test_synthesize_uses_voice_design_checkpoint_for_voice_caption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_path = tmp_path / "irodori"
    root_path.mkdir()
    (root_path / "infer.py").write_text("", encoding="utf-8")
    captured_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured_command[:] = command
        _write_test_wav(Path(command[command.index("--output-wav") + 1]))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.adapters.irodori_tts_adapter.subprocess.run", fake_run)

    adapter = IrodoriTtsAdapter(root_path=root_path)
    adapter.synthesize(text="こんにちは", output_path=tmp_path / "voice.wav", voice="落ち着いた声")

    assert (
        captured_command[captured_command.index("--hf-checkpoint") + 1] == VOICE_DESIGN_CHECKPOINT
    )
    assert captured_command[captured_command.index("--caption") + 1] == "落ち着いた声"


def test_synthesize_uses_default_ref_wav_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既定の置き場所に参照音声があれば --ref-wav が付き --no-ref は付かない。"""
    root_path = tmp_path / "irodori"
    root_path.mkdir()
    (root_path / "infer.py").write_text("", encoding="utf-8")
    ref_path = tmp_path / "reference.wav"
    _write_test_wav(ref_path)
    captured_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured_command[:] = command
        _write_test_wav(Path(command[command.index("--output-wav") + 1]))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.adapters.irodori_tts_adapter.subprocess.run", fake_run)

    adapter = IrodoriTtsAdapter(root_path=root_path, ref_wav=ref_path)
    adapter.synthesize(text="こんにちは", output_path=tmp_path / "out.wav")

    assert "--no-ref" not in captured_command
    assert captured_command[captured_command.index("--ref-wav") + 1] == str(ref_path.resolve())


def test_synthesize_falls_back_to_no_ref_when_default_ref_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既定の参照音声が無ければ（置いていなければ）従来どおり --no-ref で合成する。"""
    root_path = tmp_path / "irodori"
    root_path.mkdir()
    (root_path / "infer.py").write_text("", encoding="utf-8")
    captured_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured_command[:] = command
        _write_test_wav(Path(command[command.index("--output-wav") + 1]))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.adapters.irodori_tts_adapter.subprocess.run", fake_run)

    adapter = IrodoriTtsAdapter(root_path=root_path, ref_wav=tmp_path / "missing.wav")
    adapter.synthesize(text="こんにちは", output_path=tmp_path / "out.wav")

    assert "--no-ref" in captured_command
    assert "--ref-wav" not in captured_command


def test_synthesize_raises_when_explicit_ref_missing(tmp_path: Path) -> None:
    """明示指定した参照音声が存在しない場合は不正入力として ValueError。"""
    root_path = tmp_path / "irodori"
    root_path.mkdir()
    (root_path / "infer.py").write_text("", encoding="utf-8")

    adapter = IrodoriTtsAdapter(root_path=root_path)
    with pytest.raises(ValueError):
        adapter.synthesize(
            text="こんにちは",
            ref_wav=tmp_path / "nope.wav",
            output_path=tmp_path / "out.wav",
        )


def test_synthesize_via_server_sends_ref_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """サイドカー経路でも参照音声を絶対パスで渡す。"""
    root_path = tmp_path / "irodori"
    root_path.mkdir()
    (root_path / "infer.py").write_text("", encoding="utf-8")
    ref_path = tmp_path / "reference.wav"
    wav_bytes = _write_test_wav(ref_path)
    captured: dict[str, object] = {}

    adapter = IrodoriTtsAdapter(
        root_path=root_path,
        server_url="http://127.0.0.1:8770",
        ref_wav=ref_path,
    )

    def fake_ensure_ready() -> None:
        return None

    def fake_urlopen(req: object, timeout: float = 0.0):  # type: ignore[no-untyped-def]
        captured["body"] = json.loads(req.data.decode("utf-8"))

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return wav_bytes

        return _Resp()

    monkeypatch.setattr(adapter, "_ensure_server_ready", fake_ensure_ready)
    monkeypatch.setattr("app.adapters.irodori_tts_adapter.urllib.request.urlopen", fake_urlopen)

    adapter.synthesize(text="こんにちは", output_path=tmp_path / "out.wav")

    body = captured["body"]
    assert body["text"] == "こんにちは"
    assert body["ref_wav"] == str(ref_path.resolve())


def test_synthesize_via_server_sends_caption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """声色 caption もサイドカー経路で渡す（subprocess へ落とさない）。

    VoiceDesign の毎回再ロード（低速・タイムアウト）を避け、常駐サイドカーで合成するため。
    """
    root_path = tmp_path / "irodori"
    root_path.mkdir()
    (root_path / "infer.py").write_text("", encoding="utf-8")
    wav_bytes = _write_test_wav(tmp_path / "ref.wav")
    captured: dict[str, object] = {}

    adapter = IrodoriTtsAdapter(root_path=root_path, server_url="http://127.0.0.1:8770")

    def fake_urlopen(req: object, timeout: float = 0.0):  # type: ignore[no-untyped-def]
        captured["body"] = json.loads(req.data.decode("utf-8"))

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return wav_bytes

        return _Resp()

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        raise AssertionError("subprocess fallback must not run when sidecar succeeds")

    monkeypatch.setattr(adapter, "_ensure_server_ready", lambda: None)
    monkeypatch.setattr("app.adapters.irodori_tts_adapter.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.adapters.irodori_tts_adapter.subprocess.run", fake_run)

    adapter.synthesize(
        text="こんにちは", output_path=tmp_path / "out.wav", voice="落ち着いた大人の男性の声で"
    )

    body = captured["body"]
    assert body["text"] == "こんにちは"
    assert body["caption"] == "落ち着いた大人の男性の声で"


def test_synthesize_with_duration_scale_runs_infer_even_with_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """長さ倍率はサイドカーが受け付けないので、指定時は infer.py へ ``--duration-scale`` を渡す。"""
    root_path = tmp_path / "irodori"
    root_path.mkdir()
    (root_path / "infer.py").write_text("", encoding="utf-8")
    captured_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured_command[:] = command
        _write_test_wav(Path(command[command.index("--output-wav") + 1]))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def fake_urlopen(req: object, timeout: float = 0.0):  # type: ignore[no-untyped-def]
        raise AssertionError("sidecar must not be used when duration_scale is given")

    adapter = IrodoriTtsAdapter(root_path=root_path, server_url="http://127.0.0.1:8770")
    monkeypatch.setattr("app.adapters.irodori_tts_adapter.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.adapters.irodori_tts_adapter.subprocess.run", fake_run)

    adapter.synthesize(text="こんにちは", output_path=tmp_path / "out.wav", duration_scale=0.85)

    assert captured_command[captured_command.index("--duration-scale") + 1] == "0.85"

    with pytest.raises(ValueError):
        adapter.synthesize(text="こんにちは", output_path=tmp_path / "out.wav", duration_scale=0)


def test_post_tts_returns_base64_wav(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wav_path = tmp_path / "response.wav"
    wav_bytes = _write_test_wav(wav_path, sample_rate=24000, channels=2)

    def fake_synthesize(
        text: str,
        ref_wav: Path | None = None,
        output_path: Path | None = None,
        voice: str = "default",
    ) -> Path:
        assert text == "接続を確認しました"
        assert voice == "default"
        assert ref_wav is None
        assert output_path is not None
        output_path.write_bytes(wav_bytes)
        return output_path

    monkeypatch.setattr("app.api.tts.tts_adapter.synthesize", fake_synthesize)

    response = client.post(
        "/api/tts",
        json={
            "device_id": "atoms3-001",
            "session_id": "sess-1",
            "text": "接続を確認しました",
            "voice": "default",
            "audio": {"format": "wav", "sample_rate": 16000},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "sess-1"
    assert payload["audio"]["format"] == "wav"
    assert payload["audio"]["sample_rate"] == 24000
    assert payload["audio"]["channels"] == 2
    assert base64.b64decode(payload["audio"]["data"]) == wav_bytes
