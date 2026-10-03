"""ファームウェア動作確認用の軽量スタブサーバー。

LLM / TTS が不要な状態でファームウェアの動作を段階的に確認する。
ポート 8000 でリッスンし、ファームウェアが呼ぶエンドポイントを提供する。

  GET  /api/health          → 200 OK
  POST /api/chat/audio      → test_melody_16k.wav（ドレミの音階）をそのまま audio/wav で返す（web モードのクリックで呼ばれる）
  POST /api/chat            → 同じ WAV を base64 の JSON で返す（JSON 版の確認用）
  POST /api/playback/stop   → {"status": "accepted"}

WAV は 16kHz / mono / 16-bit（44 バイトの正規ヘッダ）であること。ファームはそれ以外を拒否する。

Usage:
    cd host
    uv run python tests/firmware_test_server.py
    # または別の WAV を指定:
    uv run python tests/firmware_test_server.py path/to/my.wav
"""

import base64
import http.server
import json
import sys
import wave
from pathlib import Path
from urllib.parse import quote

DEFAULT_WAV = Path(__file__).parent / "fixtures" / "test_melody_16k.wav"
PORT = 8000
STUB_TEXT = "これはファームウェアテスト用のスタブ応答です。"


def _load_wav(path: Path) -> tuple[str, int, int]:
    """WAV を読み込み (base64データ, sample_rate, channels) を返す。"""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return b64, sr, ch


class StubHandler(http.server.BaseHTTPRequestHandler):
    wav_bytes: bytes = b""
    wav_b64: str = ""
    wav_sr: int = 16000
    wav_ch: int = 1

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.address_string()}] {fmt % args}")

    def _send_json(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self._send_json(200, {"status": "ok", "submodules": {}})
        else:
            self._send_json(404, {"detail": "not found"})

    def _send_wav(self) -> None:
        """本番の /api/chat/audio と同じく、生の WAV と応答テキストのヘッダを返す。"""
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(self.wav_bytes)))
        self.send_header("X-LLM-Text", quote(STUB_TEXT))
        self.send_header("X-LLM-Text-B64", base64.b64encode(STUB_TEXT.encode()).decode("ascii"))
        self.end_headers()
        self.wfile.write(self.wav_bytes)

    def do_POST(self) -> None:
        # リクエストボディは使わないが、読み捨ててから応答する（接続を正しく閉じるため）。
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path == "/api/chat/audio":
            self._send_wav()
        elif self.path == "/api/chat":
            self._send_json(
                200,
                {
                    "session_id": "fw-test",
                    "message_id": "msg-stub",
                    "llm_text": STUB_TEXT,
                    "audio": {
                        "format": "wav",
                        "encoding": "base64",
                        "sample_rate": self.wav_sr,
                        "channels": self.wav_ch,
                        "data": self.wav_b64,
                    },
                },
            )
        elif self.path == "/api/playback/stop":
            self._send_json(200, {"status": "accepted"})
        else:
            self._send_json(404, {"detail": "not found"})


def main() -> None:
    wav_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WAV

    if not wav_path.exists():
        print(f"[ERROR] WAV not found: {wav_path}")
        print("既定のテスト音声は gen_test_wav.py で作り直せます:")
        print("  uv run python tests/gen_test_wav.py")
        sys.exit(1)

    b64, sr, ch = _load_wav(wav_path)
    StubHandler.wav_bytes = wav_path.read_bytes()
    StubHandler.wav_b64 = b64
    StubHandler.wav_sr = sr
    StubHandler.wav_ch = ch

    wav_kb = wav_path.stat().st_size // 1024
    b64_kb = len(b64) // 1024
    print(f"WAV: {wav_path.name}  ({sr} Hz, {ch}ch, {wav_kb} KB → base64 {b64_kb} KB)")
    print(f"Starting stub server on http://0.0.0.0:{PORT}")
    print()
    print("firmware/atom/.env を以下に設定してアップロード:")
    print(f"  HOST_BASE_URL=http://<このPCのIP>:{PORT}")
    print()
    print("エンドポイント:")
    print(f"  GET  http://0.0.0.0:{PORT}/api/health")
    print(f"  POST http://0.0.0.0:{PORT}/api/chat/audio   ← web モードのクリックで呼ばれる")
    print(f"  POST http://0.0.0.0:{PORT}/api/chat")
    print(f"  POST http://0.0.0.0:{PORT}/api/playback/stop ← 再生中のクリックで呼ばれる")
    print()
    print("Ctrl+C で停止")

    with http.server.HTTPServer(("0.0.0.0", PORT), StubHandler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
