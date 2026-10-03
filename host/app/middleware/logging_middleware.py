import json
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("ai_voice_atom")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        latency_ms = round((time.monotonic() - start) * 1000)

        session_id = request.headers.get("x-session-id") or _extract_session_id(request)
        record = {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": latency_ms,
        }
        if session_id:
            record["session_id"] = session_id

        logger.info(json.dumps(record, ensure_ascii=False))
        return response


def _extract_session_id(request: Request) -> str | None:
    # Best-effort extraction from query string; body is not re-readable here.
    return request.query_params.get("session_id")
