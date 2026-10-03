"""GET /api/devices の検証（登録済みペルソナ一覧・空時のフォールバック）。"""

from fastapi.testclient import TestClient

from app.api import chat as chat_module
from app.api import device as device_module
from app.config import get_settings
from app.device_profiles import DeviceProfile
from app.main import app

client = TestClient(app)


def test_devices_falls_back_to_default_device_when_no_profiles() -> None:
    """device_profiles.json 未配置（空）なら既定 device_id 1件を返す。"""
    original = chat_module.chat_service.device_profiles
    chat_module.chat_service.device_profiles = {}
    try:
        payload = client.get("/api/devices").json()
    finally:
        chat_module.chat_service.device_profiles = original

    devices = payload["devices"]
    assert len(devices) == 1
    assert devices[0]["device_id"] == get_settings().device_id
    assert devices[0]["display_name"] == get_settings().device_id


def test_devices_lists_registered_profiles_with_display_name() -> None:
    """登録済みプロファイルを device_id / display_name で返す。"""
    original = chat_module.chat_service.device_profiles
    chat_module.chat_service.device_profiles = {
        "atoms3-001": DeviceProfile(display_name="げんき"),
        "atoms3-002": DeviceProfile(display_name=None),  # 未設定は device_id で代替
    }
    try:
        payload = client.get("/api/devices").json()
    finally:
        chat_module.chat_service.device_profiles = original

    devices = {d["device_id"]: d["display_name"] for d in payload["devices"]}
    assert devices == {"atoms3-001": "げんき", "atoms3-002": "atoms3-002"}


def test_devices_router_uses_same_chat_service_instance() -> None:
    """device ルーターは chat ルーターと同一の ChatService を参照する。"""
    assert device_module.chat_service is chat_module.chat_service
