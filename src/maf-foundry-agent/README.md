# maf-foundry-agent

任意の Excel / CSV（1ファイル）をアップロードし、自然言語で集計・分析・可視化を依頼できる
データ分析エージェント。Microsoft Agent Framework (MAF) + Foundry の **Code Interpreter** を使い、
**Foundry Hosted Agent**（Responses プロトコル）として動かす。ユーザー向け出力はすべて日本語。

フロントエンドや SSE は持たない。Agent 本体（ホスト入口 + 定義）のみ。

> 開発ルール（init 先行・定義共有・依存分離）は [../../CLAUDE.md](../../CLAUDE.md)、汎用手順書は
> [../../IMPLEMENTATION_PLAN.md](../../IMPLEMENTATION_PLAN.md)、プロジェクト全体の進捗は
> [../../README.md](../../README.md) を参照。

## アーキテクチャ

```
[Responses クライアント / azd ai agent invoke]
        │ POST /responses
        ▼
[ResponsesHostServer]  ← main.py（Foundry がホスト・履歴/ファイルを管理）
        │
        ▼
[Agent(FoundryChatClient) + Code Interpreter]  ← agent_def.py（create_agent）
        │
        ▼
[Microsoft Foundry project（gpt-5.4 + サンドボックス Python 実行）]
```

- 推論クライアント: `agent_framework.foundry.FoundryChatClient`（Entra ID 認証）
- ツール: `FoundryChatClient.get_code_interpreter_tool(file_ids=...)`（サンドボックス Python）。
  **`CodeInterpreterFileInjector` middleware が run 毎に組み立てて注入**する（既定 tools には置かない）
- ファイル経路は2つ（詳細は [../../FILE_ANALYSIS_HANDOFF.md](../../FILE_ANALYSIS_HANDOFF.md)）:
  1. **デプロイ後（実用経路）**: `azd ai agent files upload` → セッションFSがコンテナの
     `/home/session` にマウント → middleware がコンテナ内から Files API にアップロードして
     file_ids を CI へ注入（Foundry ゲートウェイは `input_file` 付き `/responses` を 500 で落とすため）
  2. **`/responses` の `input_file`（`file_id`）**: ホスティング層が hosted_file Content に変換 →
     middleware が file_ids を抽出して CI へ注入。ローカル検証はこちらの経路
- ホスティング: `agent_framework_foundry_hosting.ResponsesHostServer`（`:8088` で `/responses`）
- 単一の `create_agent()`（[agent_def.py](agent_def.py)）を**ホスト入口とローカル検証の両方で共有**し、
  INSTRUCTIONS の二重持ちを防ぐ

## 技術スタック

| 領域 | 採用 |
|---|---|
| フレームワーク | Microsoft Agent Framework（`agent-framework` 1.x） |
| 推論プロバイダ | Microsoft Foundry（`agent-framework-foundry`） |
| ホスティング | `agent-framework-foundry-hosting`（Responses プロトコル） |
| モデル | **gpt-5.4**（[agent.yaml](agent.yaml) / [../../azure.yaml](../../azure.yaml) と一致） |
| 認証 | Entra ID（`azure-identity` の `DefaultAzureCredential`）。API キーは使わない |
| ランタイム | コンテナ `python:3.12-slim`（[Dockerfile](Dockerfile)）。ローカル検証は Python 3.13 |
| ローカル環境管理 | **uv**（`uv venv` + `uv pip install --prerelease=allow`）。`.venv` は uv 管理 |
| ビルド/デプロイ | `azd`（+ `azure.ai.agents` 拡張）。**Remote build**＝ローカル Docker 不要 |
| dev（検証のみ） | 追加依存なし（[tests/requirements-dev.txt](tests/requirements-dev.txt)）。検証は任意のサンプルファイルを持ち込む |

## プロジェクト構成

```
src/maf-foundry-agent/
├── agent_def.py        # ★ INSTRUCTIONS + CodeInterpreterFileInjector + create_agent(client) ＝単一の真実
├── main.py             # デプロイ入口。create_agent() を ResponsesHostServer に渡す薄い入口
├── requirements.txt    # コンテナ最小ランタイム依存（dev/テスト依存は入れない）
├── Dockerfile          # python:3.12-slim / EXPOSE 8088 / CMD python main.py
├── agent.yaml          # kind: hosted / Responses / gpt-5.4
├── .dockerignore       # .venv/.env と tests/ をイメージから除外
└── tests/              # ★ ローカル検証専用（イメージから除外）
    ├── verify_local.py     # 任意ファイルを引数で受け、Files API アップロード → hosted_file 添付 → agent.run()
    └── requirements-dev.txt # dev 専用の追加依存（現状なし）
```

## 必要環境

- [uv](https://docs.astral.sh/uv/)（ローカル環境・依存管理。`.venv` は uv で作る）
- Python 3.13（uv が用意）/ Azure CLI（`az login` 済み — ローカルは Entra ID 認証）
- Microsoft Foundry プロジェクト（gpt-5.4 デプロイ済み）
- `azd` + `azure.ai.agents` 拡張（デプロイ時）
- `.env`（このディレクトリ。`.dockerignore` で除外済み）:
  ```env
  FOUNDRY_PROJECT_ENDPOINT="https://<your-project>.services.ai.azure.com"
  AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-5.4"
  ```

## 実装フェーズ

- [x] **A. Scaffold + 実装** — `azd ai agent init` の雛形に、日本語 INSTRUCTIONS + Code Interpreter を実装。
      定義を [agent_def.py](agent_def.py) に集約し、[main.py](main.py) は薄い入口に。
- [x] **B. ローカル検証** — 実エンドポイントに対し file_ids 経路で Excel 分析を確認。**green 済み**。
      任意のサンプルファイルと質問を引数で渡して `verify_local.py` で検証する。
- [x] **C. デプロイ** — `azd provision` → `azd deploy`（= `azd up`）→ `azd ai agent show` で Active 確認。
      **green 済み**（version 5, 2026-07-04）。`azd ai agent invoke "こんにちは"` で応答を確認。
- [x] **D. デプロイ後のファイル分析確認** — **green 済み（2026-07-04, version 9）**。
      `azd ai agent files upload` → `azd ai agent invoke "...合計を教えて"` で実データに基づく
      正答（検算一致）とマルチターン継続を確認。実現方式: セッションFSはコンテナの
      `/home/session` にマウントされるため、`CodeInterpreterFileInjector` middleware が
      run 毎にそこを走査して Files API にアップロードし、file_ids を CI へ注入する。
      経緯（ゲートウェイの `input_file` 500 問題など）は
      [../../FILE_ANALYSIS_HANDOFF.md](../../FILE_ANALYSIS_HANDOFF.md) を参照。

## ローカル検証（少額の実モデル課金が発生）

「ローカルで通ったもの＝デプロイされるもの」を満たすため、`main.py` と同じ `create_agent()` を
直接駆動して実エンドポイントで green にする。

`.venv` は **uv** で管理する（作成・依存インストールとも uv）。

```bash
cd src/maf-foundry-agent
az login                          # 未ログインなら
uv venv .venv --python 3.13 && source .venv/bin/activate
uv pip install --prerelease=allow -r requirements.txt

# 実ファイル分析（最重要）— 任意のサンプルファイルと質問を引数で渡す
#   python tests/verify_local.py <file.xlsx> "質問1" ["質問2" ...]
python tests/verify_local.py ~/data/uriage.xlsx "売上の合計は？" "月別に集計して"
```

> プレリリースの `agent-framework` 系を入れるため `uv pip install` には `--prerelease=allow` が必須。

`verify_local.py` の動き:

- 引数の `<file.xlsx>` を Files API にアップロードして file_id を取得し、デプロイ入口と同じ
  `create_agent()` に **`Content.from_hosted_file(file_id)` を添付したメッセージ**で駆動する。
  middleware が file_ids を CI へ注入する経路そのものを検証する（「ローカルで通った＝デプロイされる」）。
- 続く引数の質問は「履歴 + 新質問」を毎回 messages で渡す（デプロイ時のホスティング層と同じ渡し方。
  2問目以降で履歴中の添付から file_ids が引き継がれることも検証される）。
- 引数不足・ファイル不在のときは usage を表示して `exit 2`。実モデル課金を避けるため質問は明示指定。

> 固定の仮データ（旧 `make_fixture.py`）は廃止。任意のファイルを持ち込んで検証する。
> 回答が正しいかは表示された内容を目視で確認する（LLM 出力は揺れるため）。

> ローカルのホストサーバ（`python main.py`）への素の `/responses` POST も、`input_file`
> （`file_id` 付き）を含めればファイル分析まで動く。**動かないのはデプロイ後のゲートウェイ経由の
> `input_file` だけ**（下記「既知の落とし穴」）。

### 起動スモーク

```bash
azd ai agent run                         # venv 作成 + 依存 install + 起動（:8088）
azd ai agent invoke --local "自己紹介して"   # 別ターミナル（venv 不要）
```

## デプロイ（Hosted Agent）

**B が green になってから**実施する。`azure.yaml` / `infra/` / `Dockerfile` / `agent.yaml` は
`azd ai agent init` 生成のものをそのまま使う。

```bash
cd ../..                 # リポジトリルート
azd auth login           # ※ az login とは別。2種類のログインが要る
azd provision            # Foundry/ACR/マネージドID 等を用意
azd deploy               # Remote build → ACR push → Hosted Agent 登録
azd ai agent show        # ステータスが Active か
azd ai agent invoke "こんにちは"
```

> `azd ai agent up` は**存在しない**。デプロイは `azd provision` + `azd deploy`（= `azd up`）。
> 詰まったら `azd ai agent doctor`。

デプロイ後のファイル分析（フェーズ D）:

```bash
azd ai agent files upload <local.xlsx>   # Hosted Agent セッションへ投入（/home/session に載る）
azd ai agent invoke "アップロードしたファイルの概要を教えて"
```

> セッションは連続 invoke で自動継続する。`files upload` は「直近の invoke セッション」に入るため、
> 新しいセッションで始めたいときは先に `azd ai agent invoke --new-session "こんにちは"` などで
> セッションを作ってから upload する。

## 既知の落とし穴

- **デプロイ後のゲートウェイは `input_file` 付き `/responses` を 500 で落とす**（2026-07-04 実機確認。
  コンテナに届く前にゲートウェイ側で失敗し、`azd ai agent invoke -f` 経由だと file 部分が黙って
  落ちる）。デプロイ後のファイル投入は `azd ai agent files upload`（セッションFS）を使う。
  ローカルの `/responses` POST では `input_file` はそのまま動く。
- **hosted_file Content をモデル入力に残すと 400 (unsupported_file)**（PDF 以外の `input_file` 直接入力は
  Responses API が拒否）。middleware が file_ids 抽出後にテキストの目印へ置き換えている。
- **プラットフォーム側の一時障害**: デプロイ直後にコンテナ内マネージド ID がトークンを取れず
  テキスト対話まで全部 500 になることがある（`azd ai agent monitor` に
  `DefaultAzureCredential failed ... ManagedIdentityCredential: No token received`）。
  コード起因ではないので、再デプロイでインスタンスを作り直すと直る。
- **ログイン2種**を忘れない（`az login` と `azd auth login`）。
- `requirements.txt` はランタイム最小に保つ（dev/テスト依存を入れない）。dev 依存は `tests/requirements-dev.txt`。
- 依存管理は **uv** に統一。`uv pip install` でプレリリースを入れるには `--prerelease=allow` が必須
  （`.venv` も `uv venv` で作る）。素の `pip` を使う場合は `--prerelease=allow` ではなく `--pre`。
- ローカル検証ログに `Can't parse tool.` という警告が出ることがあるが**無害**
  （`agent_framework/_tools.py` の logger.warning。Code Interpreter は正常に動く）。
- `.env` を git にコミットしない（`.gitignore` で除外済み）。
