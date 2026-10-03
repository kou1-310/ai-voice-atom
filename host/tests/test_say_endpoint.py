"""POST /api/chat/say と GET /api/conversation/status の検証。"""

import asyncio

from fastapi.testclient import TestClient

from app.api import chat as chat_module
from app.main import app
from app.services.connection_manager import connection_manager

client = TestClient(app)


def test_say_returns_wav_for_device_voice() -> None:
    async def _fake_say(device_id: str, text: str) -> bytes:
        assert device_id == "atoms3-002"
        assert text == "やあ"
        return b"RIFF....WAVEfake"

    original = chat_module.chat_service.synthesize_say
    chat_module.chat_service.synthesize_say = _fake_say  # type: ignore[assignment]
    try:
        resp = client.post("/api/chat/say", json={"device_id": "atoms3-002", "text": "やあ"})
    finally:
        chat_module.chat_service.synthesize_say = original  # type: ignore[assignment]

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == b"RIFF....WAVEfake"


def test_say_uses_persona_voice_when_given() -> None:
    seen: list[str] = []

    async def _fake_say(device_id: str, text: str) -> bytes:
        seen.append(device_id)
        return b"RIFF....WAVEfake"

    original = chat_module.chat_service.synthesize_say
    chat_module.chat_service.synthesize_say = _fake_say  # type: ignore[assignment]
    try:
        # 実機 atoms3-001 が、ペルソナ atoms3-002 の声色で鳴らす（実機 1 台で 2 体の会話）。
        resp = client.post(
            "/api/chat/say",
            json={"device_id": "atoms3-001", "text": "やあ", "persona_id": "atoms3-002"},
        )
    finally:
        chat_module.chat_service.synthesize_say = original  # type: ignore[assignment]

    assert resp.status_code == 200
    assert seen == ["atoms3-002"]


def test_say_rejects_empty_text() -> None:
    async def _fake_say(device_id: str, text: str) -> bytes:
        raise ValueError("text must not be empty")

    original = chat_module.chat_service.synthesize_say
    chat_module.chat_service.synthesize_say = _fake_say  # type: ignore[assignment]
    try:
        resp = client.post("/api/chat/say", json={"device_id": "atoms3-002", "text": "  "})
    finally:
        chat_module.chat_service.synthesize_say = original  # type: ignore[assignment]

    assert resp.status_code == 400


def test_conversation_status_reports_connected_devices() -> None:
    class _Sock:
        async def send_json(self, data: object) -> None: ...

    sock = _Sock()
    connection_manager.register("atoms3-009", sock)
    try:
        payload = client.get("/api/conversation/status").json()
    finally:
        connection_manager.unregister("atoms3-009", sock)

    assert payload["running"] is False
    assert "atoms3-009" in payload["connected_devices"]


def test_conversation_start_rejects_unconnected_device() -> None:
    asyncio.get_event_loop  # noqa: B018 — TestClient は同期だが start は接続チェックで弾く
    resp = client.post(
        "/api/conversation/start",
        json={"device_a": "nope-a", "device_b": "nope-b", "max_turns": 2},
    )
    # 未接続デバイスは 409。
    assert resp.status_code == 409


def test_conversation_chat_rejects_unconnected_device() -> None:
    resp = client.post(
        "/api/conversation/chat",
        json={"persona_id": "nope-a", "text": "やあ", "output_device": "nope-x"},
    )
    assert resp.status_code == 409


def test_conversation_chat_rejects_empty_text() -> None:
    resp = client.post("/api/conversation/chat", json={"persona_id": "nope-a", "text": " "})
    assert resp.status_code == 400


def test_conversation_chat_maps_llm_failure_to_503() -> None:
    from app.api import conversation as conversation_module

    class _Sock:
        async def send_json(self, data: object) -> None: ...

    async def _failing_reply(*args: object, **kwargs: object) -> str:
        raise RuntimeError("OpenAI completion timed out")

    orch = conversation_module.orchestrator
    original = orch._chat
    orch._chat = type("_Chat", (), {"generate_reply": staticmethod(_failing_reply)})()
    sock = _Sock()
    connection_manager.register("atoms3-008", sock)
    try:
        resp = client.post(
            "/api/conversation/chat",
            json={"persona_id": "atoms3-008", "text": "やあ"},
        )
    finally:
        connection_manager.unregister("atoms3-008", sock)
        orch._chat = original

    # バックエンド（LLM）失敗は実機の競合（409）ではなく 503。
    assert resp.status_code == 503
    assert orch.status()["chatting"] is False
