# パフォーマンス調整 — PCが重いときの最適化

> **この文書の位置づけ**: 会話中に PC が重い・返事が遅いときの調整方法です。
> ほかの文書は [docs/README.md](README.md) から探せます。

LLM（Ollama）と TTS（irodori サイドカー）を同じPCで動かすと、会話中にPCが重くなることがある。
本書はその原因と、快適に動かすための設定をまとめる。

## なぜ重くなるか

主因は **1枚のGPUを「画面描画・LLM・TTS」で奪い合う** こと。多くの構成で GPU はモニター出力も
兼ねているため、会話中に Ollama と irodori TTS が交互にGPUを連打すると、Windowsの画面合成が
同じGPUを取り合ってデスクトップがカクつく。とくにWebの自動会話は STT を挟まず LLM→TTS を
休みなく連打するので顕著。副次的に、各プロセスの **CPUスレッドが全コアを張り付かせる** ことも
体感の重さにつながる。

GPU/CPU の使用状況は会話を回しながら確認できる:

```bash
nvidia-smi -l 1          # GPUのVRAM/使用率（Ollama と tts_server が両方載っているか）
```

## A. TTSをbf16にする（GPU負荷を約半減）

irodori サイドカーは既定で **fp32**（高精度）。`bf16` にすると VRAM と演算がおよそ半分になり、
GPU競合が和らぐ。音質はわずかに変わりうるので、重さが気になるとき・GPUが手狭なときに有効化する。

```bash
# host/.env
AI_VOICE_ATOM_TTS_PRECISION=bf16
```

サイドカーを再起動（ホストを再起動）すると反映される。起動ログに
`loading model on device=cuda precision=bf16` と出れば適用済み。CPUデバイスでは fp32 に自動退避する。

## B. CPUスレッドの上限を設ける

TTSサイドカーの torch は既定で全コアを使おうとする。OS/デスクトップに余力を残すため上限を設ける
（例: 12コアなら 8 程度）。

```bash
# host/.env
AI_VOICE_ATOM_TTS_TORCH_THREADS=8
```

ホストプロセス（moonshine STT 等）側を絞りたい場合は、起動シェルで標準の環境変数を併用する:

```bash
OMP_NUM_THREADS=8 uv run uvicorn app.main:app --host 0.0.0.0
```

## C. Ollama（LLM）の設定

LLM側のVRAM・再ロードのムダを減らすと、TTSとのGPU競合が軽くなる。Ollama を動かしている環境
（多くは別プロセス／サービス）の環境変数で設定する。

| 環境変数 | 推奨 | 効果 |
|---|---|---|
| `OLLAMA_KEEP_ALIVE` | `30m` など長め | モデルをVRAMに保持し、ターンごとの再ロード（ディスク+VRAMのスラッシング）を防ぐ |
| `OLLAMA_NUM_PARALLEL` | `1` | 同時実行を1に固定し、VRAMの余計な確保を防ぐ |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | 複数モデルの常駐を防ぐ |

さらに効く施策:

- **量子化モデルを使う**: 同じ gemma でも Q4 量子化版（例 `gemma3:4b` の Q4_K_M）にすると
  VRAM が大きく減り、TTSと同居しやすくなる。会話用途では体感品質の低下は小さい。
- **コンテキスト長を欲張らない**: 音声会話は短文中心。`num_ctx` を小さめ（例 2048）にすると
  VRAM とレイテンシが下がる。本プロジェクトは `AI_VOICE_ATOM_LLM_MAX_TOKENS`（既定512）で
  出力長を既に抑えている。

Windows でサービスとして動かしている場合は、ユーザー環境変数に設定してOllamaを再起動する。

### VRAM配分の目安（RTX 4060 Ti / 16GB の例）

- irodori TTS（500Mモデル）: fp32 で数GB、bf16 でその約半分
- Ollama gemma（4B・Q4）: 3〜4GB 程度

両方を載せても 16GB に収まる構成。`nvidia-smi` で両プロセスが載り、ターンごとに
モデルが載り替わっていない（KEEP_ALIVE が効いている）ことを確認する。

## D. 常用時は `--reload` を外す（任意）

`uvicorn --reload` はファイル監視が常時CPUを舐める。開発時以外は外すと軽くなる。

```bash
uv run uvicorn app.main:app --host 0.0.0.0      # --reload なし
```

## 効果確認のすすめ

1台ずつ変えて `nvidia-smi -l 1` を見ながら会話を回すと、どの施策が効いたか切り分けやすい。
まず A（bf16）と C（KEEP_ALIVE/量子化）でGPU競合を下げ、まだCPUが張り付くなら B を足す、の順が分かりやすい。
