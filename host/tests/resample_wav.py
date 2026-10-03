"""WAV ファイルを 16kHz / 16-bit / mono に変換する。

手持ちの WAV をスタブサーバー（firmware_test_server.py）で実機に鳴らしたいときに使う。

Usage:
    cd host
    uv run python tests/resample_wav.py <入力.wav> <出力.wav>
"""

import sys
import wave
from pathlib import Path

import numpy as np

TARGET_SR = 16000


def resample(src: Path, dst: Path, target_sr: int = TARGET_SR) -> None:
    with wave.open(str(src), "rb") as wf:
        src_sr = wf.getframerate()
        src_ch = wf.getnchannels()
        src_sw = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[src_sw]
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)

    # インターリーブされたチャンネルをモノラルにミックスダウン
    if src_ch > 1:
        samples = samples.reshape(-1, src_ch).mean(axis=1)

    # 線形補間でリサンプリング
    if src_sr != target_sr:
        ratio = target_sr / src_sr
        n_out = int(len(samples) * ratio)
        x_src = np.linspace(0, len(samples) - 1, n_out)
        samples = np.interp(x_src, np.arange(len(samples)), samples)

    # 16-bit にクリップ・変換
    samples_i16 = np.clip(samples, -32768, 32767).astype(np.int16)

    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sr)
        wf.writeframes(samples_i16.tobytes())

    dur = len(samples_i16) / target_sr
    print(f"  src: {src}  ({src_sr} Hz, {src_ch}ch, {n_frames / src_sr:.2f}s)")
    print(f"  dst: {dst}  ({target_sr} Hz, 1ch, {dur:.2f}s, {dst.stat().st_size} B)")


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python tests/resample_wav.py <入力.wav> <出力.wav>")
        sys.exit(1)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])

    if not src.exists():
        print(f"[ERROR] source file not found: {src}")
        sys.exit(1)

    print(f"Resampling {src.name} → {dst.name} ...")
    resample(src, dst)
    print("Done.")


if __name__ == "__main__":
    main()
