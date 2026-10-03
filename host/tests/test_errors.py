"""統一エラー応答形式 ``{"error": {"code", "message"}}`` の検証。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_invalid_audio_format_returns_unified_error() -> None:
    # ApiError 経由（個別コードを明示する経路）。
    response = client.post(
        "/api/tts",
        json={
            "device_id": "atoms3-001",
            "session_id": "sess-1",
            "text": "テスト",
            "voice": "default",
            "audio": {"format": "mp3", "sample_rate": 16000},
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_AUDIO_FORMAT",
            "message": "Only wav output is supported",
        }
    }


def test_validation_error_returns_unified_error() -> None:
    # 型不正（audio がオブジェクトでない） → 422。FastAPI のバリデーション失敗を統一形式へ変換する。
    response = client.post(
        "/api/tts",
        json={"device_id": "atoms3-001", "text": "テスト", "audio": "invalid"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "INVALID_REQUEST"
    assert payload["error"]["message"] == "Request validation failed"
    assert isinstance(payload["error"]["errors"], list)


def test_backend_unavailable_returns_unified_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # アダプターが RuntimeError → 503 BACKEND_UNAVAILABLE。
    def fake_synthesize(*args: object, **kwargs: object) -> object:
        raise RuntimeError("TTS backend unavailable")

    monkeypatch.setattr("app.api.tts.tts_adapter.synthesize", fake_synthesize)

    response = client.post(
        "/api/tts",
        json={
            "device_id": "atoms3-001",
            "session_id": "sess-1",
            "text": "テスト",
            "voice": "default",
            "audio": {"format": "wav", "sample_rate": 16000},
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "BACKEND_UNAVAILABLE",
            "message": "TTS backend unavailable",
        }
    }
