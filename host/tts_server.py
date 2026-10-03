#!/usr/bin/env python3
"""Irodori-TTS 常駐サイドカー。

モデルを **起動時に1回だけ** ロードして GPU に常駐させ、HTTP で TTS リクエストを
受け付ける。これにより host の ``IrodoriTtsAdapter`` から呼ぶたびに発生していた
「``uv run python infer.py`` の新規プロセス起動＋モデルのコールドロード（約13秒/回）」
を排除する。実際の推論は GPU 上で約2秒なので、TTS の体感が劇的に短縮される。

irodori-tts の venv 内で実行すること（``irodori_tts`` を import するため）::

    uv run --no-sync python <repo>/host/tts_server.py --host 127.0.0.1 --port 8770

通常は host 側の ``IrodoriTtsAdapter`` が自動起動するので手動起動は不要。
標準ライブラリ＋irodori venv に既に入っている依存のみを使う（余計な依存を足さない）。

エンドポイント:
- ``GET  /health`` -> ``{"status": "ok", "ready": bool}``
- ``POST /tts``    -> body は JSON ``{"text": "...", "ref_wav"?: "...", "caption"?: "..."}``、
  応答は ``audio/wav`` バイナリ。``caption`` 指定時は VoiceDesign チェックポイントで合成する
  （初回のみ遅延ロード）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ``irodori_tts`` は pip 導入パッケージではなく irodori ルートのソースディレクトリにある。
# infer.py は cwd(=irodori ルート)が sys.path に入ることで import できているため、
# このスクリプト（host/ 配置）でも cwd を path 先頭に追加して同じ解決にする。
# 起動時は必ず irodori ルートを cwd にすること（アダプタの自動起動はそうしている）。
sys.path.insert(0, os.getcwd())

from huggingface_hub import hf_hub_download  # noqa: E402
from irodori_tts.inference_runtime import (  # noqa: E402
    InferenceRuntime,
    RuntimeKey,
    SamplingRequest,
    default_runtime_device,
    list_available_runtime_precisions,
    save_wav,
)

DEFAULT_CHECKPOINT = "Aratako/Irodori-TTS-500M-v3"
# caption（声色の自然文指定 / VoiceDesign）を扱うチェックポイント。infer.py の
# VOICE_DESIGN_CHECKPOINT と一致させること。caption が来たときだけ遅延ロードする。
VOICE_DESIGN_CHECKPOINT = "Aratako/Irodori-TTS-600M-v3-VoiceDesign"
CODEC_REPO = "Aratako/Semantic-DACVAE-Japanese-32dim"

# checkpoint ごとにロード済み InferenceRuntime をキャッシュする。既定チェックポイントは
# 起動時に常駐ロードし、VoiceDesign は最初の caption 要求時に遅延ロードして常駐させる
# （毎回 infer.py を起動して 600M モデルを再ロードしていた低速・タイムアウトを解消する）。
_runtimes: dict[str, InferenceRuntime] = {}
# モデル（GPU）はスレッドセーフでないため合成は直列化する
_synth_lock = threading.Lock()
# 遅延ロードの多重実行を防ぐ（ロード中に別リクエストが来ても二重ロードさせない）
_load_lock = threading.Lock()
# 起動時に解決したデバイス/精度を遅延ロードでも使い回す
_device: str | None = None
_precision: str | None = None


def _resolve_device() -> str:
    """使用デバイス。``AI_VOICE_ATOM_TTS_DEVICE`` で上書き可、無ければ自動（cuda優先）。"""
    override = (os.environ.get("AI_VOICE_ATOM_TTS_DEVICE") or "").strip()
    return override or default_runtime_device()


def _resolve_precision(device: str) -> str:
    """使用精度。``AI_VOICE_ATOM_TTS_PRECISION`` で指定（既定 fp32）。

    既定は高精度の fp32。``bf16`` を指定すると GPU の VRAM/演算が約半減し画面描画との
    GPU 競合が和らぐ（音質はわずかに変わりうる）。指定がデバイスで非対応なら fp32 へ退避。
    """
    requested = (os.environ.get("AI_VOICE_ATOM_TTS_PRECISION") or "fp32").strip().lower()
    available = list_available_runtime_precisions(device)
    if requested in available:
        return requested
    print(
        f"[tts_server] precision={requested!r} は device={device} で非対応。"
        f"{available[0]!r} に退避します。",
        flush=True,
    )
    return available[0]


def _apply_thread_limit() -> None:
    """``AI_VOICE_ATOM_TTS_TORCH_THREADS`` が指定されていれば torch の CPU スレッド数を制限する。

    既定（未指定）は torch 任せ（全コア）。CPU 全コア張り付きで OS/デスクトップが
    重くなるのを避けたいときに、例えば 12 コア中 8 などに絞る。
    """
    raw = (os.environ.get("AI_VOICE_ATOM_TTS_TORCH_THREADS") or "").strip()
    if not raw:
        return
    try:
        threads = int(raw)
    except ValueError:
        print(f"[tts_server] TTS_TORCH_THREADS={raw!r} を整数解釈できず無視します。", flush=True)
        return
    if threads <= 0:
        return
    import torch

    torch.set_num_threads(threads)
    print(f"[tts_server] torch CPU threads limited to {threads}", flush=True)


def _build_runtime(checkpoint_repo: str) -> InferenceRuntime:
    """checkpoint_repo の model.safetensors を取得して InferenceRuntime を組む。"""
    assert _device is not None and _precision is not None  # load_runtime で解決済み
    checkpoint_path = hf_hub_download(repo_id=checkpoint_repo, filename="model.safetensors")
    print(
        f"[tts_server] loading model {checkpoint_repo} on device={_device} "
        f"precision={_precision} ...",
        flush=True,
    )
    t0 = time.perf_counter()
    runtime = InferenceRuntime.from_key(
        RuntimeKey(
            checkpoint=checkpoint_path,
            model_device=_device,
            codec_repo=CODEC_REPO,
            model_precision=_precision,
            codec_device=_device,
            codec_precision=_precision,
            codec_deterministic_encode=True,
            codec_deterministic_decode=True,
            compile_model=False,
            compile_dynamic=False,
        )
    )
    print(
        f"[tts_server] model {checkpoint_repo} ready in {time.perf_counter() - t0:.1f}s",
        flush=True,
    )
    return runtime


def _get_runtime(checkpoint_repo: str) -> InferenceRuntime:
    """checkpoint_repo のランタイムをキャッシュから返す。未ロードなら遅延ロードする。

    VoiceDesign（caption 用）は初回の caption 要求でここからロードされて常駐し、以降は
    再ロードなしで合成できる。ロードは ``_load_lock`` で直列化し二重ロードを防ぐ。
    """
    runtime = _runtimes.get(checkpoint_repo)
    if runtime is not None:
        return runtime
    with _load_lock:
        runtime = _runtimes.get(checkpoint_repo)
        if runtime is None:
            runtime = _build_runtime(checkpoint_repo)
            _runtimes[checkpoint_repo] = runtime
    return runtime


def load_runtime() -> None:
    """既定チェックポイントを1回だけロードして常駐させる（起動時）。"""
    global _device, _precision
    _apply_thread_limit()
    _device = _resolve_device()
    _precision = _resolve_precision(_device)
    _get_runtime(DEFAULT_CHECKPOINT)


def synthesize_wav(text: str, ref_wav: str | None = None, caption: str | None = None) -> bytes:
    """テキストを TTS して WAV バイト列を返す。

    ``caption`` を渡すと声色を自然文で指定して合成する（VoiceDesign チェックポイント・
    infer.py の ``--caption`` 相当）。``ref_wav`` を渡すとその話者音声で条件付けし
    （infer.py の ``--ref-wav`` 相当）、無ければ参照なし（``--no-ref`` 相当）。caption と
    ref_wav は併用でき、「その声＋このスタイル」のクローンになる。
    """
    if not _runtimes:
        raise RuntimeError("runtime is not loaded")

    caption_text = (caption or "").strip() or None
    checkpoint_repo = VOICE_DESIGN_CHECKPOINT if caption_text else DEFAULT_CHECKPOINT
    runtime = _get_runtime(checkpoint_repo)

    if ref_wav is not None:
        if not os.path.isfile(ref_wav):
            raise FileNotFoundError(f"reference wav not found: {ref_wav}")
        # ref 系の既定値は infer.py / SamplingRequest の既定と一致させる。
        request = SamplingRequest(text=text, caption=caption_text, ref_wav=ref_wav, no_ref=False)
    else:
        # SamplingRequest は text/caption 以外すべて既定値（infer.py の argparse 既定と一致）
        request = SamplingRequest(text=text, caption=caption_text, no_ref=True)

    t0 = time.perf_counter()
    with _synth_lock:
        result = runtime.synthesize(request)
    infer_s = time.perf_counter() - t0

    # save_wav はパス必須なので一時ファイル経由で bytes 化する
    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        save_wav(tmp_path, result.audio, result.sample_rate)
        with open(tmp_path, "rb") as f:
            data = f.read()
    finally:
        os.unlink(tmp_path)

    print(
        f"[tts_server] synthesized chars={len(text)} wav={len(data)}B in {infer_s:.2f}s"
        f"{' ref=' + os.path.basename(ref_wav) if ref_wav else ''}"
        f"{' caption' if caption_text else ''}",
        flush=True,
    )
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # アクセスログは抑制（必要な行は自前で print）
        pass

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            # 既定チェックポイントが常駐していれば ready。VoiceDesign は遅延ロードなので
            # ここでは見ない（caption 要求時に初回ロードされる）。
            ready = DEFAULT_CHECKPOINT in _runtimes
            self._send_json(200, {"status": "ok", "ready": ready})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/tts":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "text must not be empty"})
                return
            ref_wav = payload.get("ref_wav") or None
            caption = payload.get("caption") or None
            wav_bytes = synthesize_wav(text, ref_wav, caption)
        except Exception as exc:  # noqa: BLE001 — 失敗は 500 にして詳細を返す
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav_bytes)))
        self.end_headers()
        self.wfile.write(wav_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Irodori-TTS persistent sidecar server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    load_runtime()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[tts_server] listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
