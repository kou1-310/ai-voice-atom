from pydantic import BaseModel, Field


class AudioResponse(BaseModel):
    format: str = "wav"
    encoding: str = "base64"
    sample_rate: int = 16000
    channels: int = 1
    data: str = ""


class AudioRequest(BaseModel):
    format: str = "wav"
    sample_rate: int = 16000


class ChatRequest(BaseModel):
    device_id: str = "atoms3-001"
    session_id: str = "sess-mvp"
    mode: str = Field(default="web")
    input_text: str = ""
    response_format: str = "text+audio"
    audio: AudioRequest = Field(default_factory=AudioRequest)
    context: dict[str, str] | None = None


class ChatResponse(BaseModel):
    session_id: str
    message_id: str
    llm_text: str
    audio: AudioResponse | None = None


class TtsRequest(BaseModel):
    device_id: str = "atoms3-001"
    session_id: str = "sess-mvp"
    text: str = ""
    voice: str = "default"
    audio: AudioRequest = Field(default_factory=AudioRequest)


class TtsResponse(BaseModel):
    session_id: str
    audio: AudioResponse


class PlaybackStopRequest(BaseModel):
    device_id: str = "atoms3-001"
    session_id: str = "sess-mvp"


class PlaybackStopResponse(BaseModel):
    status: str = "accepted"


class NetworkStatus(BaseModel):
    wifi_connected: bool = True
    ip: str = "127.0.0.1"


class AudioStatus(BaseModel):
    is_playing: bool = False
    volume: int = 70


class BackendStatus(BaseModel):
    llm: str = "not-configured"
    tts: str = "not-configured"
    stt: str = "not-configured"


class StatusResponse(BaseModel):
    device_id: str = "atoms3-001"
    mode: str = "web"
    network: NetworkStatus = Field(default_factory=NetworkStatus)
    audio: AudioStatus = Field(default_factory=AudioStatus)
    backend: BackendStatus = Field(default_factory=BackendStatus)
    active_session_id: str | None = None


class DeviceSummary(BaseModel):
    """フロントのペルソナ選択肢用。device_id と表示名のみを返す軽量サマリ。"""

    device_id: str
    display_name: str


class DevicesResponse(BaseModel):
    devices: list[DeviceSummary] = Field(default_factory=list)


class SayRequest(BaseModel):
    """デバイスが自分のターンの音声を取得するための入力。

    リレー会話でホストから push されたテキストを、デバイスがこのエンドポイントへ送って
    自分の声色（device_id のペルソナ ref_wav）で TTS した WAV を受け取る。LLM は通さない。
    """

    device_id: str = "atoms3-001"
    text: str = ""
    # 声色を借りるペルソナ。実機1台で複数ペルソナを鳴らすときにホストが play で指定する。
    # 省略時は device_id 自身の声色（従来どおり）。
    persona_id: str | None = None


class ConversationStartRequest(BaseModel):
    """リレー会話を開始する要求（フロントから）。

    ``device_a`` / ``device_b`` は話者のペルソナ（device_profiles のキー）。``output_device_a`` /
    ``output_device_b`` はそのペルソナの発話を鳴らす実機で、省略時はペルソナと同じ ID の実機
    （= 実機2台の従来動作）。両方に同じ実機を指定すれば 1 台で 2 体の会話を再生できる。
    """

    device_a: str
    device_b: str
    opening_text: str | None = None
    max_turns: int = 6
    output_device_a: str | None = None
    output_device_b: str | None = None


class ConversationStartResponse(BaseModel):
    status: str = "started"
    device_a: str
    device_b: str
    max_turns: int
    output_device_a: str
    output_device_b: str


class ConversationChatRequest(BaseModel):
    """Web から実機のペルソナへ 1 往復話しかける要求。

    ``persona_id`` のペルソナが ``text`` に応答し、``output_device``（省略時は persona_id と
    同じ ID の実機）がその声色で再生する。``session_id`` ごとに会話履歴が続く。
    """

    persona_id: str
    text: str
    output_device: str | None = None
    session_id: str = "webchat"


class ConversationChatResponse(BaseModel):
    persona_id: str
    output_device: str
    text: str


class ConversationStatusResponse(BaseModel):
    """リレー会話の状態と、現在 push 可能な（WS接続中の）デバイス一覧。"""

    running: bool = False
    device_a: str | None = None
    device_b: str | None = None
    output_device_a: str | None = None
    output_device_b: str | None = None
    # Web からの 1 往復チャット（/api/conversation/chat）を処理中か。
    chatting: bool = False
    turn: int = 0
    max_turns: int = 0
    connected_devices: list[str] = Field(default_factory=list)
