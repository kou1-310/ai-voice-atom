import asyncio

import pytest

from app.services.connection_manager import ConnectionManager
from app.services.conversation_orchestrator import ConversationOrchestrator


class _FakeWebSocket:
    """play を受けたら自動で played ack を返すデバイスのフェイク。"""

    def __init__(self, manager: ConnectionManager, device_id: str, auto_ack: bool = True) -> None:
        self._manager = manager
        self._device_id = device_id
        self._auto_ack = auto_ack
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)
        if self._auto_ack and data.get("type") == "play":
            # 実機が再生完了で played を返すのを模す。
            self._manager.resolve_ack(self._device_id, data.get("turn"))

    def play_texts(self) -> list[str]:
        return [m["text"] for m in self.sent if m.get("type") == "play"]


class _FakeChatService:
    def __init__(self, opening_lines: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        # (device_id, partner_id, directive) を記録し、相手認識・フェーズ指示の検証に使う。
        self.kwargs: list[tuple[str, str | None, str | None]] = []
        self.recorded_openings: list[tuple[str, str]] = []
        self._opening_lines = opening_lines or {}

    async def generate_reply(
        self,
        device_id: str,
        session_id: str,
        input_text: str,
        partner_id: str | None = None,
        directive: str | None = None,
    ) -> str:
        self.calls.append((device_id, input_text))
        self.kwargs.append((device_id, partner_id, directive))
        return f"{device_id}:reply-to:{input_text}"

    def relay_opening_line(self, device_id: str) -> str | None:
        return self._opening_lines.get(device_id)

    def record_relay_opening(self, device_id: str, session_id: str, text: str) -> None:
        self.recorded_openings.append((device_id, text))


def _connect(manager: ConnectionManager, *device_ids: str) -> dict[str, _FakeWebSocket]:
    sockets = {}
    for device_id in device_ids:
        ws = _FakeWebSocket(manager, device_id)
        manager.register(device_id, ws)
        sockets[device_id] = ws
    return sockets


def test_relay_runs_turns_and_alternates_speakers() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        sockets = _connect(manager, "dev-a", "dev-b")
        chat = _FakeChatService()
        orch = ConversationOrchestrator(chat, manager)

        await orch.start("dev-a", "dev-b", opening_text="天気の話をして", max_turns=4)
        await orch._task

        # turn0: A が開始文をそのまま発話（LLM不使用）。turn1..3 は generate_reply。
        assert sockets["dev-a"].play_texts()[0] == "天気の話をして"
        assert len(sockets["dev-a"].play_texts()) == 2  # turn0, turn2
        assert len(sockets["dev-b"].play_texts()) == 2  # turn1, turn3
        # generate_reply は turn1..3 の3回（turn0は呼ばれない）。
        assert [c[0] for c in chat.calls] == ["dev-b", "dev-a", "dev-b"]
        # B の最初の入力は A の開始文。
        assert chat.calls[0][1] == "天気の話をして"
        assert orch.status()["running"] is False

    asyncio.run(scenario())


def test_relay_injects_partner_and_phase_directive() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        _connect(manager, "dev-a", "dev-b")
        chat = _FakeChatService()
        orch = ConversationOrchestrator(chat, manager)

        await orch.start("dev-a", "dev-b", opening_text="開始文", max_turns=3)
        await orch._task

        # turn0 は逐語（generate_reply は呼ばれない）→ 履歴に記録される（B2）。
        assert chat.recorded_openings == [("dev-a", "開始文")]
        # generate_reply は turn1(dev-b)・turn2(dev-a)。相手は常に「もう一方」（B1）。
        assert [(d, p) for d, p, _ in chat.kwargs] == [("dev-b", "dev-a"), ("dev-a", "dev-b")]
        # 最終ターン（turn2, 残り1）の directive に締めの指示が入る（B3）。
        assert "最後の発言" in (chat.kwargs[-1][2] or "")

    asyncio.run(scenario())


def test_relay_uses_persona_opening_line_when_no_opening_text() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        sockets = _connect(manager, "dev-a", "dev-b")
        chat = _FakeChatService(opening_lines={"dev-a": "やあ、はじめまして。"})
        orch = ConversationOrchestrator(chat, manager)

        # opening_text 省略でも、最初の話者のペルソナ口火を逐語で使う（A3）。
        await orch.start("dev-a", "dev-b", opening_text=None, max_turns=2)
        await orch._task

        assert sockets["dev-a"].play_texts()[0] == "やあ、はじめまして。"
        assert chat.recorded_openings == [("dev-a", "やあ、はじめまして。")]
        # turn0 は generate_reply を通さない → 呼び出しは turn1 の dev-b のみ。
        assert [d for d, _ in chat.calls] == ["dev-b"]

    asyncio.run(scenario())


def test_relay_generates_opening_when_no_text_or_persona_line() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        _connect(manager, "dev-a", "dev-b")
        chat = _FakeChatService()  # opening_line 無し
        orch = ConversationOrchestrator(chat, manager)

        await orch.start("dev-a", "dev-b", opening_text=None, max_turns=2)
        await orch._task

        # 口火が無ければ turn0 も generate_reply で生成する（逐語記録はされない）。
        assert chat.recorded_openings == []
        assert [d for d, _ in chat.calls] == ["dev-a", "dev-b"]
        # turn0 の directive は導入指示。
        assert "導入" in (chat.kwargs[0][2] or "")

    asyncio.run(scenario())


def test_relay_requires_both_devices_connected() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        _connect(manager, "dev-a")  # dev-b は未接続
        orch = ConversationOrchestrator(_FakeChatService(), manager)
        with pytest.raises(RuntimeError, match="not connected"):
            await orch.start("dev-a", "dev-b", opening_text=None, max_turns=2)

    asyncio.run(scenario())


def test_relay_rejects_same_device_and_bad_turns() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        _connect(manager, "dev-a", "dev-b")
        orch = ConversationOrchestrator(_FakeChatService(), manager)
        with pytest.raises(ValueError, match="must differ"):
            await orch.start("dev-a", "dev-a", opening_text=None, max_turns=2)
        with pytest.raises(ValueError, match="max_turns"):
            await orch.start("dev-a", "dev-b", opening_text=None, max_turns=0)

    asyncio.run(scenario())


def test_relay_aborts_on_ack_timeout() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        # auto_ack=False のデバイス: played を返さない → タイムアウトで中断。
        ws_a = _FakeWebSocket(manager, "dev-a", auto_ack=False)
        ws_b = _FakeWebSocket(manager, "dev-b", auto_ack=False)
        manager.register("dev-a", ws_a)
        manager.register("dev-b", ws_b)
        orch = ConversationOrchestrator(_FakeChatService(), manager, ack_timeout=0.05)

        await orch.start("dev-a", "dev-b", opening_text="やあ", max_turns=4)
        await orch._task

        # 最初のターンの play は送るが ack が来ず、以降は進まない。
        assert len(ws_a.play_texts()) == 1
        assert len(ws_b.play_texts()) == 0
        assert orch.status()["running"] is False

    asyncio.run(scenario())


def test_relay_rejects_concurrent_start() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        # auto_ack=False で会話を走らせ続け、2回目の start を弾く。
        ws_a = _FakeWebSocket(manager, "dev-a", auto_ack=False)
        ws_b = _FakeWebSocket(manager, "dev-b", auto_ack=False)
        manager.register("dev-a", ws_a)
        manager.register("dev-b", ws_b)
        orch = ConversationOrchestrator(_FakeChatService(), manager, ack_timeout=5.0)

        await orch.start("dev-a", "dev-b", opening_text="やあ", max_turns=4)
        try:
            with pytest.raises(ValueError, match="already running"):
                await orch.start("dev-a", "dev-b", opening_text="やあ", max_turns=4)
        finally:
            await orch.stop()

    asyncio.run(scenario())


def test_connection_manager_register_and_ack() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        ws = _FakeWebSocket(manager, "dev-a", auto_ack=False)
        manager.register("dev-a", ws)
        assert manager.is_connected("dev-a")
        assert manager.connected_devices() == ["dev-a"]

        future = manager.expect_ack("dev-a", 3)
        assert not future.done()
        manager.resolve_ack("dev-a", 3)
        await asyncio.wait_for(future, timeout=1.0)
        assert future.done()

        # 別ソケットでの unregister は現行接続を消さない。
        other = _FakeWebSocket(manager, "dev-a", auto_ack=False)
        manager.unregister("dev-a", other)
        assert manager.is_connected("dev-a")
        manager.unregister("dev-a", ws)
        assert not manager.is_connected("dev-a")

    asyncio.run(scenario())


def test_relay_single_device_plays_both_personas() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        # 実機は 1 台だけ。ペルソナ A/B の発話をどちらもこの実機で鳴らす。
        sockets = _connect(manager, "atom-1")
        chat = _FakeChatService()
        orch = ConversationOrchestrator(chat, manager)

        outputs = await orch.start(
            "persona-a",
            "persona-b",
            opening_text="やあ",
            max_turns=4,
            output_a="atom-1",
            output_b="atom-1",
        )
        await orch._task

        assert outputs == ("atom-1", "atom-1")
        plays = [m for m in sockets["atom-1"].sent if m.get("type") == "play"]
        # 4 ターンすべてが 1 台に届き、声色のペルソナが交互に指定される。
        assert [m["persona"] for m in plays] == ["persona-a", "persona-b"] * 2
        assert [m["turn"] for m in plays] == [0, 1, 2, 3]
        # 応答の生成はペルソナ単位（履歴・system もペルソナで分かれる）。
        assert [c[0] for c in chat.calls] == ["persona-b", "persona-a", "persona-b"]
        status = orch.status()
        assert (status["output_device_a"], status["output_device_b"]) == ("atom-1", "atom-1")

    asyncio.run(scenario())


def test_relay_output_defaults_to_persona_device() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        sockets = _connect(manager, "dev-a", "dev-b")
        orch = ConversationOrchestrator(_FakeChatService(), manager)

        outputs = await orch.start("dev-a", "dev-b", opening_text="やあ", max_turns=2)
        await orch._task

        # 出力先省略 = 従来の 2 台構成。play には自分自身のペルソナが載る。
        assert outputs == ("dev-a", "dev-b")
        assert sockets["dev-a"].sent[0]["persona"] == "dev-a"
        assert sockets["dev-b"].sent[0]["persona"] == "dev-b"

    asyncio.run(scenario())


def test_relay_requires_output_device_connected() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        _connect(manager, "atom-1")
        orch = ConversationOrchestrator(_FakeChatService(), manager)
        # ペルソナ自体の実機は不要だが、出力先の実機は接続されていなければならない。
        with pytest.raises(RuntimeError, match="not connected: atom-2"):
            await orch.start(
                "p-a", "p-b", opening_text=None, max_turns=2, output_a="atom-1", output_b="atom-2"
            )

    asyncio.run(scenario())


def test_chat_once_replies_and_plays_on_output_device() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        sockets = _connect(manager, "atom-1")
        chat = _FakeChatService()
        orch = ConversationOrchestrator(chat, manager)

        text, output = await orch.chat_once("persona-a", " こんにちは ", output_device="atom-1")

        assert (text, output) == ("persona-a:reply-to:こんにちは", "atom-1")
        (play,) = sockets["atom-1"].sent
        assert play["type"] == "play"
        assert play["persona"] == "persona-a"
        assert play["text"] == text
        # リレーのターン番号と衝突しない番号で ack を待ち合わせる。
        assert play["turn"] > 20
        # 相手・フェーズ指示は付けない（通常の 1 対 1 の応答）。
        assert chat.kwargs == [("persona-a", None, None)]
        assert orch.status()["chatting"] is False

    asyncio.run(scenario())


def test_chat_once_validates_input_and_connection() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        _connect(manager, "atom-1")
        orch = ConversationOrchestrator(_FakeChatService(), manager)
        with pytest.raises(ValueError, match="empty"):
            await orch.chat_once("persona-a", "  ", output_device="atom-1")
        # 出力先省略時はペルソナと同じ ID の実機。未接続なら RuntimeError。
        with pytest.raises(RuntimeError, match="not connected: persona-a"):
            await orch.chat_once("persona-a", "やあ")

    asyncio.run(scenario())


def test_chat_once_rejected_while_relay_running() -> None:
    async def scenario() -> None:
        manager = ConnectionManager()
        ws = _FakeWebSocket(manager, "atom-1", auto_ack=False)
        manager.register("atom-1", ws)
        orch = ConversationOrchestrator(_FakeChatService(), manager, ack_timeout=5.0)

        await orch.start(
            "p-a", "p-b", opening_text="やあ", max_turns=4, output_a="atom-1", output_b="atom-1"
        )
        try:
            with pytest.raises(ValueError, match="already running"):
                await orch.chat_once("p-a", "やあ", output_device="atom-1")
        finally:
            await orch.stop()

    asyncio.run(scenario())
