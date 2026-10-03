from fastapi import APIRouter, HTTPException

from app.api.chat import chat_service
from app.config import get_settings
from app.models.schemas import (
    ConversationChatRequest,
    ConversationChatResponse,
    ConversationStartRequest,
    ConversationStartResponse,
    ConversationStatusResponse,
)
from app.services.connection_manager import DeviceNotConnectedError, connection_manager
from app.services.conversation_orchestrator import ConversationOrchestrator

router = APIRouter(tags=["conversation"])
# chat.py のサービスと WS の接続レジストリを共有する（実質シングルトン）。
orchestrator = ConversationOrchestrator(
    chat_service=chat_service,
    connection_manager=connection_manager,
    ack_timeout=get_settings().relay_ack_timeout,
)


@router.post("/api/conversation/start", response_model=ConversationStartResponse)
async def post_conversation_start(
    payload: ConversationStartRequest,
) -> ConversationStartResponse:
    """リレー会話を開始する（同時に1会話）。出力先に同じ実機を指定すれば 1 台で 2 体が話す。"""
    try:
        output_a, output_b = await orchestrator.start(
            device_a=payload.device_a,
            device_b=payload.device_b,
            opening_text=payload.opening_text,
            max_turns=payload.max_turns,
            output_a=payload.output_device_a,
            output_b=payload.output_device_b,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ConversationStartResponse(
        device_a=payload.device_a,
        device_b=payload.device_b,
        max_turns=payload.max_turns,
        output_device_a=output_a,
        output_device_b=output_b,
    )


@router.post("/api/conversation/chat", response_model=ConversationChatResponse)
async def post_conversation_chat(payload: ConversationChatRequest) -> ConversationChatResponse:
    """Web から実機のペルソナへ話しかけ、応答を実機で再生する（再生完了まで待って返す）。

    実機未接続は 409、LLM/TTS 失敗・再生 ack タイムアウトは 503。
    """
    try:
        text, output = await orchestrator.chat_once(
            persona_id=payload.persona_id,
            text=payload.text,
            output_device=payload.output_device,
            session_id=payload.session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DeviceNotConnectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ConversationChatResponse(persona_id=payload.persona_id, output_device=output, text=text)


@router.post("/api/conversation/stop")
async def post_conversation_stop() -> dict[str, str]:
    await orchestrator.stop()
    return {"status": "stopped"}


@router.get("/api/conversation/status", response_model=ConversationStatusResponse)
async def get_conversation_status() -> ConversationStatusResponse:
    return ConversationStatusResponse(**orchestrator.status())
