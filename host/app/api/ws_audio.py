from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.adapters.moonshine_adapter import MoonshineAdapter
from app.config import get_settings
from app.services.audio_session_service import AudioSessionService
from app.services.connection_manager import connection_manager

router = APIRouter(tags=["audio"])
settings = get_settings()
audio_session_service = AudioSessionService(
    stt_adapter=MoonshineAdapter(
        root_path=settings.moonshine_root_path,
        cache_path=settings.moonshine_cache_path,
        language=settings.stt_model_language,
    )
)


@router.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket) -> None:
    """STT アップリンクと、リレー会話の push ダウンリンクを兼ねる接続。

    - ``register`` / ``session.start``: ``device_id`` を ``ConnectionManager`` に登録し、
      ホスト→デバイスの ``play`` push を受け取れるようにする。
    - ``played``: リレー会話のターン完了 ack（orchestrator の待ちを解く）。
    - それ以外（``session.start`` / ``audio.chunk`` / ``audio.end``）: 従来どおり STT に委譲。
    """
    await websocket.accept()
    device_id: str | None = None
    try:
        while True:
            message = await websocket.receive_json()
            mtype = message.get("type")

            if mtype == "register":
                device_id = message.get("device_id") or device_id
                connection_manager.register(device_id, websocket)
                await websocket.send_json({"type": "registered", "device_id": device_id})
                continue

            if mtype == "played":
                connection_manager.resolve_ack(
                    message.get("device_id") or device_id, message.get("turn")
                )
                continue

            # STT 経路。session.start の device_id でも push 用に登録しておく
            # （voice モードのデバイスも到達可能にし、既存ファームと後方互換）。
            if mtype == "session.start" and message.get("device_id"):
                device_id = message.get("device_id")
                connection_manager.register(device_id, websocket)

            try:
                response = audio_session_service.handle_message(message)
            except (ValueError, RuntimeError) as exc:
                response = {
                    "type": "error",
                    "session_id": message.get("session_id"),
                    "message": str(exc),
                }
            await websocket.send_json(response)
    except WebSocketDisconnect:
        return
    finally:
        connection_manager.unregister(device_id, websocket)
