from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_frontend_index_is_served() -> None:
    response = client.get("/frontend/index.html")

    assert response.status_code == 200
    assert "AI Voice Atom Console" in response.text


def test_playback_stop_is_accepted() -> None:
    response = client.post(
        "/api/playback/stop",
        json={"device_id": "atoms3-001", "session_id": "sess-1"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
