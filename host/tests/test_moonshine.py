from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapters.moonshine_adapter import MoonshineAdapter


class _FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def transcribe_without_streaming(
        self,
        audio_data: list[float],
        sample_rate: int = 16000,
        flags: int = 0,
    ) -> object:
        self.calls.append(
            {
                "audio_data": audio_data,
                "sample_rate": sample_rate,
                "flags": flags,
            }
        )
        return SimpleNamespace(
            lines=[
                SimpleNamespace(text="  こんにちは  "),
                SimpleNamespace(text="世界"),
            ]
        )


def test_transcribe_pcm_converts_int16_and_joins_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_transcriber = _FakeTranscriber()
    captured: dict[str, object] = {}

    def fake_get_model_for_language(
        language: str, cache_root: Path | None = None
    ) -> tuple[str, str]:
        captured["language"] = language
        captured["cache_root"] = cache_root
        return ("/tmp/moonshine-model", "base")

    def fake_transcriber_factory(model_path: str, model_arch: str) -> _FakeTranscriber:
        captured["model_path"] = model_path
        captured["model_arch"] = model_arch
        return fake_transcriber

    monkeypatch.setattr(
        "app.adapters.moonshine_adapter.get_model_for_language",
        fake_get_model_for_language,
    )
    monkeypatch.setattr(
        "app.adapters.moonshine_adapter.Transcriber",
        fake_transcriber_factory,
    )

    adapter = MoonshineAdapter(cache_path=Path("/tmp/moonshine-cache"), language="ja")
    pcm_bytes = (-32768).to_bytes(2, "little", signed=True) + (16384).to_bytes(
        2, "little", signed=True
    )

    result = adapter.transcribe_pcm(pcm_bytes, sample_rate=22050)

    assert result == "こんにちは 世界"
    assert captured == {
        "language": "ja",
        "cache_root": Path("/tmp/moonshine-cache"),
        "model_path": "/tmp/moonshine-model",
        "model_arch": "base",
    }
    assert fake_transcriber.calls == [
        {
            "audio_data": [-1.0, 0.5],
            "sample_rate": 22050,
            "flags": 0,
        }
    ]


def test_transcribe_pcm_rejects_unaligned_bytes() -> None:
    adapter = MoonshineAdapter()

    with pytest.raises(ValueError, match="16-bit aligned"):
        adapter.transcribe_pcm(b"\x00")
