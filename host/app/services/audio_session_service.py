import base64
import logging
import os
import time
import wave
from pathlib import Path
from typing import Any

from app.adapters.moonshine_adapter import MoonshineAdapter

logger = logging.getLogger(__name__)


class AudioSessionService:
    def __init__(self, stt_adapter: MoonshineAdapter | None = None) -> None:
        self.stt_adapter = stt_adapter or MoonshineAdapter()
        self._sessions: dict[str, bytearray] = {}

    @property
    def active_session_id(self) -> str | None:
        """直近に開始され、まだ ``audio.end`` していないセッション ID（無ければ None）。"""
        if not self._sessions:
            return None
        return next(reversed(self._sessions))

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        message_type = message.get("type", "unknown")
        session_id = message.get("session_id")

        if not session_id:
            raise ValueError("session_id is required")

        if message_type == "session.start":
            device_id = message.get("device_id")
            return self.handle_session_start(session_id=session_id, device_id=device_id)

        if message_type == "audio.chunk":
            chunk = message.get("data", "")
            return self.handle_audio_chunk(session_id=session_id, pcm_b64=chunk)

        if message_type == "audio.end":
            return self.handle_audio_end(session_id=session_id)

        return {
            "type": "event.ack",
            "session_id": session_id,
            "accepted": True,
            "received_type": message_type,
        }

    def handle_session_start(self, session_id: str, device_id: str | None = None) -> dict[str, Any]:
        self._sessions[session_id] = bytearray()
        return {
            "type": "session.ack",
            "session_id": session_id,
            "device_id": device_id,
            "accepted": True,
        }

    def handle_audio_chunk(self, session_id: str, pcm_b64: str) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise ValueError("session has not been started")

        try:
            chunk = base64.b64decode(pcm_b64, validate=True)
        except ValueError as exc:
            raise ValueError("audio chunk is not valid base64") from exc

        self._sessions[session_id].extend(chunk)
        return {
            "type": "event.ack",
            "session_id": session_id,
            "accepted": True,
            "received_type": "audio.chunk",
        }

    def _dump_debug_wav(self, pcm_bytes: bytes, session_id: str) -> None:
        """``AI_VOICE_ATOM_STT_DEBUG_DIR`` が設定されていれば受信 PCM を WAV 保存する。

        実機で「届いた音声」をそのまま聴いて、欠落・断片化・歪みを確認するための診断用。
        未設定なら何もしない（本番では無効）。
        """
        debug_dir = os.getenv("AI_VOICE_ATOM_STT_DEBUG_DIR")
        if not debug_dir or not pcm_bytes:
            return
        try:
            out_dir = Path(debug_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_session = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
            out_path = out_dir / f"{int(time.time())}_{safe_session}.wav"
            with wave.open(str(out_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(pcm_bytes)
            logger.info("[stt] debug wav saved: %s", out_path)
        except OSError as exc:
            logger.warning("[stt] debug wav save failed: %s", exc)

    def _trim_tail(self, pcm_bytes: bytes) -> bytes:
        """末尾を ``AI_VOICE_ATOM_STT_TRIM_TAIL_MS`` ミリ秒だけ切り落として返す。

        停止ボタンのクリック音を STT 入力から除く。実機検証ではこのクリック音が末尾の
        語の認識を乱したため既定は 300ms（実機で良好だった値）。0 を指定すると無効。
        クリック音が残るなら増やし、語尾が切れるなら減らす。
        """
        try:
            trim_ms = int(os.getenv("AI_VOICE_ATOM_STT_TRIM_TAIL_MS", "300"))
        except ValueError:
            trim_ms = 300
        if trim_ms <= 0:
            return pcm_bytes
        trim_bytes = int(16000 * trim_ms / 1000) * 2  # 16kHz / 16bit mono、2byte 境界に揃える
        if len(pcm_bytes) <= trim_bytes:
            return pcm_bytes
        logger.info("[stt] trimmed tail: -%dms (%d bytes)", trim_ms, trim_bytes)
        return pcm_bytes[:-trim_bytes]

    def handle_audio_end(self, session_id: str) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise ValueError("session has not been started")

        pcm_bytes = bytes(self._sessions.pop(session_id))

        # 受信した音声の長さをログする。発話秒数と一致すれば音声は欠けずに届いている
        # （＝認識誤りは STT モデル側）、短ければデバイス側で末尾を取りこぼしている。
        duration_sec = len(pcm_bytes) / 2 / 16000  # 16kHz / 16bit mono
        logger.info(
            "[stt] received pcm: %d bytes (%.2fs) session=%s",
            len(pcm_bytes),
            duration_sec,
            session_id,
        )
        self._dump_debug_wav(pcm_bytes, session_id)

        # 末尾には停止ボタンのメカ音（カチャっ）が入る。鋭い広帯域トランジェントを STT が
        # 音声と誤解し、末尾の語を取りこぼす/誤認識する原因になるため、認識直前に末尾を
        # 少しトリムして除去する（録音データ自体は上の WAV ダンプで生のまま保持）。
        pcm_for_stt = self._trim_tail(pcm_bytes)

        text = self.stt_adapter.transcribe_pcm(pcm_for_stt, sample_rate=16000)
        return {
            "type": "stt.final",
            "session_id": session_id,
            "text": text,
            "is_final": True,
        }
