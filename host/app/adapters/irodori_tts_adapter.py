import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
HOST_DIR = Path(__file__).resolve().parents[2]
TTS_SERVER_SCRIPT = HOST_DIR / "tts_server.py"
DEFAULT_CHECKPOINT = "Aratako/Irodori-TTS-500M-v3"
VOICE_DESIGN_CHECKPOINT = "Aratako/Irodori-TTS-600M-v3-VoiceDesign"

# 常駐サイドカーの自動起動を多重起動させないためのプロセスハンドル（モジュール単位）
_server_process: subprocess.Popen | None = None


class IrodoriTtsAdapter:
    def __init__(
        self,
        root_path: Path | None = None,
        timeout: float = 120.0,
        server_url: str | None = None,
        server_autostart: bool = True,
        server_startup_timeout: float = 90.0,
        ref_wav: Path | None = None,
    ) -> None:
        self.root_path = root_path or REPO_ROOT / "third_party" / "irodori-tts"
        self.timeout = timeout
        # 常駐 TTS サイドカーの URL。設定されていれば subprocess の代わりに HTTP で叩く。
        # 空文字 / None なら従来どおり毎回 infer.py を subprocess 実行する。
        self.server_url = (server_url or "").rstrip("/") or None
        self.server_autostart = server_autostart
        self.server_startup_timeout = server_startup_timeout
        # 話者リファレンス音声の既定の置き場所。synthesize 時に存在すれば自動採用する
        # （呼び出し側が ref_wav を明示した場合はそちらが優先）。実行のたびに存在確認するので
        # 再起動なしでファイルを差し替えられる。
        self.ref_wav = ref_wav

    def is_available(self) -> bool:
        return self.root_path.exists() and (self.root_path / "infer.py").exists()

    def synthesize(
        self,
        text: str,
        ref_wav: Path | None = None,
        output_path: Path | None = None,
        voice: str = "default",
        duration_scale: float | None = None,
    ) -> Path:
        """``duration_scale`` は合成の長さ倍率（1 未満で短く）。指定時はサイドカーを使わず infer.py を起動する。"""
        if not self.is_available():
            raise RuntimeError("Irodori-TTS is not available")

        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("text must not be empty")

        destination = output_path or self.root_path / "outputs" / f"tts-{uuid4().hex}.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)

        # 参照音声を解決する。明示指定が最優先、無ければ既定の置き場所に wav があれば採用する。
        effective_ref = self._resolve_ref_wav(ref_wav)

        # 常駐サイドカーが使えるならそちらを優先する。参照音声・カスタムボイス（caption）とも
        # サイドカーで合成できる（caption は VoiceDesign を初回のみ遅延ロードして常駐）。
        # サイドカー不調時のみ従来の subprocess 経路へ退避する。
        normalized_voice = voice.strip()
        caption = (
            normalized_voice if normalized_voice and normalized_voice.lower() != "default" else None
        )
        if self.server_url is not None and duration_scale is None:
            try:
                wav_bytes = self._synthesize_via_server(normalized_text, effective_ref, caption)
                destination.write_bytes(wav_bytes)
                return destination
            except Exception as exc:  # noqa: BLE001 — サイドカー不調時は subprocess へ退避
                logger.warning("[tts] sidecar failed (%s); falling back to subprocess", exc)

        command = [
            "uv",
            "run",
            "--no-sync",
            "python",
            "infer.py",
            "--text",
            normalized_text,
            "--output-wav",
            str(destination),
        ]

        if normalized_voice and normalized_voice.lower() != "default":
            command.extend(
                ["--hf-checkpoint", VOICE_DESIGN_CHECKPOINT, "--caption", normalized_voice]
            )
        else:
            command.extend(["--hf-checkpoint", DEFAULT_CHECKPOINT])

        if effective_ref is not None:
            command.extend(["--ref-wav", str(effective_ref)])
        else:
            command.append("--no-ref")

        if duration_scale is not None:
            if duration_scale <= 0:
                raise ValueError("duration_scale must be positive")
            command.extend(["--duration-scale", str(duration_scale)])

        try:
            result = subprocess.run(
                command,
                cwd=self.root_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Irodori-TTS inference timed out") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "unknown error"
            raise RuntimeError(f"Irodori-TTS inference failed: {detail}")

        if not destination.exists():
            raise RuntimeError("Irodori-TTS did not produce an output wav file")

        return destination

    def _resolve_ref_wav(self, ref_wav: Path | None) -> Path | None:
        """使用する参照音声を決める。明示指定 > 既定の置き場所（存在する場合）> なし。"""
        candidate = ref_wav if ref_wav is not None else self.ref_wav
        if candidate is None:
            return None
        candidate = Path(candidate)
        if not candidate.exists():
            # 既定の置き場所はファイルが無くても正常（既定の声で合成する）。
            # 明示指定された参照が存在しない場合のみ不正入力として扱う。
            if ref_wav is not None:
                raise ValueError(f"reference wav not found: {candidate}")
            return None
        return candidate.resolve()

    # ---- 常駐サイドカー経路 ----

    def _synthesize_via_server(
        self, text: str, ref_wav: Path | None = None, caption: str | None = None
    ) -> bytes:
        """常駐サイドカーに TTS を依頼し WAV バイト列を得る。未起動なら自動起動する。

        ``caption`` を渡すとサイドカーが VoiceDesign チェックポイントで合成する（初回のみ
        モデルを遅延ロードするため、その回だけ時間がかかる）。
        """
        self._ensure_server_ready()
        body: dict[str, str] = {"text": text}
        if ref_wav is not None:
            # サイドカーは irodori ルートを cwd に持つため絶対パスで渡す。
            body["ref_wav"] = str(ref_wav.resolve())
        if caption:
            body["caption"] = caption
        req = urllib.request.Request(
            f"{self.server_url}/tts",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def _server_ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.server_url}/health", timeout=2.0) as resp:
                payload = json.loads(resp.read() or b"{}")
                return bool(payload.get("ready"))
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _ensure_server_ready(self) -> None:
        if self._server_ready():
            return
        if not self.server_autostart:
            raise RuntimeError(f"TTS sidecar is not running at {self.server_url}")
        self._spawn_server()

        deadline = time.monotonic() + self.server_startup_timeout
        while time.monotonic() < deadline:
            if self._server_ready():
                return
            time.sleep(1.0)
        raise RuntimeError("TTS sidecar did not become ready in time")

    def _spawn_server(self) -> None:
        """サイドカーを irodori venv 上で起動する（多重起動を防ぐ）。"""
        global _server_process
        if _server_process is not None and _server_process.poll() is None:
            return  # このプロセスが起動済み
        if not TTS_SERVER_SCRIPT.is_file():
            raise RuntimeError(f"tts_server.py not found: {TTS_SERVER_SCRIPT}")

        from urllib.parse import urlsplit

        parts = urlsplit(self.server_url)
        host = parts.hostname or "127.0.0.1"
        port = str(parts.port or 8770)
        logger.info("[tts] starting sidecar: %s (port=%s)", TTS_SERVER_SCRIPT, port)
        _server_process = subprocess.Popen(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                str(TTS_SERVER_SCRIPT),
                "--host",
                host,
                "--port",
                port,
            ],
            cwd=self.root_path,
        )
