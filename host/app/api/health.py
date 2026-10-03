from datetime import UTC, datetime

from fastapi import APIRouter

from app.adapters.irodori_tts_adapter import IrodoriTtsAdapter
from app.adapters.moonshine_adapter import MoonshineAdapter
from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def get_health() -> dict:
    settings = get_settings()
    tts_adapter = IrodoriTtsAdapter(root_path=settings.tts_root_path)
    stt_adapter = MoonshineAdapter(root_path=settings.moonshine_root_path)
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "services": {
            "llm": "configured" if settings.llm_configured else "not-configured",
            "tts": "configured" if tts_adapter.is_available() else "not-configured",
            "stt": "configured" if stt_adapter.is_available() else "not-configured",
        },
        "paths": {
            "irodori_root": str(settings.tts_root_path),
            "moonshine_root": str(settings.moonshine_root_path),
        },
    }
