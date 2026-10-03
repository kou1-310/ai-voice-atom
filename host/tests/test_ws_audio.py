import base64

from fastapi.testclient import TestClient

from app.main import app
from app.services.audio_session_service import AudioSessionService

client = TestClient(app)


class _FakeMoonshineAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        self.calls.append({"pcm_bytes": pcm_bytes, "sample_rate": sample_rate})
        return "こんにちは 世界"


def test_ws_audio_returns_final_transcript(monkeypatch) -> None:
    fake_adapter = _FakeMoonshineAdapter()
    monkeypatch.setattr(
        "app.api.ws_audio.audio_session_service",
        AudioSessionService(stt_adapter=fake_adapter),
    )

    pcm_chunk_1 = (1000).to_bytes(2, "little", signed=True)
    pcm_chunk_2 = (-1000).to_bytes(2, "little", signed=True)

    with client.websocket_connect("/ws/audio") as websocket:
        websocket.send_json(
            {"type": "session.start", "session_id": "sess-1", "device_id": "atoms3-001"}
        )
        ack = websocket.receive_json()
        assert ack == {
            "type": "session.ack",
            "session_id": "sess-1",
            "device_id": "atoms3-001",
            "accepted": True,
        }

        websocket.send_json(
            {
                "type": "audio.chunk",
                "session_id": "sess-1",
                "data": base64.b64encode(pcm_chunk_1).decode("ascii"),
            }
        )
        chunk_ack = websocket.receive_json()
        assert chunk_ack == {
            "type": "event.ack",
            "session_id": "sess-1",
            "accepted": True,
            "received_type": "audio.chunk",
        }

        websocket.send_json(
            {
                "type": "audio.chunk",
                "session_id": "sess-1",
                "data": base64.b64encode(pcm_chunk_2).decode("ascii"),
            }
        )
        websocket.receive_json()

        websocket.send_json({"type": "audio.end", "session_id": "sess-1"})
        final_message = websocket.receive_json()

    assert final_message == {
        "type": "stt.final",
        "session_id": "sess-1",
        "text": "こんにちは 世界",
        "is_final": True,
    }
    assert fake_adapter.calls == [{"pcm_bytes": pcm_chunk_1 + pcm_chunk_2, "sample_rate": 16000}]
