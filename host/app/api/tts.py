import base64
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException

from app.adapters.irodori_tts_adapter import IrodoriTtsAdapter
from app.config import get_settings
from app.errors import ApiError
from app.models.schemas import AudioResponse, TtsRequest, TtsResponse
from app.utils.wav import read_wav_metadata

router = APIRouter(tags=["tts"])
_settings = get_settings()
tts_adapter = IrodoriTtsAdapter(
    root_path=_settings.tts_root_path,
    timeout=_settings.tts_timeout,
    server_url=_settings.tts_server_url,
    server_autostart=_settings.tts_server_autostart,
    ref_wav=_settings.tts_ref_wav,
)


@router.post("/api/tts", response_model=TtsResponse)
async def post_tts(payload: TtsRequest) -> TtsResponse:
    if payload.audio.format.lower() != "wav":
        raise ApiError(400, "INVALID_AUDIO_FORMAT", "Only wav output is supported")

    try:
        with TemporaryDirectory(prefix="ai-voice-atom-tts-") as temp_dir:
            output_path = Path(temp_dir) / "output.wav"
            wav_path = tts_adapter.synthesize(
                text=payload.text,
                output_path=output_path,
                voice=payload.voice,
            )
            wav_bytes = wav_path.read_bytes()
            sample_rate, channels = read_wav_metadata(wav_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return TtsResponse(
        session_id=payload.session_id,
        audio=AudioResponse(
            format="wav",
            encoding="base64",
            sample_rate=sample_rate,
            channels=channels,
            data=base64.b64encode(wav_bytes).decode("ascii"),
        ),
    )
