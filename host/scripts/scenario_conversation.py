"""実 LLM（Ollama 上の gemma4 等）でリレー会話を回し、テキスト会話ログを出力する。

実機（AtomS3 / TTS）を介さず、本番の ``ConversationOrchestrator`` と同じターン制御
（B3 会話フェーズ指示 / B4 反復ガード / B1 相手認識）を流用して、2 体のペルソナが
テキストだけで自然にやりとりできているかを確認するための手動シナリオテスト。

使い方（host/ から）::

    AI_VOICE_ATOM_LLM_BASE_URL=http://<ollama-host>:11434/v1 \\
    python scripts/scenario_conversation.py [--turns N] [--scenario genki_ottori] [--all]

TTS は使わない（``generate_reply`` は LLM のみ）。話者デバイスへの play/ack も行わず、
生成テキストをそのまま「相手の入力」として次のターンへ渡す。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import get_settings
from app.device_profiles import load_device_profiles
from app.services.chat_service import ChatService
from app.services.conversation_orchestrator import DEFAULT_KICKOFF, ConversationOrchestrator


class _NoTts:
    """TTS を呼ばないダミー。generate_reply では合成しないので未使用のまま。"""

    def synthesize(self, *args, **kwargs):  # pragma: no cover - 呼ばれない想定
        raise RuntimeError("scenario test must not synthesize audio")


# 確認したいペルソナ組み合わせ。device_profiles.json の device_id を指す。
SCENARIOS: dict[str, tuple[str, str, str | None]] = {
    # name -> (device_a, device_b, opening_text(None ならペルソナの opening_line)）
    "genki_ottori": ("atoms3-001", "atoms3-002", None),
    "genki_shikkari": ("atoms3-001", "atoms3-003", None),
    "ottori_shikkari": ("atoms3-002", "atoms3-003", None),
}


def _name(chat: ChatService, device_id: str) -> str:
    profile = chat.device_profiles.get(device_id)
    return (profile.display_name if profile else None) or device_id


async def run_scenario(
    chat: ChatService,
    name: str,
    device_a: str,
    device_b: str,
    opening_text: str | None,
    max_turns: int,
) -> list[tuple[int, str, str]]:
    """1 シナリオを回し、(turn, display_name, utterance) のリストを返す。"""
    session_id = f"scenario-{name}"
    speakers = (device_a, device_b)
    last_by_speaker: dict[str, str | None] = {device_a: None, device_b: None}
    current_text = (opening_text or "").strip()
    transcript: list[tuple[int, str, str]] = []

    print(
        f"\n{'=' * 72}\n[{name}] {_name(chat, device_a)} × {_name(chat, device_b)}  "
        f"(turns={max_turns})\n{'=' * 72}"
    )

    for turn in range(max_turns):
        speaker = speakers[turn % 2]
        partner = speakers[(turn + 1) % 2]

        opening = current_text or chat.relay_opening_line(speaker) or "" if turn == 0 else ""
        if turn == 0 and opening:
            utterance = opening
            chat.record_relay_opening(speaker, session_id, utterance)
        else:
            seed = current_text or DEFAULT_KICKOFF
            directive = ConversationOrchestrator._directive(
                turn, max_turns, last_by_speaker[speaker]
            )
            utterance = await chat.generate_reply(
                speaker, session_id, seed, partner_id=partner, directive=directive
            )

        disp = _name(chat, speaker)
        print(f"  {turn + 1:2d}. {disp}: {utterance}")
        transcript.append((turn + 1, disp, utterance))
        current_text = utterance
        last_by_speaker[speaker] = utterance

    return transcript


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=8, help="ターン数（既定 8）")
    parser.add_argument(
        "--scenario", default="genki_ottori", choices=sorted(SCENARIOS), help="実行するシナリオ"
    )
    parser.add_argument("--all", action="store_true", help="全シナリオを実行")
    args = parser.parse_args()

    settings = get_settings()
    print(
        f"model={settings.llm_model}  base_url={settings.llm_base_url}  "
        f"temp(global)={settings.llm_temperature}  "
        f"presence={settings.llm_presence_penalty}  freq={settings.llm_frequency_penalty}"
    )

    profiles = load_device_profiles(settings.device_profiles_path)
    chat = ChatService(settings=settings, tts_adapter=_NoTts(), device_profiles=profiles)

    names = sorted(SCENARIOS) if args.all else [args.scenario]
    for name in names:
        device_a, device_b, opening = SCENARIOS[name]
        if device_a not in profiles or device_b not in profiles:
            print(f"[skip] {name}: device 未登録 ({device_a} / {device_b})")
            continue
        await run_scenario(chat, name, device_a, device_b, opening, args.turns)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
