"""モード切替の音声案内を合成し、ファームウェア埋め込み用の C ヘッダを生成する。

参照音声（既定: ``host/voices/shikoku_metan-normal.wav``）の声色で Irodori-TTS に案内文を
読ませ、デバイス規約の 16kHz / mono / 16-bit WAV へ変換・前後の無音を詰めて
``firmware/atom/include/mode_prompts.h`` に書き出す。ファームは ``__has_include`` で
このヘッダを任意に取り込み、無ければ従来のチャイムで代用する。

参照音声とそこから生成した音声は著作権物を含むためコミットしない（``.gitignore``）。
四国めたんの声を使う場合はクレジット「VOICEVOX:四国めたん」を表記すること。

使い方（host/ から）::

    uv run python scripts/generate_mode_prompts.py [--ref-wav PATH] [--wav-dir DIR] [--force]

個別の WAV は ``--wav-dir``（既定 ``firmware/atom/prompts/``、Git 管理外）に試聴用として保存し、
文面と参照音声が同じ案内は次回から再合成せずに再利用する（案内を追加したときに差分だけ合成する）。
``--force`` で全文を合成し直す。1 文の合成に 1〜2 分かかる。1 文だけ作り直すときは、``--wav-dir`` の
``<配列名>.wav`` と ``<配列名>.txt`` を消してから実行する（合成は毎回ゆらぐので、気に入るまで繰り返せる）。
語尾が間延びする文面は ``DURATION_SCALES`` で長さを縮める。
"""

from __future__ import annotations

import argparse
import io
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

import numpy as np

from app.adapters.irodori_tts_adapter import IrodoriTtsAdapter
from app.utils.wav import to_pcm16_mono

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REF_WAV = REPO_ROOT / "host" / "voices" / "shikoku_metan-normal.wav"
DEFAULT_OUT_HEADER = REPO_ROOT / "firmware" / "atom" / "include" / "mode_prompts.h"
DEFAULT_WAV_DIR = REPO_ROOT / "firmware" / "atom" / "prompts"

SAMPLE_RATE = 16000
# ヘッダの版数。ファームはこれが足りないヘッダ（古い生成物）を無視してチャイムで代用する。
# PROMPTS の配列名を追加・変更したら上げ、main.cpp の REQUIRED_PROMPTS_VERSION と揃える。
PROMPTS_VERSION = 2

# (C 配列名, 案内文)。配列名はファーム側（main.cpp）が参照するので変える場合は両方を揃える。
PROMPTS: list[tuple[str, str]] = [
    ("kPromptModeWeb", "チャットモードですわ。"),
    ("kPromptModeVoice", "音声会話モードですわ。"),
    ("kPromptModeRelay", "リレー会話モードですわ。"),
    ("kPromptModeRecorder", "録音再生モードですわ。"),
    ("kPromptRecEmpty", "まだ録音がありませんわ。"),
    # オウム返し（parrot）モード
    ("kPromptModeParrot", "オウム返しモードですわ。"),
    ("kPromptVoiceHigh", "高い声にしましたわ。"),
    ("kPromptVoiceNormal", "ふつうの声にしましたわ。"),
    ("kPromptVoiceLow", "低い声にしましたわ。"),
    # タイマー（timer）モード
    ("kPromptModeTimer", "タイマーモードですわ。"),
    ("kPromptTimer1", "1分にしましたわ。"),
    ("kPromptTimer3", "3分にしましたわ。"),
    ("kPromptTimer5", "5分にしましたわ。"),
    ("kPromptTimer10", "10分にしましたわ。"),
    ("kPromptTimer25", "25分にしましたわ。"),
    ("kPromptTimerStart", "スタートですわ。"),
    ("kPromptTimerStop", "タイマーを止めましたわ。"),
    ("kPromptTimerOneMinute", "残りいっぷんですわ。"),
    ("kPromptTimerDone", "時間ですわ！"),
]

# 合成の長さ倍率（配列名 → 倍率。1 未満で短く）。Irodori-TTS が長さを長めに見積もって語尾が間延びする
# 文面だけ補正する。「リレー会話モードですわ。」は既定だと「です」のあとに間が空いて「わ」が消え入るため縮める。
DURATION_SCALES: dict[str, float] = {
    "kPromptModeRelay": 0.85,
}


class TtsLike(Protocol):
    def synthesize(
        self,
        text: str,
        ref_wav: Path | None = None,
        output_path: Path | None = None,
        *,
        duration_scale: float | None = None,
    ) -> Path: ...


def trim_and_normalize(
    wav_bytes: bytes,
    threshold: float = 0.05,
    pad_ms: int = 60,
    max_gap_ms: int = 400,
    peak: float = 0.9,
    frame_ms: int = 20,
) -> bytes:
    """16kHz/mono/16-bit WAV の前後の無音を詰め、ピークを揃えた WAV を返す。

    案内ごとの音量差をなくし、切替から発話までの間延びを減らすため。
    Irodori-TTS は文末の無音（実測 0.5 秒以上）のあとに「はぁ」等の余計な音を足すことがあるので、
    発話開始後に ``max_gap_ms`` 以上の無音が続いたらそこで打ち切る。文中の間（読点など）で切れないよう
    これより短い間は許すが、2 文以上の案内は文間で切れやすいので 1 文にすること。``threshold`` はフレーム RMS の最大値に対する比率。
    無音しか無い場合は入力をそのまま返す。
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sr = wf.getframerate()
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    frame = max(sr * frame_ms // 1000, 1)
    n_frames = samples.size // frame
    if n_frames == 0:
        return wav_bytes
    frames = samples[: n_frames * frame].astype(np.float32).reshape(n_frames, frame)
    rms = np.sqrt((frames**2).mean(axis=1))
    if float(rms.max()) == 0.0:
        return wav_bytes

    voiced = rms >= float(rms.max()) * threshold
    first = int(np.flatnonzero(voiced)[0])
    last = first
    gap_limit = max(max_gap_ms // frame_ms, 1)
    silent_run = 0
    for i in range(first + 1, n_frames):
        if voiced[i]:
            last = i
            silent_run = 0
        else:
            silent_run += 1
            if silent_run >= gap_limit:
                break

    pad = sr * pad_ms // 1000
    start = max(first * frame - pad, 0)
    end = min((last + 1) * frame + pad, samples.size)
    kept = samples[start:end].astype(np.float32)
    max_amp = float(np.abs(kept).max())
    trimmed = kept * (peak * 32767.0 / max_amp) if max_amp > 0.0 else kept

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sr)
        out.writeframes(np.clip(trimmed, -32768, 32767).astype(np.int16).tobytes())
    return buffer.getvalue()


def cache_key(text: str, ref_wav: Path | None, duration_scale: float | None = None) -> str:
    """再利用の判定キー。文面・参照音声（ファイル名）・長さ倍率が同じなら同じ音声とみなす。"""
    key = f"{text}\nref={ref_wav.name if ref_wav is not None else ''}"
    return key if duration_scale is None else f"{key}\nduration_scale={duration_scale}"


def synthesize_prompts(
    tts: TtsLike,
    ref_wav: Path | None,
    prompts: list[tuple[str, str]] = PROMPTS,
    cache_dir: Path | None = None,
    force: bool = False,
    duration_scales: dict[str, float] = DURATION_SCALES,
) -> dict[str, bytes]:
    """各案内文を合成し、配列名 → 16kHz/mono/16-bit WAV バイト列の辞書を返す。

    ``cache_dir`` があれば ``<配列名>.wav`` と判定キー ``<配列名>.txt`` を保存し、次回はキーが
    一致する案内を再合成せずに読み込む（``force`` で無効化）。
    """
    results: dict[str, bytes] = {}
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="ai-voice-atom-prompts-") as temp_dir:
        for name, text in prompts:
            duration_scale = duration_scales.get(name)
            key = cache_key(text, ref_wav, duration_scale)
            if cache_dir is not None and not force:
                cached_wav = cache_dir / f"{name}.wav"
                cached_key = cache_dir / f"{name}.txt"
                if (
                    cached_wav.is_file()
                    and cached_key.is_file()
                    and cached_key.read_text(encoding="utf-8") == key
                ):
                    print(f"[prompts] {name}: 再利用（{text}）", file=sys.stderr)
                    results[name] = cached_wav.read_bytes()
                    continue
            print(f"[prompts] {name}: {text}", file=sys.stderr)
            wav_path = tts.synthesize(
                text=text,
                ref_wav=ref_wav,
                output_path=Path(temp_dir) / f"{name}.wav",
                duration_scale=duration_scale,
            )
            pcm = to_pcm16_mono(wav_path.read_bytes(), target_sr=SAMPLE_RATE)
            results[name] = trim_and_normalize(pcm)
            if cache_dir is not None:
                (cache_dir / f"{name}.wav").write_bytes(results[name])
                (cache_dir / f"{name}.txt").write_text(key, encoding="utf-8")
    return results


def render_header(wavs: dict[str, bytes], texts: dict[str, str], credit: str) -> str:
    """WAV バイト列を C の const 配列として並べたヘッダ文字列を返す。"""
    lines = [
        "// 自動生成ファイル — host/scripts/generate_mode_prompts.py が出力する。手で編集しないこと。",
        "// 参照音声由来の音声を含むためコミットしない（.gitignore）。",
        f"// クレジット: {credit}",
        "#pragma once",
        "#include <stdint.h>",
        "#include <stddef.h>",
        "",
        "#define MODE_PROMPTS_AVAILABLE 1",
        f"#define MODE_PROMPTS_VERSION {PROMPTS_VERSION}",
        "",
    ]
    for name, data in wavs.items():
        lines.append(f"// 「{texts.get(name, '')}」 {len(data)} bytes")
        lines.append(f"static const uint8_t {name}[] = {{")
        for i in range(0, len(data), 24):
            chunk = ", ".join(f"0x{b:02x}" for b in data[i : i + 24])
            lines.append(f"  {chunk},")
        lines.append("};")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref-wav", type=Path, default=DEFAULT_REF_WAV, help="声色の参照音声")
    parser.add_argument("--out-header", type=Path, default=DEFAULT_OUT_HEADER)
    parser.add_argument(
        "--wav-dir",
        type=Path,
        default=DEFAULT_WAV_DIR,
        help="試聴用 WAV の保存先（再利用にも使う）",
    )
    parser.add_argument("--force", action="store_true", help="合成済みの案内も作り直す")
    parser.add_argument("--credit", default="VOICEVOX:四国めたん", help="ヘッダに記すクレジット")
    parser.add_argument("--timeout", type=float, default=900.0, help="1 文あたりの合成上限秒")
    args = parser.parse_args()

    if not args.ref_wav.exists():
        print(f"[prompts] 参照音声が見つかりません: {args.ref_wav}", file=sys.stderr)
        return 1

    # サイドカーは使わず毎回 infer.py を起動する（ホストを起動していなくても生成できるように）。
    tts = IrodoriTtsAdapter(timeout=args.timeout, server_url=None)
    wavs = synthesize_prompts(tts, args.ref_wav, cache_dir=args.wav_dir, force=args.force)

    args.out_header.parent.mkdir(parents=True, exist_ok=True)
    args.out_header.write_text(render_header(wavs, dict(PROMPTS), args.credit), encoding="utf-8")
    total = sum(len(d) for d in wavs.values())
    print(f"[prompts] wrote {args.out_header} ({total} bytes of audio)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
