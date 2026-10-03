import socket

from fastapi import APIRouter

from app.api.chat import chat_service
from app.api.ws_audio import audio_session_service
from app.config import get_settings
from app.models.schemas import (
    AudioStatus,
    BackendStatus,
    DevicesResponse,
    DeviceSummary,
    NetworkStatus,
    StatusResponse,
)

router = APIRouter(tags=["device"])


def _detect_host_ip() -> str:
    """ホストの LAN IP を推定する（実機が接続すべき宛先の確認用）。

    既定ルート向けの UDP ソケットを生成するだけでパケットは送らない。
    ルートが無い等で取得できなければ ``127.0.0.1`` を返す。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"


@router.get("/api/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    settings = get_settings()
    llm_state = "configured" if settings.llm_configured else "not-configured"
    tts_state = "configured" if settings.tts_configured else "not-configured"
    stt_state = "configured" if settings.stt_configured else "not-configured"
    return StatusResponse(
        device_id=settings.device_id,
        mode=settings.default_mode,
        network=NetworkStatus(wifi_connected=True, ip=_detect_host_ip()),
        # 再生はデバイス側が担うためホストは進行状況を保持しない（既定値）。
        audio=AudioStatus(),
        backend=BackendStatus(
            llm=llm_state,
            tts=tts_state,
            stt=stt_state,
        ),
        active_session_id=audio_session_service.active_session_id,
    )


@router.get("/api/devices", response_model=DevicesResponse)
async def get_devices() -> DevicesResponse:
    """登録済みデバイスプロファイル（ペルソナ）の一覧。フロントの選択肢用。

    ``device_profiles.json`` 未配置でプロファイルが空のときは、UI が必ず1体は選べるよう
    既定の ``settings.device_id`` 1件にフォールバックする。
    """
    profiles = chat_service.device_profiles
    if profiles:
        devices = [
            DeviceSummary(
                device_id=device_id,
                display_name=profile.display_name or device_id,
            )
            for device_id, profile in profiles.items()
        ]
    else:
        fallback_id = get_settings().device_id
        devices = [DeviceSummary(device_id=fallback_id, display_name=fallback_id)]
    return DevicesResponse(devices=devices)
