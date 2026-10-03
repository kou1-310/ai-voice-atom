import base64
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response

from app.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse, SayRequest
from app.services.chat_service import ChatService

router = APIRouter(tags=["chat"])
chat_service = ChatService(settings=get_settings())


@router.post("/api/chat", response_model=ChatResponse)
async def post_chat(payload: ChatRequest) -> ChatResponse:
    try:
        return await chat_service.handle_chat(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/chat/audio")
async def post_chat_audio(payload: ChatRequest) -> Response:
    """LLM 応答を 16kHz/mono の WAV バイナリとして返す（実機ストリーミング再生用）。

    応答テキストはヘッダ ``X-LLM-Text``(URLエンコード) / ``X-LLM-Text-B64``(base64 UTF-8)
    に格納する。ボディは純粋な audio/wav なのでデバイスは逐次再生できる。
    """
    try:
        llm_text, wav_bytes = await chat_service.handle_chat_audio(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-LLM-Text": quote(llm_text),
            "X-LLM-Text-B64": base64.b64encode(llm_text.encode("utf-8")).decode("ascii"),
            "Content-Length": str(len(wav_bytes)),
        },
    )


@router.post("/api/chat/say")
async def post_chat_say(payload: SayRequest) -> Response:
    """指定テキストを device_id の声色で TTS した 16kHz/mono WAV を返す（LLM は通さない）。

    リレー会話で、ホストから ``play`` を push されたデバイスが自分のターンの音声を
    取得するために叩く。``/api/chat/audio`` と同じく純粋な audio/wav バイナリを返す。
    """
    try:
        # persona_id があればその声色で（実機1台で複数ペルソナを鳴らす場合）。
        voice_id = payload.persona_id or payload.device_id
        wav_bytes = await chat_service.synthesize_say(voice_id, payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Content-Length": str(len(wav_bytes))},
    )
