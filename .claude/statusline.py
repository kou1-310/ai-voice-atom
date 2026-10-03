#!/usr/bin/env python3
"""Claude Code ステータスライン: コンテキスト使用量を表示する。

Claude Code は stdin にセッション情報の JSON を渡してこのスクリプトを実行する。
transcript（JSONL）から直近のアシスタントメッセージの usage を読み取り、
コンテキストウィンドウに対する使用割合をステータスバーへ出力する。
"""
import json
import os
import sys


def _read_stdin_json() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}


def _context_limit(data: dict) -> int:
    """コンテキストウィンドウ上限（トークン）を返す。

    Claude Code が stdin の `context_window.context_window_size` で実際の上限を
    渡すのでそれを最優先する。無い古いバージョン向けにモデル ID から推定する。
    """
    size = (data.get("context_window") or {}).get("context_window_size")
    if isinstance(size, int) and size > 0:
        return size
    model_id = (data.get("model") or {}).get("id", "") or ""
    # 1M コンテキスト対応モデルの簡易判定（必要に応じて拡張）。
    if "[1m]" in model_id.lower():
        return 1_000_000
    return 200_000


def _usage_from_stdin(data: dict) -> int | None:
    """stdin の `context_window.current_usage` から現在のコンテキスト消費量を得る。"""
    usage = (data.get("context_window") or {}).get("current_usage")
    if not isinstance(usage, dict):
        return None
    total = (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
    )
    return total if total > 0 else None


def _used_tokens(transcript_path: str) -> int | None:
    """transcript の末尾から、直近のアシスタント usage を探して合算する。

    input_tokens + cache_read + cache_creation がそのリクエスト時点での
    コンテキスト消費量に相当する（output は次ターンの入力になる前なので除く）。
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        # サイドチェーン（サブエージェント）の usage はメイン文脈ではないので除外。
        if entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        total = (
            usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
        if total > 0:
            return total
    return None


def _bar(pct: float, width: int = 10) -> str:
    filled = int(round(pct / 100 * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


# ANSI カラー（端末が対応していれば色付き表示になる）。
_RESET = "\033[0m"
_DIM = "\033[2m"


def _color(pct: float) -> str:
    if pct >= 90:
        return "\033[31m"  # 赤
    if pct >= 70:
        return "\033[33m"  # 黄
    return "\033[32m"      # 緑


def _fmt_duration(ms: float) -> str:
    """経過時間（ミリ秒）を h/m/s の読みやすい形へ整形する。"""
    secs = int(ms / 1000)
    if secs < 60:
        return f"{secs}s"
    mins, s = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m{s:02d}s"
    h, m = divmod(mins, 60)
    return f"{h}h{m:02d}m"


def _session_segment(data: dict) -> str:
    """セッションの使用状況（コスト・経過時間・変更行数）を組み立てる。"""
    cost = data.get("cost") or {}
    parts: list[str] = []

    usd = cost.get("total_cost_usd")
    if isinstance(usd, (int, float)):
        parts.append(f"{_DIM}${usd:.3f}{_RESET}")

    dur = cost.get("total_duration_ms")
    if isinstance(dur, (int, float)) and dur > 0:
        parts.append(f"{_DIM}{_fmt_duration(dur)}{_RESET}")

    added = cost.get("total_lines_added", 0) or 0
    removed = cost.get("total_lines_removed", 0) or 0
    if added or removed:
        parts.append(f"\033[32m+{added}{_RESET}/\033[31m-{removed}{_RESET}")

    if not parts:
        return ""
    return f" {_DIM}|{_RESET} " + " ".join(parts)


def main() -> None:
    data = _read_stdin_json()
    limit = _context_limit(data)
    used = _usage_from_stdin(data)
    if used is None:
        used = _used_tokens((data.get("transcript_path") or ""))

    model_name = (data.get("model") or {}).get("display_name", "Claude")
    session = _session_segment(data)

    if used is None:
        # まだ usage が無い（セッション開始直後など）。
        sys.stdout.write(f"{_DIM}{model_name} | context: --{_RESET}{session}")
        return

    pct = used / limit * 100
    used_k = used / 1000
    limit_k = limit / 1000
    col = _color(pct)

    sys.stdout.write(
        f"{_DIM}{model_name}{_RESET} | "
        f"{col}{_bar(pct)} {pct:4.1f}%{_RESET} "
        f"{_DIM}({used_k:.1f}k/{limit_k:.0f}k){_RESET}"
        f"{session}"
    )


if __name__ == "__main__":
    main()
