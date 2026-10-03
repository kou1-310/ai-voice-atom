"""Moonshine（STT）のネイティブライブラリを PyPI の wheel から取り出して配置する。

host は ``moonshine-voice`` を submodule（``third_party/moonshine/python``）から editable で
使うが、submodule にはコンパイル済みのネイティブライブラリ（Windows: ``moonshine.dll`` /
``onnxruntime.dll``、Linux: ``libmoonshine.so``、macOS: ``libmoonshine.dylib``）が含まれない。
無いと STT 初期化時に ``Failed to load Moonshine library`` で失敗するので、submodule と同じ
版数の wheel をダウンロードし、ライブラリだけを ``python/src/moonshine_voice/`` に置く
（upstream もこの場所を想定しており、submodule の .gitignore で除外済み）。
Linux の wheel は依存する onnxruntime を ``moonshine_voice.libs/`` に同梱している
（``libmoonshine.so`` の RPATH が ``$ORIGIN/../moonshine_voice.libs``）ので、それも
``python/src/moonshine_voice.libs/`` に置き、submodule の ``info/exclude`` で Git から隠す。

使い方（host/ から。標準ライブラリのみなので uv の環境が無くても動く）::

    uv run python scripts/fetch_moonshine_native.py            # 未配置なら取得
    uv run python scripts/fetch_moonshine_native.py --force    # 取り直す

wheel は 60〜90MB あるので数分かかることがある。
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MOONSHINE_PYTHON = REPO_ROOT / "third_party" / "moonshine" / "python"
PACKAGE_DIR = MOONSHINE_PYTHON / "src" / "moonshine_voice"

# OS ごとに Moonshine が読み込むライブラリ名（moonshine_api.py の _load_library と同じ）。
LIBRARY_NAME = {
    "Windows": "moonshine.dll",
    "Linux": "libmoonshine.so",
    "Darwin": "libmoonshine.dylib",
}
# OS ごとに取り出す拡張子。wheel によっては他 OS 用のライブラリも同梱されているので絞る。
LIBRARY_SUFFIXES = {"Windows": (".dll",), "Linux": (".so",), "Darwin": (".dylib",)}


def platform_key(system: str, machine: str) -> tuple[str, ...]:
    """wheel のファイル名に含まれるべき文字列の組を返す（すべて含むものを選ぶ）。"""
    machine = machine.lower()
    if system == "Windows" and machine in ("amd64", "x86_64"):
        return ("win_amd64",)
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return ("manylinux", "x86_64")
    if system == "Linux" and machine in ("aarch64", "arm64"):
        return ("manylinux", "aarch64")
    if system == "Darwin" and machine == "arm64":
        return ("macosx", "arm64")
    raise RuntimeError(f"この環境（{system} {machine}）向けの Moonshine wheel はありません")


def select_wheel(files: list[dict], key: tuple[str, ...]) -> dict:
    """PyPI の files 一覧から key の文字列をすべて含む wheel を選ぶ。"""
    for info in files:
        name = info["filename"]
        if name.endswith(".whl") and all(k in name for k in key):
            return info
    raise RuntimeError(f"条件 {key} に合う wheel が PyPI に見つかりません")


# Linux wheel が依存ライブラリを入れているディレクトリ（auditwheel の命名）。
BUNDLED_LIBS_DIR = "moonshine_voice.libs"


def is_native_member(member: str, system: str) -> bool:
    """wheel 内のパスが、この OS 用のネイティブライブラリか。

    対象は ``moonshine_voice/`` 直下のライブラリと、Linux の ``moonshine_voice.libs/`` の中身。
    """
    parts = member.split("/")
    if len(parts) != 2 or not parts[1]:
        return False
    if system == "Linux" and parts[0] == BUNDLED_LIBS_DIR:
        return True
    if parts[0] != "moonshine_voice":
        return False
    name = parts[1]
    suffixes = LIBRARY_SUFFIXES.get(system, ())
    # Linux は libonnxruntime.so.1 のような版数付きの名前もある
    return name.endswith(suffixes) or (system == "Linux" and ".so." in name)


def hide_from_submodule_git(entry: str) -> None:
    """submodule の info/exclude に entry を足し、配置物で submodule が変更扱いにならないようにする。"""
    try:
        path = subprocess.run(
            ["git", "-C", str(MOONSHINE_PYTHON.parent), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return  # git が無い・submodule でない場合は何もしない（動作には影響しない）
    exclude = Path(path)
    if not exclude.is_absolute():
        exclude = MOONSHINE_PYTHON.parent / exclude
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if entry not in current.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(current.rstrip("\n") + f"\n{entry}\n", encoding="utf-8")


def read_version() -> str:
    """submodule の pyproject.toml から moonshine-voice の版数を読む（wheel と揃えるため）。"""
    text = (MOONSHINE_PYTHON / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if match is None:
        raise RuntimeError("third_party/moonshine/python/pyproject.toml から版数を読めません")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="配置済みでも取り直す")
    args = parser.parse_args()

    if not PACKAGE_DIR.exists():
        print(
            "Moonshine の submodule がありません。先に "
            "`git submodule update --init --recursive` を実行してください。",
            file=sys.stderr,
        )
        return 1

    system = platform.system()
    target = PACKAGE_DIR / LIBRARY_NAME.get(system, "")
    if target.is_file() and not args.force:
        print(f"[moonshine] 配置済みです: {target}（取り直すときは --force）")
        return 0

    version = read_version()
    key = platform_key(system, platform.machine())
    print(f"[moonshine] moonshine-voice {version} の wheel を探しています（{' / '.join(key)}）")
    with urllib.request.urlopen(f"https://pypi.org/pypi/moonshine-voice/{version}/json") as r:
        files = json.load(r)["urls"]
    wheel = select_wheel(files, key)
    size_mb = wheel["size"] / 1024 / 1024
    print(f"[moonshine] ダウンロード中: {wheel['filename']}（約 {size_mb:.0f}MB）")
    with urllib.request.urlopen(wheel["url"]) as r:
        data = r.read()

    extracted = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            if is_native_member(member, system):
                dest = PACKAGE_DIR.parent / member
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
                extracted.append(member)
    if not extracted:
        print("[moonshine] wheel にネイティブライブラリが見つかりませんでした", file=sys.stderr)
        return 1
    if any(m.startswith(BUNDLED_LIBS_DIR + "/") for m in extracted):
        hide_from_submodule_git(f"/python/src/{BUNDLED_LIBS_DIR}/")
    print(f"[moonshine] 配置しました: {', '.join(extracted)} → {PACKAGE_DIR.parent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
