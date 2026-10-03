"""API エラー応答形式の統一。

全エンドポイントのエラー応答を ``{"error": {"code": ..., "message": ...}}`` に
そろえる。ルーター側は従来どおり ``HTTPException``（``ValueError`` → 400 /
``RuntimeError`` → 503 のマッピング）を投げてよく、ここで HTTP ステータスを
``code`` へ変換する。個別コードを明示したい箇所では ``ApiError`` を使う。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# HTTP ステータス → エラーコードの既定マッピング。
# 個別の事情があるものは ApiError で明示的に上書きする。
_CODE_BY_STATUS: dict[int, str] = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "PLAYBACK_BUSY",
    422: "INVALID_REQUEST",
    500: "INTERNAL_ERROR",
    503: "BACKEND_UNAVAILABLE",
    504: "TIMEOUT",
}


class ApiError(Exception):
    """エラーコードを明示したい場合に投げる例外。

    例: ``raise ApiError(400, "INVALID_AUDIO_FORMAT", "Only wav output is supported")``
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def _code_for_status(status_code: int) -> str:
    return _CODE_BY_STATUS.get(status_code, "INTERNAL_ERROR")


def register_error_handlers(app: FastAPI) -> None:
    """統一エラー応答用の例外ハンドラを登録する。"""

    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(_code_for_status(exc.status_code), detail),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # バリデーション失敗は 422。詳細は message に要約し errors に原文を添える。
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed",
                    "errors": jsonable_encoder(exc.errors()),
                }
            },
        )
