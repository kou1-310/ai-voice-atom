"""TTS 直前のテキスト整形（C1）。

LLM 応答には読み上げに不向きな装飾（マークダウン記号・絵文字・URL・記号の
過剰な連続・箇条書き）が混ざることがある。それらを除去・正規化し、日本語 TTS が
自然に読み上げられる平文へ整える純関数を提供する。

整形は**音声合成へ渡す直前にだけ**使う。表示用テキスト（`X-LLM-Text` ヘッダや
`ChatResponse.llm_text`）には適用せず、画面には元の応答をそのまま見せる。
"""

from __future__ import annotations

import re

# 行頭の箇条書き記号（- / * / ・ / 1. / 1) など）。中身は残し、記号だけ落とす。
_BULLET = re.compile(r"^[ \t　]*(?:[-*・]|\d+[.)])[ \t　]+", re.MULTILINE)
# マークダウンの強調・引用・コード・表の記号。
_DECOR = re.compile(r"[*_`~#>|]+")
# URL（読み上げると冗長なので落とす）。
_URL = re.compile(r"https?://\S+")
# 絵文字・装飾記号・矢印など、日本語の読み上げを邪魔する範囲。
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # 各種絵文字
    "\U00002600-\U000027bf"  # 記号・装飾
    "\U00002190-\U000021ff"  # 矢印
    "\U00002b00-\U00002bff"  # 補助矢印・記号
    "\U0000fe00-\U0000fe0f"  # 異体字セレクタ
    "\U0000200d"  # ゼロ幅接合子
    "]+"
)
# 同種の句読点・感嘆/疑問の連続は 1 つに畳む。
_REPEAT_BANG = re.compile(r"[!！]{2,}")
_REPEAT_QUES = re.compile(r"[?？]{2,}")
_REPEAT_KUTEN = re.compile(r"。{2,}")
_REPEAT_TOUTEN = re.compile(r"、{2,}")
# 連続する空白（半角/タブ/全角）。
_SPACES = re.compile(r"[ \t　]+")

# 行を連結するとき、直前の行末がこれらで終わっていれば句点を補わない。
_BREAK_CHARS = "。．！？!?、，,"


def normalize_for_tts(text: str) -> str:
    """読み上げ向けに整形した平文を返す。整形結果が空なら空文字列。

    呼び出し側は ``normalize_for_tts(text) or text`` のように、空に潰れた場合へ
    元テキストでフォールバックすること（絵文字のみの応答などで全消去され得る）。
    """
    if not text:
        return ""

    s = _URL.sub("", text)
    s = _BULLET.sub("", s)
    s = _DECOR.sub("", s)
    s = _EMOJI.sub("", s)

    # 改行は文の区切りとみなす。直前が句読点で終わっていなければ句点を補って連結する。
    parts: list[str] = []
    for line in (raw.strip() for raw in s.splitlines()):
        if not line:
            continue
        if parts and parts[-1][-1] not in _BREAK_CHARS:
            parts.append("。")
        parts.append(line)
    s = "".join(parts)

    s = _REPEAT_BANG.sub("！", s)
    s = _REPEAT_QUES.sub("？", s)
    s = _REPEAT_KUTEN.sub("。", s)
    s = _REPEAT_TOUTEN.sub("、", s)
    s = _SPACES.sub(" ", s)
    return s.strip()
