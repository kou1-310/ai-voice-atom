import io
import wave
from pathlib import Path

import numpy as np


def read_wav_metadata(path: Path) -> tuple[int, int]:
    """Return (sample_rate, channels) from a wav file header."""
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getframerate(), wav_file.getnchannels()


def to_pcm16_mono(wav_bytes: bytes, target_sr: int = 16000) -> bytes:
    """WAV バイト列を target_sr / 16-bit / モノラルの WAV バイト列へ変換する。

    線形補間でリサンプリングし、44 バイトの正規 WAV ヘッダを持つ小さなファイルを返す。
    実機(AtomS3 + Atomic Echo Base)のメモリ制約に合わせて音声サイズを縮小する用途。
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        src_sr = wf.getframerate()
        src_ch = wf.getnchannels()
        src_sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[src_sw]
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)

    if src_ch > 1:
        samples = samples.reshape(-1, src_ch).mean(axis=1)

    if src_sr != target_sr and len(samples) > 1:
        n_out = int(len(samples) * target_sr / src_sr)
        samples = np.interp(
            np.linspace(0, len(samples) - 1, n_out),
            np.arange(len(samples)),
            samples,
        )

    samples_i16 = np.clip(samples, -32768, 32767).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(target_sr)
        out.writeframes(samples_i16.tobytes())
    return buffer.getvalue()
