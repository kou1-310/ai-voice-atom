"""TTS 前テキスト整形（C1）の単体テスト。純関数なので submodule 不要。"""

from app.utils.text_normalize import normalize_for_tts


def test_empty_returns_empty() -> None:
    assert normalize_for_tts("") == ""


def test_plain_text_unchanged() -> None:
    # 装飾の無い平文はそのまま（前後トリムのみ）。
    assert normalize_for_tts("  こんにちは、元気ですか。  ") == "こんにちは、元気ですか。"


def test_strips_markdown_decoration() -> None:
    out = normalize_for_tts("**強調**と`コード`と~~取り消し~~")
    assert "*" not in out
    assert "`" not in out
    assert "~" not in out
    assert "強調" in out and "コード" in out and "取り消し" in out


def test_removes_url() -> None:
    out = normalize_for_tts("詳しくは https://example.com/page を見てね")
    assert "http" not in out
    assert "詳しくは" in out and "見てね" in out


def test_strips_bullet_markers() -> None:
    out = normalize_for_tts("- りんご\n- みかん\n1. ぶどう")
    assert "りんご" in out and "みかん" in out and "ぶどう" in out
    # 行頭の箇条書き記号は消える。
    assert not out.lstrip().startswith("-")
    assert "1." not in out


def test_newlines_become_kuten() -> None:
    # 句点で終わっていない行の連結には句点を補う。
    out = normalize_for_tts("はい\nそうです")
    assert out == "はい。そうです"


def test_newline_keeps_existing_terminator() -> None:
    # 既に句読点・感嘆/疑問で終わる行には句点を重ねない。
    assert normalize_for_tts("はい。\nそうです") == "はい。そうです"
    assert normalize_for_tts("本当？\nすごい") == "本当？すごい"


def test_collapses_repeated_marks() -> None:
    assert normalize_for_tts("すごい！！！") == "すごい！"
    assert normalize_for_tts("ええ？？") == "ええ？"
    assert normalize_for_tts("ふむ。。。") == "ふむ。"
    assert normalize_for_tts("えっと、、、") == "えっと、"


def test_removes_emoji() -> None:
    out = normalize_for_tts("おはよう😀🎉 今日もがんばろう✨")
    assert "おはよう" in out and "今日もがんばろう" in out
    # 絵文字は残らない（ASCII と日本語のみ）。
    assert all(ord(ch) < 0x2190 or 0x3000 <= ord(ch) <= 0x9FFF or ch in "！？。、 " for ch in out)


def test_emoji_only_collapses_to_empty() -> None:
    # 全消去され得る。呼び出し側は元文へフォールバックする想定。
    assert normalize_for_tts("🎉✨😀") == ""


def test_collapses_inner_spaces() -> None:
    assert normalize_for_tts("こんにちは　　　世界") == "こんにちは 世界"
