from fastapi import APIRouter

from app.models.schemas import PlaybackStopRequest, PlaybackStopResponse
from app.services.playback_service import PlaybackService

router = APIRouter(tags=["playback"])
playback_service = PlaybackService()


@router.post("/api/playback/stop", response_model=PlaybackStopResponse)
async def post_playback_stop(payload: PlaybackStopRequest) -> PlaybackStopResponse:
    _ = payload
    return PlaybackStopResponse(**playback_service.stop())
