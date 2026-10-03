import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_DIR = Path(__file__).resolve().parents[1]

# 音声デバイス向けの既定システムプロンプト。応答は読み上げられて短時間で再生されるため、
# 実際の会話と同程度に短く・口語的な返答を促す。空文字を設定すると system は送らない。
DEFAULT_LLM_SYSTEM_PROMPT = (
    "あなたは音声で会話するアシスタントです。応答はそのまま読み上げられます。"
    "実際の会話のように、1〜2文・40文字程度を目安に短く自然な口語で答えてください。"
    "箇条書き・見出し・記号・絵文字・URL は使わず、要点だけを簡潔に話します。"
    "前置きや補足説明は省き、聞かれたことに端的に答えます。"
    "ユーザーがもっと詳しく知りたい場合だけ、続けて説明を加えます。"
)

# 構造化ペルソナ（device_profiles.json の persona/speaking_style 等）から system プロンプトを
# 合成する際に末尾へ付ける共通の音声向けスタイル制約。自由文の system_prompt を書く手間を
# 省きつつ、読み上げ前提の短さ・記号禁止などを必ず効かせる。
COMMON_VOICE_STYLE = (
    "応答はそのまま読み上げられます。1〜2文・40文字程度を目安に、短く自然な口語で答えてください。"
    "記号・絵文字・URL は使いません。"
)

# 対話ガイダンス。ペルソナ/グローバルの system プロンプトの末尾に連結し、相手の発言を
# そのまま繰り返す「エコー崩壊」（小型モデルで顕著）を防ぎ、毎ターン会話を前に進めさせる。
# 空文字を設定すると連結しない（= 対話誘導を無効化）。
DEFAULT_LLM_DIALOGUE_GUIDANCE = (
    "これは相手と交互に続く会話です。相手の発言をそのまま繰り返したり、"
    "「そうだね」「いいね」など同意だけで終えたりしないでください。"
    "まず相手がいま話している話題に具体的に反応し、その話題を一歩だけ深めます"
    "（短い感想・自分の体験・相手への質問のうち一つを返す）。"
    "話題を変えるのは会話が一区切りしたときだけにし、"
    "一度の発言で新しい話題をいくつも持ち出さないでください。"
    "自分の性格や口調は保ったまま、自然な相づちは最小限にします。"
)


def _load_env_file(path: Path) -> None:
    """`.env` を os.environ へ読み込む（既存の環境変数は上書きしない）。

    uvicorn の ``--env-file`` や明示的な環境変数を優先するため、すでに設定済みの
    キーはスキップする。python-dotenv に依存しない最小実装（標準ライブラリのみ）。
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


# モジュール読み込み時に host/.env を自動ロードする。
# 既存の環境変数や uvicorn --env-file が優先される（上書きしない）。
_load_env_file(Path(os.getenv("AI_VOICE_ATOM_ENV_FILE", str(HOST_DIR / ".env"))))


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default

    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float_opt(name: str, default: float | None) -> float | None:
    """オプションの float 設定。未設定なら default、空文字なら None（= パラメータを送らない）。"""
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if not value:
        return None
    return float(value)


@dataclass(frozen=True)
class Settings:
    device_id: str
    default_mode: str
    host: str
    port: int
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str
    llm_api_key_present: bool
    llm_timeout: float
    llm_reasoning_effort: str | None
    tts_root_path: Path
    tts_timeout: float
    tts_server_url: str | None
    tts_server_autostart: bool
    tts_ref_wav: Path
    relay_ack_timeout: float
    device_profiles_path: Path
    moonshine_root_path: Path
    moonshine_cache_path: Path
    stt_model_language: str
    include_audio_inline: bool
    response_prefix: str
    history_max_turns: int
    history_ttl_sec: float
    llm_system_prompt: str | None
    llm_dialogue_guidance: str | None
    llm_max_tokens: int
    llm_temperature: float | None
    llm_presence_penalty: float | None
    llm_frequency_penalty: float | None

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key_present)

    @property
    def tts_configured(self) -> bool:
        return self.tts_root_path.exists()

    @property
    def stt_configured(self) -> bool:
        return self.moonshine_root_path.exists()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        device_id=os.getenv("AI_VOICE_ATOM_DEVICE_ID", "atoms3-001"),
        default_mode=os.getenv("AI_VOICE_ATOM_DEFAULT_MODE", "web"),
        host=os.getenv("AI_VOICE_ATOM_HOST", "0.0.0.0"),
        port=int(os.getenv("AI_VOICE_ATOM_PORT", "8000")),
        llm_base_url=os.getenv("AI_VOICE_ATOM_LLM_BASE_URL"),
        llm_api_key=os.getenv("AI_VOICE_ATOM_LLM_API_KEY"),
        llm_model=os.getenv("AI_VOICE_ATOM_LLM_MODEL", "gpt-compatible-model"),
        llm_api_key_present=bool(os.getenv("AI_VOICE_ATOM_LLM_API_KEY")),
        llm_timeout=float(os.getenv("AI_VOICE_ATOM_LLM_TIMEOUT", "30")),
        llm_reasoning_effort=(
            os.getenv("AI_VOICE_ATOM_LLM_REASONING_EFFORT", "none").strip() or None
        ),
        tts_root_path=_env_path(
            "AI_VOICE_ATOM_IRODORI_ROOT",
            REPO_ROOT / "third_party" / "irodori-tts",
        ),
        tts_timeout=float(os.getenv("AI_VOICE_ATOM_TTS_TIMEOUT", "120")),
        # 常駐 TTS サイドカーの URL（空にすると毎回 infer.py を subprocess 実行）
        tts_server_url=(
            os.getenv("AI_VOICE_ATOM_TTS_SERVER_URL", "http://127.0.0.1:8770").strip() or None
        ),
        tts_server_autostart=_env_bool("AI_VOICE_ATOM_TTS_SERVER_AUTOSTART", True),
        # 話者リファレンス音声の置き場所。ここに wav を置くとその声色で合成する（無ければ既定の声）。
        tts_ref_wav=_env_path(
            "AI_VOICE_ATOM_TTS_REF_WAV",
            HOST_DIR / "voices" / "reference.wav",
        ),
        # リレー会話で「play を push してからデバイスの played ack を待つ」上限秒数。
        # 合成(GPU 競合で遅延しうる)＋再生の合計がこれを超えると会話を打ち切るため、
        # 負荷の高い環境では十分大きく取る。デバイス側の /api/chat/say タイムアウトより長く。
        relay_ack_timeout=float(os.getenv("AI_VOICE_ATOM_RELAY_ACK_TIMEOUT", "180")),
        # device_id ごとのペルソナ・声色を定義する JSON（無ければ全デバイスがグローバル設定で動く）。
        device_profiles_path=_env_path(
            "AI_VOICE_ATOM_DEVICE_PROFILES",
            HOST_DIR / "device_profiles.json",
        ),
        moonshine_root_path=_env_path(
            "AI_VOICE_ATOM_MOONSHINE_ROOT",
            REPO_ROOT / "third_party" / "moonshine",
        ),
        moonshine_cache_path=_env_path(
            "AI_VOICE_ATOM_MOONSHINE_CACHE",
            REPO_ROOT / ".cache" / "moonshine_voice",
        ),
        stt_model_language=os.getenv("AI_VOICE_ATOM_STT_LANGUAGE", "ja"),
        include_audio_inline=_env_bool("AI_VOICE_ATOM_INCLUDE_AUDIO_INLINE", False),
        response_prefix=os.getenv("AI_VOICE_ATOM_RESPONSE_PREFIX", "MVP 応答"),
        # 会話履歴: session_id 単位で直近 N 往復を保持し LLM へ渡す。0 で無効。
        history_max_turns=int(os.getenv("AI_VOICE_ATOM_HISTORY_MAX_TURNS", "6")),
        history_ttl_sec=float(os.getenv("AI_VOICE_ATOM_HISTORY_TTL_SEC", "1800")),
        # LLM へ渡す system プロンプト。未設定なら DEFAULT_LLM_SYSTEM_PROMPT、空文字なら無効。
        llm_system_prompt=(
            (os.environ["AI_VOICE_ATOM_LLM_SYSTEM_PROMPT"].strip() or None)
            if "AI_VOICE_ATOM_LLM_SYSTEM_PROMPT" in os.environ
            else DEFAULT_LLM_SYSTEM_PROMPT
        ),
        # 対話ガイダンス。system プロンプト末尾に連結して相手の発言の逐語コピーを抑える。
        # 未設定なら既定、空文字なら連結しない。
        llm_dialogue_guidance=(
            (os.environ["AI_VOICE_ATOM_LLM_DIALOGUE_GUIDANCE"].strip() or None)
            if "AI_VOICE_ATOM_LLM_DIALOGUE_GUIDANCE" in os.environ
            else DEFAULT_LLM_DIALOGUE_GUIDANCE
        ),
        # LLM 応答の最大トークン数。会話用途に合わせ短めの既定値。
        llm_max_tokens=int(os.getenv("AI_VOICE_ATOM_LLM_MAX_TOKENS", "128")),
        # 生成の多様性・反復抑制。小型モデルが相手の発言をそのまま繰り返すのを防ぐ。
        # 空文字を設定するとそのパラメータを送らない（バックエンド既定に従う）。
        llm_temperature=_env_float_opt("AI_VOICE_ATOM_LLM_TEMPERATURE", 0.8),
        llm_presence_penalty=_env_float_opt("AI_VOICE_ATOM_LLM_PRESENCE_PENALTY", 0.5),
        llm_frequency_penalty=_env_float_opt("AI_VOICE_ATOM_LLM_FREQUENCY_PENALTY", 0.4),
    )
