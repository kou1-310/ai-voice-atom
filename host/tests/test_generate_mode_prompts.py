import io
import wave
from pathlib import Path

import numpy as np

from scripts.generate_mode_prompts import (
    PROMPTS,
    render_header,
    synthesize_prompts,
    trim_and_normalize,
)


def _wav_bytes(samples: np.ndarray, sample_rate: int, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.astype(np.int16).tobytes())
    return buffer.getvalue()


def _read(wav: bytes) -> tuple[int, int, int, np.ndarray]:
    with wave.open(io.BytesIO(wav), "rb") as wf:
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        return wf.getframerate(), wf.getnchannels(), wf.getsampwidth(), data


class _FakeTts:
    """24kHz ステレオで「無音 0.5s + 音 0.2s + 無音 0.5s」を返すフェイク TTS。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path | None]] = []
        self.scales: list[float | None] = []

    def synthesize(
        self,
        text: str,
        ref_wav: Path | None = None,
        output_path: Path | None = None,
        *,
        duration_scale: float | None = None,
    ) -> Path:
        assert output_path is not None
        self.calls.append((text, ref_wav))
        self.scales.append(duration_scale)
        sr = 24000
        tone = (np.sin(np.arange(sr // 5) * 2 * np.pi * 440 / sr) * 8000).astype(np.int16)
        mono = np.concatenate([np.zeros(sr // 2), tone, np.zeros(sr // 2)])
        stereo = np.repeat(mono, 2)
        output_path.write_bytes(_wav_bytes(stereo, sr, channels=2))
        return output_path


def test_synthesize_prompts_outputs_device_format_and_passes_ref(tmp_path: Path) -> None:
    tts = _FakeTts()
    ref = tmp_path / "ref.wav"

    wavs = synthesize_prompts(tts, ref)

    assert list(wavs) == [name for name, _ in PROMPTS]
    assert [text for text, _ in tts.calls] == [text for _, text in PROMPTS]
    assert all(r == ref for _, r in tts.calls)
    for wav in wavs.values():
        assert wav[:4] == b"RIFF"
        sr, ch, sw, data = _read(wav)
        # デバイス規約: 16kHz / mono / 16-bit
        assert (sr, ch, sw) == (16000, 1, 2)
        # 前後の無音 0.5s ずつが詰められ、音 0.2s + パディング程度になる
        assert len(data) < 16000 * 0.4


def test_trim_and_normalize_sets_peak() -> None:
    # 20ms フレーム（320 サンプル）境界にそろえた有音 960 サンプル
    samples = np.concatenate([np.zeros(1600), np.full(960, 1000), np.zeros(1600)])
    _, _, _, data = _read(trim_and_normalize(_wav_bytes(samples, 16000), peak=0.5))

    assert abs(int(np.abs(data).max()) - int(0.5 * 32767)) <= 1
    # パディング 60ms（960 サンプル）×2 ＋ 有音 960
    assert len(data) == 960 + 960 * 2


def test_trim_and_normalize_drops_trailing_noise_after_long_gap() -> None:
    # 発話 0.2s → 無音 0.5s → 余計な音 0.1s（TTS が文末に付ける「はぁ」等を模す）
    samples = np.concatenate(
        [np.full(3200, 1000), np.zeros(8000), np.full(1600, 3000), np.zeros(1600)]
    )
    _, _, _, data = _read(trim_and_normalize(_wav_bytes(samples, 16000)))

    assert len(data) == 3200 + 960
    # 捨てた音ではなく残した発話でピークを揃える
    assert int(np.abs(data).max()) == int(0.9 * 32767)


def test_trim_and_normalize_keeps_silence_as_is() -> None:
    wav = _wav_bytes(np.zeros(1600), 16000)

    assert trim_and_normalize(wav) == wav


def test_render_header_defines_arrays() -> None:
    header = render_header(
        {"kPromptX": b"\x01\x02\xff"}, {"kPromptX": "テスト"}, "VOICEVOX:四国めたん"
    )

    assert "#define MODE_PROMPTS_AVAILABLE 1" in header
    assert "static const uint8_t kPromptX[] = {" in header
    assert "0x01, 0x02, 0xff," in header
    assert "VOICEVOX:四国めたん" in header


def test_synthesize_prompts_reuses_cached_wavs(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    ref = tmp_path / "ref.wav"
    prompts = [("kPromptA", "あ"), ("kPromptB", "い")]

    first = synthesize_prompts(_FakeTts(), ref, prompts, cache_dir=cache)
    assert (cache / "kPromptA.wav").read_bytes() == first["kPromptA"]

    # 2 回目は合成しない（キャッシュから読む）
    tts = _FakeTts()
    second = synthesize_prompts(tts, ref, prompts, cache_dir=cache)
    assert tts.calls == []
    assert second == first

    # 文面が変わった案内だけ合成し直す
    tts = _FakeTts()
    synthesize_prompts(tts, ref, [("kPromptA", "あ"), ("kPromptB", "う")], cache_dir=cache)
    assert [text for text, _ in tts.calls] == ["う"]

    # 参照音声が変わったら作り直す。force なら全部作り直す
    tts = _FakeTts()
    synthesize_prompts(tts, tmp_path / "other.wav", prompts[:1], cache_dir=cache)
    assert [text for text, _ in tts.calls] == ["あ"]
    tts = _FakeTts()
    synthesize_prompts(tts, tmp_path / "other.wav", prompts[:1], cache_dir=cache, force=True)
    assert [text for text, _ in tts.calls] == ["あ"]


def test_synthesize_prompts_passes_duration_scale_and_keys_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    ref = tmp_path / "ref.wav"
    prompts = [("kPromptA", "あ"), ("kPromptB", "い")]

    tts = _FakeTts()
    synthesize_prompts(tts, ref, prompts, cache_dir=cache, duration_scales={"kPromptB": 0.85})
    assert tts.scales == [None, 0.85]
    # 倍率なしの案内のキーは従来のまま（既存のキャッシュを無効にしない）
    assert (cache / "kPromptA.txt").read_text(encoding="utf-8") == "あ\nref=ref.wav"

    # 倍率を変えた案内だけ合成し直す
    tts = _FakeTts()
    synthesize_prompts(tts, ref, prompts, cache_dir=cache, duration_scales={"kPromptB": 0.9})
    assert [text for text, _ in tts.calls] == ["い"]
    assert tts.scales == [0.9]


def test_duration_scales_refer_to_existing_prompts() -> None:
    from scripts.generate_mode_prompts import DURATION_SCALES

    assert set(DURATION_SCALES) <= {name for name, _ in PROMPTS}


def test_render_header_has_version() -> None:
    from scripts.generate_mode_prompts import PROMPTS_VERSION

    header = render_header({"kPromptX": b"\x01"}, {}, "c")
    assert f"#define MODE_PROMPTS_VERSION {PROMPTS_VERSION}" in header
