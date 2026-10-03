from pathlib import Path

import numpy as np
from moonshine_voice import Transcriber, get_model_for_language

REPO_ROOT = Path(__file__).resolve().parents[3]


class MoonshineAdapter:
    def __init__(
        self,
        root_path: Path | None = None,
        cache_path: Path | None = None,
        language: str = "ja",
    ) -> None:
        self.root_path = root_path or REPO_ROOT / "third_party" / "moonshine"
        self.cache_path = cache_path or REPO_ROOT / ".cache" / "moonshine_voice"
        self.language = language
        self._transcriber: Transcriber | None = None

    def is_available(self) -> bool:
        return self.root_path.exists()

    def transcribe(self, audio_bytes: bytes) -> str:
        return self.transcribe_pcm(audio_bytes)

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        if not pcm_bytes:
            return ""

        if len(pcm_bytes) % 2 != 0:
            raise ValueError("PCM data must be 16-bit aligned")

        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        transcript = self._get_transcriber().transcribe_without_streaming(
            audio.tolist(),
            sample_rate=sample_rate,
        )
        lines = [line.text.strip() for line in transcript.lines if line.text and line.text.strip()]
        return " ".join(lines)

    def _get_transcriber(self) -> Transcriber:
        if self._transcriber is None:
            model_path, model_arch = get_model_for_language(
                self.language,
                cache_root=self.cache_path,
            )
            self._transcriber = Transcriber(model_path=model_path, model_arch=model_arch)
        return self._transcriber
