import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class DeviceNotConnectedError(RuntimeError):
    """push 先の実機が WS 未接続。ルーターはバックエンド失敗（503）と区別して 409 にする。"""


class WebSocketLike(Protocol):
    """``send_json`` だけ使う最小インターフェース（テストでフェイクを差し込めるように）。"""

    async def send_json(self, data: Any) -> None: ...


class ConnectionManager:
    """``device_id`` ごとの WebSocket 接続を保持し、ホスト→デバイスの push を仲介する。

    リレー会話では orchestrator が ``send`` でデバイスへ ``play`` を押し込み、デバイスが
    返す ``played`` を ws ハンドラが ``resolve_ack`` で解決する。orchestrator は
    ``wait_ack`` でそれを待つ。接続はプロセス内メモリで管理する（実質シングルトン）。
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocketLike] = {}
        # (device_id, turn) -> Future。push したターンの完了通知を待つために使う。
        self._pending_acks: dict[tuple[str, int], asyncio.Future[None]] = {}

    def register(self, device_id: str, websocket: WebSocketLike) -> None:
        if not device_id:
            return
        previous = self._connections.get(device_id)
        if previous is not None and previous is not websocket:
            logger.info("[ws] device %s reconnected; replacing previous socket", device_id)
        self._connections[device_id] = websocket

    def unregister(self, device_id: str | None, websocket: WebSocketLike) -> None:
        """切断時に呼ぶ。登録中のソケットが当人のときだけ外す（新しい接続を消さない）。"""
        if not device_id:
            return
        if self._connections.get(device_id) is websocket:
            self._connections.pop(device_id, None)

    def is_connected(self, device_id: str) -> bool:
        return device_id in self._connections

    def connected_devices(self) -> list[str]:
        return sorted(self._connections.keys())

    async def send(self, device_id: str, message: dict[str, Any]) -> None:
        websocket = self._connections.get(device_id)
        if websocket is None:
            raise DeviceNotConnectedError(f"device not connected: {device_id}")
        await websocket.send_json(message)

    # ---- ターン完了（played）の待ち合わせ ----

    def expect_ack(self, device_id: str, turn: int) -> asyncio.Future[None]:
        """push 前に呼ぶ。``resolve_ack`` で解決される Future を作って返す。"""
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending_acks[(device_id, turn)] = future
        return future

    def resolve_ack(self, device_id: str | None, turn: int | None) -> None:
        """デバイスからの ``played`` を受けて該当ターンの待ちを解く。"""
        if not device_id or turn is None:
            return
        future = self._pending_acks.pop((device_id, turn), None)
        if future is not None and not future.done():
            future.set_result(None)

    def cancel_ack(self, device_id: str, turn: int) -> None:
        future = self._pending_acks.pop((device_id, turn), None)
        if future is not None and not future.done():
            future.cancel()


# ルーター/サービスから共有する実質シングルトン。
connection_manager = ConnectionManager()
