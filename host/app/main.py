import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.api.conversation import router as conversation_router
from app.api.device import router as device_router
from app.api.health import router as health_router
from app.api.playback import router as playback_router
from app.api.tts import router as tts_router
from app.api.ws_audio import router as ws_audio_router
from app.errors import register_error_handlers
from app.middleware.logging_middleware import RequestLoggingMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPO_ROOT / "frontend"


def create_app() -> FastAPI:
    app = FastAPI(
        title="ai-voice-atom host",
        version="0.1.0",
        description="Host API for AtomS3-Lite voice and web control integration.",
    )
    app.add_middleware(RequestLoggingMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(device_router)
    app.include_router(chat_router)
    app.include_router(conversation_router)
    app.include_router(playback_router)
    app.include_router(tts_router)
    app.include_router(ws_audio_router)
    app.mount("/frontend", StaticFiles(directory=FRONTEND_ROOT, html=True), name="frontend")
    return app


app = create_app()
