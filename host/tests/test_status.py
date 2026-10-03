"""GET /api/status の検証（バックエンド状態・ホストIP・進行中STTセッション）。"""

from fastapi.testclient import TestClient

from app.api import ws_audio
from app.main import app

client = TestClient(app)


def test_status_reports_backend_and_host_ip() -> None:
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["backend"]) == {"llm", "tts", "stt"}
    for value in payload["backend"].values():
        assert value in {"configured", "not-configured"}
    # network.ip は実IPまたはフォールバックの 127.0.0.1。空でないこと。
    assert payload["network"]["ip"]
    assert payload["network"]["wifi_connected"] is True


def test_status_active_session_id_tracks_open_stt_session() -> None:
    service = ws_audio.audio_session_service
    # 進行中セッションが無ければ None。
    assert client.get("/api/status").json()["active_session_id"] is None

    service.handle_session_start(session_id="sess-active", device_id="cli")
    try:
        assert client.get("/api/status").json()["active_session_id"] == "sess-active"
    finally:
        # audio.end 相当で破棄し、状態を元へ戻す。
        service._sessions.pop("sess-active", None)

    assert client.get("/api/status").json()["active_session_id"] is None
