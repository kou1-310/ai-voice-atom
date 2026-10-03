import asyncio
import base64
import logging
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.adapters.irodori_tts_adapter import IrodoriTtsAdapter
from app.adapters.openai_client import OpenAIClient
from app.config import COMMON_VOICE_STYLE, Settings
from app.device_profiles import DeviceProfile, load_device_profiles
from app.models.schemas import AudioResponse, ChatRequest, ChatResponse
from app.utils.text_normalize import normalize_for_tts
from app.utils.wav import read_wav_metadata, to_pcm16_mono

logger = logging.getLogger(__name__)


class _ConversationStore:
    """session_id 単位で会話履歴を保持する（プロセス内メモリ・上限/TTL つき）。

    ``max_turns`` 往復（= ユーザー/アシスタント 2*max_turns メッセージ）を超えると古いものから
    捨てる。``ttl_sec`` を過ぎたセッションは破棄する。``max_turns <= 0`` で履歴機能を無効化する。
    """

    def __init__(self, max_turns: int, ttl_sec: float) -> None:
        self.max_turns = max_turns
        self.ttl_sec = ttl_sec
        self._store: dict[str, tuple[float, list[dict[str, str]]]] = {}

    def get(self, session_id: str) -> list[dict[str, str]]:
        if self.max_turns <= 0:
            return []
        entry = self._store.get(session_id)
        if entry is None:
            return []
        timestamp, messages = entry
        if self.ttl_sec > 0 and time.monotonic() - timestamp > self.ttl_sec:
            self._store.pop(session_id, None)
            return []
        return list(messages)

    def append(self, session_id: str, user_text: str, assistant_text: str) -> None:
        if self.max_turns <= 0:
            return
        messages = self.get(session_id)
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": assistant_text})
        limit = self.max_turns * 2
        if len(messages) > limit:
            messages = messages[-limit:]
        self._store[session_id] = (time.monotonic(), messages)


class ChatService:
    def __init__(
        self,
        settings: Settings,
        llm_client: OpenAIClient | None = None,
        tts_adapter: IrodoriTtsAdapter | None = None,
        device_profiles: dict[str, DeviceProfile] | None = None,
    ) -> None:
        self.settings = settings
        self.llm_client = llm_client or OpenAIClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout,
            reasoning_effort=settings.llm_reasoning_effort,
        )
        self.tts_adapter = tts_adapter or IrodoriTtsAdapter(
            root_path=settings.tts_root_path,
            timeout=settings.tts_timeout,
            server_url=settings.tts_server_url,
            server_autostart=settings.tts_server_autostart,
            ref_wav=settings.tts_ref_wav,
        )
        # device_id ごとのペルソナ・声色。未指定なら設定ファイルからロードする。
        self.device_profiles = (
            device_profiles
            if device_profiles is not None
            else load_device_profiles(settings.device_profiles_path)
        )
        self._history = _ConversationStore(
            max_turns=settings.history_max_turns,
            ttl_sec=settings.history_ttl_sec,
        )

    def _history_key(self, payload: ChatRequest) -> str:
        """履歴キー。device_id::session_id の複合キーで、複数デバイスの履歴を分離する。"""
        return f"{payload.device_id}::{payload.session_id}"

    def _persona_base_prompt(self, device_id: str) -> str | None:
        """device_id のペルソナ本体プロンプト（対話ガイダンス等を足す前）。

        優先順位は ①明示の ``system_prompt`` ②構造化フィールドからの合成 ③グローバル既定。
        """
        profile = self.device_profiles.get(device_id)
        if profile is not None and profile.system_prompt:
            return profile.system_prompt
        if profile is not None and (
            profile.persona or profile.speaking_style or profile.interests or profile.first_person
        ):
            return self._synthesize_persona_prompt(device_id, profile)
        return self.settings.llm_system_prompt

    @staticmethod
    def _synthesize_persona_prompt(device_id: str, profile: DeviceProfile) -> str:
        """構造化フィールドから読み上げ向けの system プロンプトを組み立てる（A1）。"""
        name = profile.display_name or device_id
        segments = [f"あなたは「{name}」です。"]
        if profile.persona:
            segments.append(f"{profile.persona.rstrip('。')}。")
        if profile.first_person:
            segments.append(f"一人称は「{profile.first_person}」。")
        if profile.speaking_style:
            segments.append(f"{profile.speaking_style.rstrip('。')}。")
        if profile.interests:
            segments.append(f"好きな話題は{'、'.join(profile.interests)}。")
        segments.append(COMMON_VOICE_STYLE)
        return "".join(segments)

    def _partner_block(self, partner_id: str) -> str:
        """会話相手を認識させる一文（B1）。相手の表示名と性格要約で呼びかけを促す。"""
        profile = self.device_profiles.get(partner_id)
        name = (profile.display_name if profile is not None else None) or partner_id
        descriptor = ""
        if profile is not None and profile.persona:
            descriptor = f"（{profile.persona.rstrip('。')}）"
        return (
            f"いま「{name}」{descriptor}と交互に会話しています。"
            "相手の名前を時々呼びかけ、相手の発言に具体的に反応してください。"
            "同意や相づちだけで終わらせず、自分の話題を一つ加えてください。"
        )

    def _resolve_system_prompt(
        self,
        device_id: str,
        partner_id: str | None = None,
        directive: str | None = None,
    ) -> str | None:
        """device_id のペルソナ system プロンプトを組み立てる。

        ①ペルソナ本体 ②相手認識ブロック（B1・``partner_id`` 指定時） ③対話ガイダンス
        ④ターン指示（B3 フェーズ / B4 反復ガード・``directive`` 指定時）を空行で連結する。
        非リレー経路は ``partner_id`` / ``directive`` を省くので従来と同じ出力になる。
        """
        parts: list[str] = []
        base = self._persona_base_prompt(device_id)
        if base:
            parts.append(base)
        if partner_id:
            parts.append(self._partner_block(partner_id))
        if self.settings.llm_dialogue_guidance:
            parts.append(self.settings.llm_dialogue_guidance)
        if directive:
            parts.append(directive)
        if not parts:
            return None
        return "\n\n".join(parts)

    def _llm_sampling_kwargs(self, device_id: str | None = None) -> dict[str, float]:
        """生成パラメータをまとめる。ペルソナ別の上書きがあれば優先（A2）、None は送らない。"""
        profile = self.device_profiles.get(device_id) if device_id else None

        def resolve(profile_value: float | None, global_value: float | None) -> float | None:
            return profile_value if profile_value is not None else global_value

        candidates = {
            "temperature": resolve(
                profile.temperature if profile else None, self.settings.llm_temperature
            ),
            "presence_penalty": resolve(
                profile.presence_penalty if profile else None, self.settings.llm_presence_penalty
            ),
            "frequency_penalty": resolve(
                profile.frequency_penalty if profile else None,
                self.settings.llm_frequency_penalty,
            ),
        }
        return {key: value for key, value in candidates.items() if value is not None}

    def relay_opening_line(self, device_id: str) -> str | None:
        """device_id のペルソナに設定された口火の一言（A3）。無ければ None。"""
        profile = self.device_profiles.get(device_id)
        return profile.opening_line if profile is not None else None

    def record_relay_opening(self, device_id: str, session_id: str, text: str) -> None:
        """リレー会話の最初の発話（逐語）を話者の履歴へ記録する（B2）。

        turn 0 を LLM に通さず逐語再生する場合でも、その後のターンで「自分が口火を切った」
        ことを思い出せるよう、合成的なキックオフ user とペアで履歴に積む。
        """
        history_key = f"{device_id}::{session_id}"
        self._history.append(history_key, "（会話を始めてください）", text)

    def _resolve_ref_wav(self, device_id: str) -> Path | None:
        """device_id の声色 ref_wav。存在するファイルのときだけ採用し、無ければ None
        （= アダプタのグローバル既定 ref / 既定の声にフォールバック）。"""
        profile = self.device_profiles.get(device_id)
        if profile is not None and profile.ref_wav is not None and profile.ref_wav.exists():
            return profile.ref_wav
        return None

    def _resolve_voice(self, device_id: str) -> str:
        """device_id の声色 caption（VoiceDesign 用）。参照音声を使わなくても性別・トーン等を
        自然文で指定して合成できる。未指定なら "default"（= 既定の声 / ベースモデル）。
        ref_wav と併用された場合はアダプタ側で「その声＋このスタイル」のクローンになる。"""
        profile = self.device_profiles.get(device_id)
        if profile is not None and profile.voice_caption:
            return profile.voice_caption
        return "default"

    async def _generate_llm_reply(
        self,
        device_id: str,
        history_key: str,
        text: str,
        partner_id: str | None = None,
        directive: str | None = None,
    ) -> str:
        """履歴付きで LLM 応答を生成し、履歴へ追記して応答テキストを返す。

        ``handle_chat`` / ``handle_chat_audio`` / ``generate_reply``（リレー会話）で共有する。
        ``partner_id`` / ``directive`` はリレー会話で相手認識・ターン指示を渡すために使う
        （非リレー経路は省略され、従来と同じ挙動になる）。
        """
        history = self._history.get(history_key)
        llm_text = await self.llm_client.complete(
            messages=[*history, {"role": "user", "content": text}],
            system_prompt=self._resolve_system_prompt(
                device_id, partner_id=partner_id, directive=directive
            ),
            max_tokens=self.settings.llm_max_tokens,
            **self._llm_sampling_kwargs(device_id),
        )
        self._history.append(history_key, text, llm_text)
        return llm_text

    async def generate_reply(
        self,
        device_id: str,
        session_id: str,
        input_text: str,
        partner_id: str | None = None,
        directive: str | None = None,
    ) -> str:
        """リレー会話用: device_id のペルソナで input_text への応答テキストを生成する。

        音声は作らず（TTS は ``synthesize_say`` 側）、履歴は ``device_id::session_id`` で分離。
        ``partner_id`` で相手を認識させ（B1）、``directive`` でターンごとの会話フェーズ指示や
        反復ガード（B3/B4）を差し込む。
        """
        text = input_text.strip()
        if not text:
            raise ValueError("input_text must not be empty")
        history_key = f"{device_id}::{session_id}"
        return await self._generate_llm_reply(
            device_id, history_key, text, partner_id=partner_id, directive=directive
        )

    async def synthesize_say(self, device_id: str, text: str) -> bytes:
        """device_id の声色で text を TTS した 16kHz/mono WAV バイト列を返す（LLM 不使用）。

        リレー会話で、ホストから push されたテキストをデバイスが自分の声で鳴らすために使う。
        """
        normalized = text.strip()
        if not normalized:
            raise ValueError("text must not be empty")
        ref_wav = self._resolve_ref_wav(device_id)
        voice = self._resolve_voice(device_id)
        return await asyncio.to_thread(self._synthesize_audio_16k, normalized, ref_wav, voice)

    async def handle_chat(self, payload: ChatRequest) -> ChatResponse:
        text = payload.input_text.strip()
        if not text:
            raise ValueError("input_text must not be empty")

        history_key = self._history_key(payload)
        llm_text = await self._generate_llm_reply(payload.device_id, history_key, text)

        audio = None
        if payload.response_format == "text+audio" and self.settings.include_audio_inline:
            ref_wav = self._resolve_ref_wav(payload.device_id)
            voice = self._resolve_voice(payload.device_id)
            audio = await asyncio.to_thread(
                self._synthesize_audio, llm_text, payload.audio.sample_rate, ref_wav, voice
            )

        return ChatResponse(
            session_id=payload.session_id,
            message_id=f"msg-{uuid4().hex[:12]}",
            llm_text=llm_text,
            audio=audio,
        )

    async def handle_chat_audio(self, payload: ChatRequest) -> tuple[str, bytes]:
        """LLM 応答テキストと、それを TTS した 16kHz/mono WAV バイト列を返す。

        実機ストリーミング再生用。base64/JSON を介さずバイナリ WAV をそのまま返すため、
        デバイス側はチャンク受信しながら逐次再生でき、巨大な応答でも RAM を圧迫しない。
        """
        text = payload.input_text.strip()
        if not text:
            raise ValueError("input_text must not be empty")

        t0 = time.perf_counter()
        history_key = self._history_key(payload)
        llm_text = await self._generate_llm_reply(payload.device_id, history_key, text)
        t1 = time.perf_counter()
        ref_wav = self._resolve_ref_wav(payload.device_id)
        voice = self._resolve_voice(payload.device_id)
        wav_bytes = await asyncio.to_thread(self._synthesize_audio_16k, llm_text, ref_wav, voice)
        t2 = time.perf_counter()
        logger.info(
            "[timing] chat_audio total=%.2fs | llm=%.2fs tts+conv=%.2fs "
            "| in_chars=%d out_chars=%d wav=%dB",
            t2 - t0,
            t1 - t0,
            t2 - t1,
            len(text),
            len(llm_text),
            len(wav_bytes),
        )
        return llm_text, wav_bytes

    def _synthesize_audio_16k(
        self, text: str, ref_wav: Path | None = None, voice: str = "default"
    ) -> bytes:
        speech = normalize_for_tts(text) or text  # 読み上げ向けに整形（C1）。空潰れは元文へ
        with TemporaryDirectory(prefix="ai-voice-atom-chat-") as temp_dir:
            output_path = Path(temp_dir) / "reply.wav"
            t0 = time.perf_counter()
            wav_path = self.tts_adapter.synthesize(
                text=speech, output_path=output_path, ref_wav=ref_wav, voice=voice
            )
            t1 = time.perf_counter()
            raw = wav_path.read_bytes()
            pcm16 = to_pcm16_mono(raw, target_sr=16000)
            t2 = time.perf_counter()
            logger.info(
                "[timing] synth tts=%.2fs conv=%.2fs | raw=%dB pcm16=%dB",
                t1 - t0,
                t2 - t1,
                len(raw),
                len(pcm16),
            )
            return pcm16

    def _synthesize_audio(
        self,
        text: str,
        requested_sample_rate: int,
        ref_wav: Path | None = None,
        voice: str = "default",
    ) -> AudioResponse:
        speech = normalize_for_tts(text) or text  # 読み上げ向けに整形（C1）。空潰れは元文へ
        with TemporaryDirectory(prefix="ai-voice-atom-chat-") as temp_dir:
            output_path = Path(temp_dir) / "reply.wav"
            wav_path = self.tts_adapter.synthesize(
                text=speech, output_path=output_path, ref_wav=ref_wav, voice=voice
            )
            wav_bytes = wav_path.read_bytes()
            sample_rate, channels = read_wav_metadata(wav_path)

        return AudioResponse(
            format="wav",
            encoding="base64",
            sample_rate=sample_rate or requested_sample_rate,
            channels=channels,
            data=base64.b64encode(wav_bytes).decode("ascii"),
        )
