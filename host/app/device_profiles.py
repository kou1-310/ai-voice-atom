"""デバイスごとのペルソナ・声色を定義する device_profiles.json のローダ。

複数台の AtomS3 を別個の AI として動かすため、``device_id`` ごとに system プロンプトと
話者リファレンス音声（ref_wav）を割り当てる。外部の設定ライブラリは使わず標準 ``json`` のみで
読み込む（プロジェクトの規約）。ファイルが無い/空なら空 dict を返し、全デバイスがグローバル設定
（``Settings.llm_system_prompt`` / ``tts_ref_wav``）にフォールバックする。
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

HOST_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DeviceProfile:
    """1 デバイス分のペルソナ設定。未設定の項目は None でグローバル設定にフォールバックする。

    ``system_prompt`` を直接書けば従来どおりそれを最優先で使う。``system_prompt`` を省き
    ``persona`` / ``speaking_style`` / ``interests`` / ``first_person`` を与えると、サーバ側が
    読み上げ向けスタイルを足して system プロンプトを合成する（自由文より破綻しにくい）。
    ``temperature`` などはこのペルソナ専用のサンプリング上書き、``opening_line`` はリレー会話で
    このペルソナが口火を切るときの一言。
    """

    system_prompt: str | None = None
    ref_wav: Path | None = None
    # 参照音声を使わずに声色を指定するための caption（自然文）。VoiceDesign チェックポイントへ
    # ``--caption`` として渡る（例: 「落ち着いた女性の声で、やわらかく読み上げて」）。ref_wav と
    # 併用すると「その声＋このスタイル」のクローンになる。未指定（None）なら既定の声。
    voice_caption: str | None = None
    display_name: str | None = None
    # 構造化ペルソナ（system_prompt 未指定時にこれらから合成する）。
    persona: str | None = None
    speaking_style: str | None = None
    first_person: str | None = None
    interests: tuple[str, ...] = ()
    # ペルソナ別サンプリング上書き（未指定はグローバル設定にフォールバック）。
    temperature: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    # リレー会話で、このペルソナが最初の話者のときに使う口火の一言。
    opening_line: str | None = None


def _resolve_ref_wav(value: str | None) -> Path | None:
    """ref_wav のパスを解決する（相対パスは host/ 基準）。空/未指定なら None。"""
    if value is None or not str(value).strip():
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = HOST_DIR / path
    return path


def _clean_str(value: object) -> str | None:
    """文字列を strip して非空なら返す。空/未指定/非文字列は None。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_interests(value: object) -> tuple[str, ...]:
    """interests を文字列タプルへ正規化する。list 以外や空要素は捨てる。"""
    if not isinstance(value, list | tuple):
        return ()
    items = [_clean_str(item) for item in value]
    return tuple(item for item in items if item)


def _clean_float(device_id: str, key: str, value: object) -> float | None:
    """サンプリング値を float へ。数値化できなければ警告して None（= グローバルに従う）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("[device-profiles] %s.%s must be a number; ignoring", device_id, key)
        return None


def load_device_profiles(path: Path) -> dict[str, DeviceProfile]:
    """device_profiles.json を読み込み ``device_id -> DeviceProfile`` の dict を返す。

    ファイルが存在しない場合は空 dict（= 全デバイスがグローバル設定で動く）。不正な JSON や
    想定外の構造はエラーログを出して空 dict を返し、サービス起動自体は止めない。
    """
    if not path.is_file():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[device-profiles] failed to load %s: %s", path, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("[device-profiles] %s must be a JSON object (device_id -> profile)", path)
        return {}

    profiles: dict[str, DeviceProfile] = {}
    for device_id, entry in raw.items():
        if not isinstance(entry, dict):
            logger.warning("[device-profiles] skipping %s: profile must be an object", device_id)
            continue
        device_key = str(device_id)
        profiles[device_key] = DeviceProfile(
            system_prompt=_clean_str(entry.get("system_prompt")),
            ref_wav=_resolve_ref_wav(entry.get("ref_wav")),
            voice_caption=_clean_str(entry.get("voice_caption")),
            display_name=entry.get("display_name"),
            persona=_clean_str(entry.get("persona")),
            speaking_style=_clean_str(entry.get("speaking_style")),
            first_person=_clean_str(entry.get("first_person")),
            interests=_clean_interests(entry.get("interests")),
            temperature=_clean_float(device_key, "temperature", entry.get("temperature")),
            presence_penalty=_clean_float(
                device_key, "presence_penalty", entry.get("presence_penalty")
            ),
            frequency_penalty=_clean_float(
                device_key, "frequency_penalty", entry.get("frequency_penalty")
            ),
            opening_line=_clean_str(entry.get("opening_line")),
        )

    logger.info("[device-profiles] loaded %d profile(s) from %s", len(profiles), path)
    return profiles
