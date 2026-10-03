import asyncio
import base64
from pathlib import Path
from wave import open as wave_open

from app.config import COMMON_VOICE_STYLE, Settings
from app.device_profiles import DeviceProfile
from app.models.schemas import ChatRequest
from app.services.chat_service import ChatService


class _FakeOpenAIClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []
        # サンプリング系などの追加 kwargs を別途記録する（既存の calls 比較を壊さないため）。
        self.extra_kwargs: list[dict[str, object]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        max_tokens: int = 256,
        **kwargs: object,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "system_prompt": system_prompt,
                "max_tokens": max_tokens,
            }
        )
        self.extra_kwargs.append(dict(kwargs))
        return self.text


class _FakeIrodoriAdapter:
    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.calls: list[dict[str, object]] = []

    def synthesize(
        self,
        text: str,
        ref_wav: Path | None = None,
        output_path: Path | None = None,
        voice: str = "default",
    ) -> Path:
        assert output_path is not None
        self.calls.append(
            {
                "text": text,
                "ref_wav": ref_wav,
                "output_path": output_path,
                "voice": voice,
            }
        )
        with wave_open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(b"\x00\x00" * 32 * self.channels)
        return output_path


def _settings(
    include_audio_inline: bool,
    history_max_turns: int = 6,
    history_ttl_sec: float = 1800.0,
    llm_system_prompt: str | None = None,
    llm_max_tokens: int = 256,
    llm_dialogue_guidance: str | None = None,
    llm_temperature: float | None = None,
    llm_presence_penalty: float | None = None,
    llm_frequency_penalty: float | None = None,
) -> Settings:
    return Settings(
        device_id="atoms3-001",
        default_mode="web",
        host="0.0.0.0",
        port=8000,
        llm_base_url="http://localhost:1234/v1",
        llm_api_key="dummy",
        llm_model="test-model",
        llm_api_key_present=True,
        llm_timeout=30.0,
        llm_reasoning_effort="none",
        tts_root_path=Path("/tmp/irodori"),
        tts_timeout=120.0,
        tts_server_url=None,
        tts_server_autostart=False,
        tts_ref_wav=Path("/tmp/irodori/reference.wav"),
        relay_ack_timeout=180.0,
        device_profiles_path=Path("/tmp/irodori/device_profiles.json"),
        moonshine_root_path=Path("/tmp/moonshine"),
        moonshine_cache_path=Path("/tmp/moonshine-cache"),
        stt_model_language="ja",
        include_audio_inline=include_audio_inline,
        response_prefix="MVP 応答",
        history_max_turns=history_max_turns,
        history_ttl_sec=history_ttl_sec,
        llm_system_prompt=llm_system_prompt,
        llm_dialogue_guidance=llm_dialogue_guidance,
        llm_max_tokens=llm_max_tokens,
        llm_temperature=llm_temperature,
        llm_presence_penalty=llm_presence_penalty,
        llm_frequency_penalty=llm_frequency_penalty,
    )


def test_handle_chat_returns_text_and_audio_when_enabled() -> None:
    llm_client = _FakeOpenAIClient("本日の予定は3件あります。")
    tts_adapter = _FakeIrodoriAdapter(sample_rate=24000, channels=2)
    service = ChatService(
        settings=_settings(include_audio_inline=True),
        llm_client=llm_client,
        tts_adapter=tts_adapter,
    )

    response = asyncio.run(
        service.handle_chat(
            ChatRequest(
                session_id="sess-1",
                input_text="今日の予定を教えて",
                response_format="text+audio",
            )
        )
    )

    assert response.session_id == "sess-1"
    assert response.llm_text == "本日の予定は3件あります。"
    assert response.audio is not None
    assert response.audio.sample_rate == 24000
    assert response.audio.channels == 2
    assert base64.b64decode(response.audio.data).startswith(b"RIFF")
    assert llm_client.calls == [
        {
            "messages": [{"role": "user", "content": "今日の予定を教えて"}],
            "system_prompt": None,
            "max_tokens": 256,
        }
    ]
    assert tts_adapter.calls[0]["text"] == "本日の予定は3件あります。"


def test_handle_chat_returns_text_only_when_inline_audio_disabled() -> None:
    llm_client = _FakeOpenAIClient("了解しました。")
    tts_adapter = _FakeIrodoriAdapter()
    service = ChatService(
        settings=_settings(include_audio_inline=False),
        llm_client=llm_client,
        tts_adapter=tts_adapter,
    )

    response = asyncio.run(
        service.handle_chat(
            ChatRequest(
                session_id="sess-2",
                input_text="了解",
                response_format="text+audio",
            )
        )
    )

    assert response.llm_text == "了解しました。"
    assert response.audio is None
    assert tts_adapter.calls == []


def test_handle_chat_passes_conversation_history_on_next_turn() -> None:
    llm_client = _FakeOpenAIClient("応答テキスト")
    service = ChatService(
        settings=_settings(include_audio_inline=False),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
    )

    asyncio.run(service.handle_chat(ChatRequest(session_id="sess-h", input_text="一回目")))
    asyncio.run(service.handle_chat(ChatRequest(session_id="sess-h", input_text="二回目")))

    # 1 回目は履歴が空（user のみ）、2 回目は前回の user/assistant が前置される
    assert llm_client.calls[0]["messages"] == [{"role": "user", "content": "一回目"}]
    assert llm_client.calls[1]["messages"] == [
        {"role": "user", "content": "一回目"},
        {"role": "assistant", "content": "応答テキスト"},
        {"role": "user", "content": "二回目"},
    ]


def test_handle_chat_keeps_history_per_session() -> None:
    llm_client = _FakeOpenAIClient("応答テキスト")
    service = ChatService(
        settings=_settings(include_audio_inline=False),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
    )

    asyncio.run(service.handle_chat(ChatRequest(session_id="sess-a", input_text="A1")))
    asyncio.run(service.handle_chat(ChatRequest(session_id="sess-b", input_text="B1")))

    # 別 session_id の履歴は混ざらない
    assert llm_client.calls[1]["messages"] == [{"role": "user", "content": "B1"}]


def test_handle_chat_disables_history_when_max_turns_zero() -> None:
    llm_client = _FakeOpenAIClient("応答テキスト")
    service = ChatService(
        settings=_settings(include_audio_inline=False, history_max_turns=0),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
    )

    asyncio.run(service.handle_chat(ChatRequest(session_id="sess-z", input_text="一回目")))
    asyncio.run(service.handle_chat(ChatRequest(session_id="sess-z", input_text="二回目")))

    # 履歴無効時は常に user 単発
    assert llm_client.calls[1]["messages"] == [{"role": "user", "content": "二回目"}]


def test_chat_audio_uses_per_device_persona_and_voice(tmp_path: Path) -> None:
    """device_id ごとに system プロンプトと ref_wav が切り替わることを確認する。"""
    ref_a = tmp_path / "voice_a.wav"
    ref_b = tmp_path / "voice_b.wav"
    ref_a.write_bytes(b"a")
    ref_b.write_bytes(b"b")

    llm_client = _FakeOpenAIClient("応答")
    tts_adapter = _FakeIrodoriAdapter()
    service = ChatService(
        settings=_settings(include_audio_inline=False),
        llm_client=llm_client,
        tts_adapter=tts_adapter,
        device_profiles={
            "atoms3-001": DeviceProfile(system_prompt="A用プロンプト", ref_wav=ref_a),
            "atoms3-002": DeviceProfile(system_prompt="B用プロンプト", ref_wav=ref_b),
        },
    )

    asyncio.run(service.handle_chat_audio(ChatRequest(device_id="atoms3-001", input_text="やあ")))
    asyncio.run(service.handle_chat_audio(ChatRequest(device_id="atoms3-002", input_text="やあ")))

    assert llm_client.calls[0]["system_prompt"] == "A用プロンプト"
    assert llm_client.calls[1]["system_prompt"] == "B用プロンプト"
    assert tts_adapter.calls[0]["ref_wav"] == ref_a
    assert tts_adapter.calls[1]["ref_wav"] == ref_b


def test_chat_audio_passes_voice_caption_without_ref_wav() -> None:
    """ref_wav 無しでも voice_caption を VoiceDesign 用 caption として TTS へ渡す。"""
    llm_client = _FakeOpenAIClient("応答")
    tts_adapter = _FakeIrodoriAdapter()
    service = ChatService(
        settings=_settings(include_audio_inline=False),
        llm_client=llm_client,
        tts_adapter=tts_adapter,
        device_profiles={
            "atoms3-001": DeviceProfile(
                system_prompt="A用", voice_caption="落ち着いた女性の声で読み上げて"
            ),
            "atoms3-002": DeviceProfile(system_prompt="B用"),
        },
    )

    asyncio.run(service.handle_chat_audio(ChatRequest(device_id="atoms3-001", input_text="やあ")))
    asyncio.run(service.handle_chat_audio(ChatRequest(device_id="atoms3-002", input_text="やあ")))

    # caption 指定機は voice にそのまま渡り（ref_wav は無し）、未指定機は既定の声。
    assert tts_adapter.calls[0]["voice"] == "落ち着いた女性の声で読み上げて"
    assert tts_adapter.calls[0]["ref_wav"] is None
    assert tts_adapter.calls[1]["voice"] == "default"


def test_chat_audio_keeps_history_per_device_even_with_same_session(tmp_path: Path) -> None:
    """同一 session_id でも device_id が違えば履歴が混ざらないことを確認する。"""
    llm_client = _FakeOpenAIClient("応答")
    service = ChatService(
        settings=_settings(include_audio_inline=False),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
    )

    # ファームは全機が固定 session_id を送りうる。device_id で分離されなければならない。
    asyncio.run(
        service.handle_chat_audio(
            ChatRequest(device_id="atoms3-001", session_id="sess", input_text="A1")
        )
    )
    asyncio.run(
        service.handle_chat_audio(
            ChatRequest(device_id="atoms3-002", session_id="sess", input_text="B1")
        )
    )

    # 2 機目は自分の履歴のみ（1 機目の発話は混ざらない）
    assert llm_client.calls[1]["messages"] == [{"role": "user", "content": "B1"}]


def test_chat_audio_falls_back_to_global_settings_for_unknown_device(tmp_path: Path) -> None:
    """未登録 device_id はグローバル system プロンプト / ref=None にフォールバックする。"""
    llm_client = _FakeOpenAIClient("応答")
    tts_adapter = _FakeIrodoriAdapter()
    service = ChatService(
        settings=_settings(include_audio_inline=False, llm_system_prompt="共通プロンプト"),
        llm_client=llm_client,
        tts_adapter=tts_adapter,
        device_profiles={"atoms3-001": DeviceProfile(system_prompt="A用")},
    )

    asyncio.run(
        service.handle_chat_audio(ChatRequest(device_id="unknown-device", input_text="やあ"))
    )

    assert llm_client.calls[0]["system_prompt"] == "共通プロンプト"
    assert tts_adapter.calls[0]["ref_wav"] is None


def test_dialogue_guidance_is_appended_to_persona_prompt() -> None:
    """対話ガイダンスがペルソナ system プロンプトの末尾に連結されることを確認する。"""
    llm_client = _FakeOpenAIClient("応答")
    service = ChatService(
        settings=_settings(include_audio_inline=False, llm_dialogue_guidance="繰り返さない"),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
        device_profiles={"atoms3-001": DeviceProfile(system_prompt="あなたは冷静")},
    )

    asyncio.run(service.handle_chat(ChatRequest(device_id="atoms3-001", input_text="やあ")))

    assert llm_client.calls[0]["system_prompt"] == "あなたは冷静\n\n繰り返さない"


def test_dialogue_guidance_used_alone_when_no_base_prompt() -> None:
    """ベースの system プロンプトが無ければガイダンス単体を使う。"""
    llm_client = _FakeOpenAIClient("応答")
    service = ChatService(
        settings=_settings(
            include_audio_inline=False,
            llm_system_prompt=None,
            llm_dialogue_guidance="繰り返さない",
        ),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
    )

    asyncio.run(service.handle_chat(ChatRequest(input_text="やあ")))

    assert llm_client.calls[0]["system_prompt"] == "繰り返さない"


def test_sampling_params_passed_only_when_set() -> None:
    """temperature / penalty は設定があるときだけ complete に渡す。"""
    llm_client = _FakeOpenAIClient("応答")
    service = ChatService(
        settings=_settings(
            include_audio_inline=False,
            llm_temperature=0.8,
            llm_presence_penalty=0.5,
            llm_frequency_penalty=None,  # 未設定は送らない
        ),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
    )

    asyncio.run(service.handle_chat(ChatRequest(input_text="やあ")))

    assert llm_client.extra_kwargs[0] == {"temperature": 0.8, "presence_penalty": 0.5}


def test_handle_chat_trims_history_to_max_turns() -> None:
    llm_client = _FakeOpenAIClient("応答テキスト")
    service = ChatService(
        settings=_settings(include_audio_inline=False, history_max_turns=2),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
    )

    for i in range(4):
        asyncio.run(service.handle_chat(ChatRequest(session_id="sess-t", input_text=f"q{i}")))

    # max_turns=2 → 直近 2 往復（4 メッセージ）+ 今回の user = 5 メッセージに収まる
    last_messages = llm_client.calls[-1]["messages"]
    assert len(last_messages) == 5
    assert last_messages[0] == {"role": "user", "content": "q1"}
    assert last_messages[-1] == {"role": "user", "content": "q3"}


def test_structured_persona_prompt_is_synthesized() -> None:
    """system_prompt 省略時、構造化フィールドから読み上げ向け system を合成する（A1）。"""
    llm_client = _FakeOpenAIClient("応答")
    service = ChatService(
        settings=_settings(include_audio_inline=False),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
        device_profiles={
            "atoms3-001": DeviceProfile(
                display_name="げんき",
                persona="明るく好奇心旺盛",
                first_person="ぼく",
                speaking_style="はきはき話す",
                interests=("カメラ", "散歩"),
            )
        },
    )

    asyncio.run(service.handle_chat(ChatRequest(device_id="atoms3-001", input_text="やあ")))

    prompt = llm_client.calls[0]["system_prompt"]
    assert "「げんき」" in prompt
    assert "明るく好奇心旺盛" in prompt
    assert "一人称は「ぼく」" in prompt
    assert "カメラ、散歩" in prompt
    assert COMMON_VOICE_STYLE in prompt


def test_per_persona_sampling_overrides_global() -> None:
    """ペルソナ別の temperature 等がグローバル設定を上書きする（A2）。"""
    llm_client = _FakeOpenAIClient("応答")
    service = ChatService(
        settings=_settings(
            include_audio_inline=False,
            llm_temperature=0.8,
            llm_presence_penalty=0.5,
        ),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
        device_profiles={
            "atoms3-001": DeviceProfile(system_prompt="A", temperature=0.3),
        },
    )

    asyncio.run(service.handle_chat(ChatRequest(device_id="atoms3-001", input_text="やあ")))

    # temperature はペルソナ値、presence_penalty はグローバルにフォールバック。
    assert llm_client.extra_kwargs[0] == {"temperature": 0.3, "presence_penalty": 0.5}


def test_generate_reply_injects_partner_and_directive() -> None:
    """リレー会話で相手認識ブロック（B1）とターン指示（B3/B4）が system に入る。"""
    llm_client = _FakeOpenAIClient("応答")
    service = ChatService(
        settings=_settings(include_audio_inline=False, llm_dialogue_guidance="繰り返さない"),
        llm_client=llm_client,
        tts_adapter=_FakeIrodoriAdapter(),
        device_profiles={
            "atoms3-001": DeviceProfile(system_prompt="あなたはA"),
            "atoms3-002": DeviceProfile(display_name="しっかりさん", persona="几帳面な世話焼き"),
        },
    )

    asyncio.run(
        service.generate_reply(
            "atoms3-001",
            "relay-1",
            "やあ",
            partner_id="atoms3-002",
            directive="これが最後の発言です。",
        )
    )

    prompt = llm_client.calls[0]["system_prompt"]
    assert prompt.startswith("あなたはA")
    assert "「しっかりさん」（几帳面な世話焼き）" in prompt
    assert "繰り返さない" in prompt
    assert "これが最後の発言です。" in prompt
