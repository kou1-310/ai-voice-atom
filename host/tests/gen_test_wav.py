"""Generate test WAV files for manual verification.

Usage:
    cd host
    uv run python tests/gen_test_wav.py
"""

import math
import struct
import wave
from pathlib import Path

OUT_DIR = Path(__file__).parent / "fixtures"


def _write_wav(path: Path, frequency: float, duration: float, sample_rate: int = 16000) -> None:
    num_samples = int(sample_rate * duration)
    amplitude = 16000
    frames = struct.pack(
        f"<{num_samples}h",
        *(
            int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
            for i in range(num_samples)
        ),
    )
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)
    print(f"wrote {path} ({num_samples} samples, {sample_rate} Hz, {duration:.1f}s)")


def _write_melody(path: Path, sample_rate: int = 16000) -> None:
    """ドレミファソラシドを 1 音 0.45 秒で鳴らす WAV を書き出す（スタブサーバーの再生確認用）。

    単音のビープより「途切れていない・速さが合っている」を耳で確かめやすい。
    音の変わり目でプチッと鳴らないよう、各音の前後を 20ms かけて立ち上げ・立ち下げる。
    """
    notes = [261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25]
    note_samples = int(sample_rate * 0.45)
    gap_samples = int(sample_rate * 0.05)
    fade = int(sample_rate * 0.02)
    amplitude = 12000
    values: list[int] = []
    for frequency in notes:
        for i in range(note_samples):
            envelope = min(1.0, i / fade, (note_samples - 1 - i) / fade)
            values.append(
                int(amplitude * envelope * math.sin(2 * math.pi * frequency * i / sample_rate))
            )
        values.extend([0] * gap_samples)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(values)}h", *values))
    print(
        f"wrote {path} ({len(values)} samples, {sample_rate} Hz, {len(values) / sample_rate:.1f}s)"
    )


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    # ドレミの音階 — スタブサーバー（firmware_test_server.py）が既定で返す音声
    _write_melody(OUT_DIR / "test_melody_16k.wav")

    # 440 Hz (A4) — 1 second beep for WAV playback verification
    _write_wav(OUT_DIR / "beep_440hz_1s.wav", frequency=440.0, duration=1.0)

    # Short beep for base64 round-trip test
    _write_wav(OUT_DIR / "beep_880hz_0.5s.wav", frequency=880.0, duration=0.5)

    # Silent clip — 16kHz 16-bit mono PCM zeros (STT input test)
    silent_path = OUT_DIR / "silence_1s.wav"
    _write_wav(silent_path, frequency=0.0, duration=1.0)

    print(f"\nFiles written to {OUT_DIR}")
    print("Use test_melody_16k.wav with firmware_test_server.py to verify playback on the device.")
    print("Use beep_440hz_1s.wav to verify WAV playback pipeline (TTS → base64 → firmware).")
    print("Use silence_1s.wav as dummy STT input.")


if __name__ == "__main__":
    main()
