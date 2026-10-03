import asyncio
import contextlib
import logging
import time

from app.services.connection_manager import ConnectionManager, DeviceNotConnectedError

logger = logging.getLogger(__name__)

# opening_text 未指定のとき、最初の話者に渡す既定のキックオフ入力。
DEFAULT_KICKOFF = "こんにちは。短く自己紹介して、会話を始めてください。"

# Web チャット（chat_once）の play に振るターン番号の起点。ack は (device_id, turn) で
# 待ち合わせるので、リレー会話のターン番号（0 始まり・max 20）と衝突しない範囲を使う。
CHAT_TURN_BASE = 10000


class ConversationOrchestrator:
    """実機のリレー会話と、Web からの 1 往復チャットを統括する。

    ホストがターンを回し、各ターンのテキストを ``ChatService`` で生成して話者デバイスへ
    ``play`` を push、デバイスの ``played`` を待ってから次のターンへ進む。デバイス同士は
    ホスト経由でテキストを正確に受け渡すので、空気越し STT のような劣化がない。同時に動く
    会話（リレー or チャット）は1つ（プロセス内の単一インスタンス）。

    話者（ペルソナ）と鳴らす実機は分けて指定できる。``play`` に ``persona`` を載せると、実機は
    そのペルソナの声色で ``/api/chat/say`` から音声を取得する。これで実機 1 台でも 2 体の
    会話を再生できる（出力先を省略すればペルソナと同じ ID の実機 = 従来の 2 台構成）。
    """

    def __init__(
        self,
        chat_service: object,
        connection_manager: ConnectionManager,
        ack_timeout: float = 70.0,
    ) -> None:
        self._chat = chat_service
        self._conn = connection_manager
        self._ack_timeout = ack_timeout
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._running = False
        self._device_a: str | None = None
        self._device_b: str | None = None
        self._output_a: str | None = None
        self._output_b: str | None = None
        self._turn = 0
        self._max_turns = 0
        self._chatting = False
        self._chat_turn = CHAT_TURN_BASE

    def status(self) -> dict:
        return {
            "running": self._running,
            "chatting": self._chatting,
            "device_a": self._device_a,
            "device_b": self._device_b,
            "output_device_a": self._output_a,
            "output_device_b": self._output_b,
            "turn": self._turn,
            "max_turns": self._max_turns,
            "connected_devices": self._conn.connected_devices(),
        }

    async def start(
        self,
        device_a: str,
        device_b: str,
        opening_text: str | None,
        max_turns: int,
        output_a: str | None = None,
        output_b: str | None = None,
    ) -> tuple[str, str]:
        """会話を開始し、実際に使う出力先 (output_a, output_b) を返す。"""
        async with self._lock:
            if self._running or self._chatting:
                raise ValueError("conversation already running")
            if device_a == device_b:
                raise ValueError("device_a and device_b must differ")
            if max_turns <= 0:
                raise ValueError("max_turns must be positive")
            out_a = output_a or device_a
            out_b = output_b or device_b
            for device_id in dict.fromkeys((out_a, out_b)):
                if not self._conn.is_connected(device_id):
                    raise DeviceNotConnectedError(f"device not connected: {device_id}")

            self._device_a = device_a
            self._device_b = device_b
            self._output_a = out_a
            self._output_b = out_b
            self._max_turns = max_turns
            self._turn = 0
            self._running = True
            self._stop.clear()
            outputs = {device_a: out_a, device_b: out_b}
            self._task = asyncio.create_task(
                self._run(device_a, device_b, opening_text, max_turns, outputs)
            )
            return out_a, out_b

    async def chat_once(
        self,
        persona_id: str,
        text: str,
        output_device: str | None = None,
        session_id: str = "webchat",
    ) -> tuple[str, str]:
        """Web から話しかけた ``text`` に ``persona_id`` が応答し、実機で再生する。

        再生完了（played ack）または停止まで待ってから (応答テキスト, 出力先) を返す。
        リレー会話と同時には動かさない（実機のスピーカーと ack の待ち合わせが 1 系統のため）。
        """
        normalized = text.strip()
        if not normalized:
            raise ValueError("text must not be empty")
        output = output_device or persona_id
        async with self._lock:
            if self._running or self._chatting:
                raise ValueError("conversation already running")
            if not self._conn.is_connected(output):
                raise DeviceNotConnectedError(f"device not connected: {output}")
            self._chatting = True
            self._stop.clear()
            self._chat_turn += 1
            turn = self._chat_turn

        try:
            reply = await self._chat.generate_reply(persona_id, session_id, normalized)
            logger.info("[chat] %s -> %s: %s", persona_id, output, reply)
            if not self._stop.is_set():
                await self._play_on(output, reply, turn, persona=persona_id)
            return reply, output
        finally:
            self._chatting = False

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(
        self,
        device_a: str,
        device_b: str,
        opening_text: str | None,
        max_turns: int,
        outputs: dict[str, str],
    ) -> None:
        session_id = f"relay-{int(time.time() * 1000)}"
        speakers = (device_a, device_b)
        # 各話者が前回自分で言った台詞。反復ガード（B4）で「前回と違う切り口で」と促すのに使う。
        last_by_speaker: dict[str, str | None] = {device_a: None, device_b: None}
        current_text = (opening_text or "").strip()
        try:
            for turn in range(max_turns):
                if self._stop.is_set():
                    break
                speaker = speakers[turn % 2]
                partner = speakers[(turn + 1) % 2]
                self._turn = turn + 1

                opening = current_text or self._opening_line_for(speaker) if turn == 0 else ""
                if turn == 0 and opening:
                    # 開始文/ペルソナの口火があれば、話者Aの最初の台詞として逐語で使う。
                    # B2: LLM を通さなくても「自分が口火を切った」ことを履歴に残す。
                    utterance = opening
                    self._chat.record_relay_opening(speaker, session_id, utterance)
                else:
                    seed = current_text or DEFAULT_KICKOFF
                    directive = self._directive(turn, max_turns, last_by_speaker[speaker])
                    utterance = await self._chat.generate_reply(
                        speaker, session_id, seed, partner_id=partner, directive=directive
                    )

                if self._stop.is_set():
                    break
                logger.info("[relay] turn %d/%d %s: %s", turn + 1, max_turns, speaker, utterance)
                await self._play_on(outputs[speaker], utterance, turn, persona=speaker)
                current_text = utterance
                last_by_speaker[speaker] = utterance
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 1ターンの失敗で会話全体を止め、状態を戻す
            logger.warning("[relay] conversation aborted: %s", exc)
        finally:
            self._running = False

    def _opening_line_for(self, device_id: str) -> str:
        """ペルソナに口火（opening_line）が設定されていれば返す（A3）。無ければ空文字。"""
        getter = getattr(self._chat, "relay_opening_line", None)
        if getter is None:
            return ""
        return (getter(device_id) or "").strip()

    @staticmethod
    def _directive(turn: int, max_turns: int, own_last: str | None) -> str | None:
        """ターンごとの会話指示を組み立てる（B3 フェーズ + B4 反復ガード）。"""
        parts: list[str] = []
        remaining = max_turns - turn  # このターンを含む残り発話数
        if turn == 0:
            parts.append("会話の導入です。相手に軽く話題を振って自然に始めてください。")
        elif remaining <= 1:
            parts.append(
                "これが最後の発言です。話をやさしく締めくくり、会話を自然に終えてください。"
            )
        elif remaining == 2:
            parts.append("そろそろ会話を締めに向かわせてください。")
        else:
            # 中間ターンは相手の話題を引き継いで深掘りさせ、話題がころころ飛ぶ脱線を防ぐ（B3）。
            parts.append(
                "相手の直前の発言の話題を引き継いで返してください。"
                "新しい話題には飛ばず、いまの話を一歩だけ掘り下げます。"
            )
        if own_last:
            # B4 反復ガード: エコー崩壊（自分の言い回しの逐語反復）だけを避けさせる。
            # 以前は「違う切り口・新しい話題で」と促していたが、これが脱線の一因だったため
            # 話題転換は求めず、言い回しの重複回避に限定する。
            parts.append(
                f"ただし、あなたの前回の発言『{own_last}』と同じ言い回しの繰り返しは避けてください。"
            )
        return "\n".join(parts) if parts else None

    async def _play_on(
        self, device_id: str, text: str, turn: int, persona: str | None = None
    ) -> None:
        """実機へ ``play`` を push し、``played`` ack（または停止）を待つ。

        ``persona`` は声色を借りるペルソナ。実機はこれを ``/api/chat/say`` の ``persona_id`` に
        載せて、自分以外のペルソナの声でも再生できる。
        """
        future = self._conn.expect_ack(device_id, turn)
        message: dict[str, object] = {"type": "play", "text": text, "turn": turn}
        if persona:
            message["persona"] = persona
        try:
            await self._conn.send(device_id, message)
        except Exception:
            self._conn.cancel_ack(device_id, turn)
            raise

        stop_wait = asyncio.ensure_future(self._stop.wait())
        try:
            done, _ = await asyncio.wait(
                {future, stop_wait},
                timeout=self._ack_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_wait.cancel()

        if future in done:
            return
        # ack が来ないまま timeout もしくは stop。待ちを破棄する。
        self._conn.cancel_ack(device_id, turn)
        if self._stop.is_set():
            return
        raise RuntimeError(f"playback ack timeout from {device_id} (turn {turn})")
