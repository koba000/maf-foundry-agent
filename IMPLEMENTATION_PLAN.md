# 実装計画書 — MAF エージェントを Foundry Hosted Agent として新規開発する手順

## 0. この文書について

Microsoft Agent Framework (MAF) でエージェントを作り、**Microsoft Foundry の Hosted Agent**
として動かすための、新規開発者向け手順書。**汎用化してあり、新しいエージェントにそのまま使える。**

最重要は**順序**:

> **azd の雛形生成（scaffold）が最初。実装はその雛形の中で行う。ローカルで green にしてからデプロイする。**

この順序を守らないと `azd ai agent init` が失敗する（§7 の教訓を参照）。

---

## 1. 全体像（4ステップ）

| Step | 内容 | 主なコマンド |
|---|---|---|
| **1. Scaffold** | 空ディレクトリで雛形を生成 | `azd ai agent init` |
| **2. Implement** | 雛形 `main.py` に指示とツールを実装（定義は切り出して共有） | （コード編集） |
| **3. Local verify** | ローカルで起動・推論・**実ファイル分析**まで検証 | `azd ai agent run` / `invoke --local` / file_ids harness |
| **4. Deploy** | provision → deploy、デプロイ後にセッションで最終確認 | `azd provision` / `azd deploy` |

---

## 2. 設計方針

1. **雛形（azd 生成物）には極力手を入れない。** 触るのは `main.py` の中身（指示・ツール）と
   `requirements.txt` だけ。`azure.yaml` / `infra/` / `Dockerfile` は基本そのまま。
2. **エージェント定義は1箇所に集約し、入口とテストで共有する。** `create_agent()` を
   `main.py`（デプロイ入口）とローカル検証 harness の両方から呼ぶ。「ローカルで通ったもの =
   デプロイされるもの」を保証する。差し込みたいものだけ引数に出す（例: `file_ids`, `client`）。
3. **過剰実装を避ける。** 自前 Web フレームワーク・セッション管理・リトライ・SSE は入れない。
   会話履歴・ファイル管理は Foundry のホスト基盤に委譲（`default_options={"store": False}`）。
4. **依存は2系統に分ける。** ランタイム最小 `requirements.txt`（イメージに入る）と、
   開発/テスト用 dev 依存（`tests/requirements-dev.txt`、必要になったら置く）。**テスト依存をイメージに入れない。**
5. **認証は Entra ID。** ローカル実行は `az login`、azd の provision/deploy は `azd auth login`。
   コードにキーを書かない。

---

## 3. 技術スタックと選定理由

| 領域 | 採用 | 理由 |
|---|---|---|
| 言語 | Python 3.13+ | 現行 azd 雛形（Responses/Agent Framework）が 3.13 ベース |
| エージェント | `agent-framework` | MAF 本体 |
| モデル接続 | `agent_framework.foundry.FoundryChatClient` | Foundry の Responses エンドポイントへ直接推論。`instructions`/`tools` をコード側が持てる |
| ホスティング | `agent-framework-foundry-hosting` の `ResponsesHostServer` | Responses 互換 API（`/responses`, :8088）。Foundry がそのままホストできる |
| 認証 | `azure-identity`（`DefaultAzureCredential`） | Entra ID。ローカルと本番で同じコード |
| 設定 | `python-dotenv` + 環境変数 | `.env` をローカルで読み、本番はホスト基盤が env を注入 |
| デプロイ | `azd`（`azure.ai.agents` 拡張）+ `azd provision`/`azd deploy` | 雛形生成 → コンテナを ACR へ → Foundry に Hosted Agent 登録 |
| テスト | 素のスクリプト harness + Files API | 実エンドポイントに対するローカル検証 |

> **API の世代**: 旧 `AzureAIAgentClient` / `AzureAIProjectAgentProvider` 系は Python から
> **削除済み**。現行は `agent_framework.foundry` 配下の `FoundryChatClient`（自前で定義を持つ場合）と
> `FoundryAgent`（定義が Foundry 側にある既存エージェントに接続するだけの場合）。本手順は前者。

---

## 4. 手順（詳細）

### Step 1. Scaffold — 空ディレクトリで雛形を作る

**前提**
- Azure サブスクリプション + Foundry プロジェクト（既存 or 新規）+ プロジェクトに対する適切なロール。
- `azd` と `azure.ai.agents` 拡張（`azd extension list` で確認、無ければ `azd ext install azure.ai.agents`）。
- **Docker は不要**（後述の Remote build を選ぶ）。
- **ログインは2種類**: `az login`（ローカル実行で `DefaultAzureCredential` が使う）と
  `azd auth login`（azd の provision/deploy 用）。

**実行**
```bash
mkdir <your-agent> && cd <your-agent>     # ★ 必ず空ディレクトリで
az login
azd auth login
azd ai agent init
```

**プロンプトの選び方**

| プロンプト | 選ぶもの |
|---|---|
| Language | Python |
| Starter template | Basic agent (Responses, Agent Framework, Python) |
| Deployment type | Container deploy |
| Dependency resolution | **Remote build**（サーバ側ビルド＝Docker 不要） |
| Foundry Project | 既存を選択 or 新規作成 |
| Model deployment | 使うモデルデプロイ（既存を選ぶ or 新規） |

**生成物**
```
<your-agent>/
├── azure.yaml                 # サービス定義（project: src/<agent>, remoteBuild, モデル）
├── infra/                     # bicep
└── src/<agent>/
    ├── main.py                # 入口（ResponsesHostServer を起動）
    ├── agent.yaml             # kind: hosted / responses / AZURE_AI_MODEL_DEPLOYMENT_NAME
    ├── Dockerfile             # CMD ["python","main.py"]、EXPOSE 8088
    ├── requirements.txt       # ランタイム依存
    ├── .dockerignore / .agentignore
    └── README.md
```

### Step 2. Implement — 雛形の中に「中身」を入れる

雛形の `main.py` は「フレンドリーなアシスタント」のサンプル。ここに**指示とツール**を入れる。
**定義は切り出して入口とテストで共有する**（§5）。

```python
# src/<agent>/agent_def.py — 定義（入口とテストで共有する単一の真実）
import os
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

INSTRUCTIONS = "..."  # システム指示

def create_chat_client(client=None):
    return FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    )

def create_agent(file_ids=None, client=None) -> Agent:
    client = client or create_chat_client()
    return Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=FoundryChatClient.get_code_interpreter_tool(file_ids=file_ids),
        default_options={"store": False},   # 履歴はホスト基盤が管理
    )
```

```python
# src/<agent>/main.py — 入口は薄く保つ
from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv
from agent_def import create_agent

load_dotenv()

def main():
    ResponsesHostServer(create_agent()).run()   # file_ids なし＝デプロイ用

if __name__ == "__main__":
    main()
```

- **`requirements.txt` を import に合わせる。** 雛形の既定は最小なので、`main.py`/`agent_def.py` が
  import するものを満たす（例: `agent-framework`, `agent-framework-foundry`,
  `agent-framework-foundry-hosting`, `azure-identity`, `python-dotenv`）。
  **テスト/開発専用の依存はここに入れない（`tests/requirements-dev.txt` 側へ）。**
- ツールは `FoundryChatClient.get_code_interpreter_tool()` / `get_web_search_tool()` 等の
  静的ファクトリ、またはプロセス内で動かす `@tool` 関数。

### Step 3. Local verify — ローカルで green にする

`.env`（`FOUNDRY_PROJECT_ENDPOINT` / `AZURE_AI_MODEL_DEPLOYMENT_NAME`）を `src/<agent>/` に置く。

1. **起動 + 疎通**
   ```bash
   azd ai agent run                       # venv 作成 + 依存 install + 起動
   azd ai agent invoke --local "自己紹介して"   # 別ターミナル
   ```
   → 起動できるか・`requirements.txt` の過不足・推論・出力言語・Code Interpreter 実行を確認。

2. **実ファイル分析（最重要）** — file_ids を使うローカル harness で検証（§5）。
   harness 用の `.venv` は **uv** で作る:
   ```bash
   uv venv .venv --python 3.13 && source .venv/bin/activate
   uv pip install --prerelease=allow -r requirements.txt   # dev 追加依存があれば -r tests/requirements-dev.txt も
   ```
   - 任意のサンプルファイルと質問を引数で渡す: `python tests/verify_local.py <file.xlsx> "質問" ...`。
   - harness が file_id を取得し `create_agent(file_ids=[file_id])` を駆動、回答をそのまま表示する。
   - 回答が正しいか（事実・意図したツール実行・出力言語）は**表示内容を目視で確認**する。

   なぜ harness が必要か: デプロイ用 `main.py` は `file_ids` を渡さない（ファイルはセッション経由前提）。
   ローカルで**ファイル分析まで**再現するには file_ids 経路が要る（§6 の供給経路の差）。

**green になるまでデプロイしない。**

### Step 4. Deploy — provision → deploy → 最終確認

```bash
azd provision        # Foundry/ACR/マネージドID 等を用意（azd up で provision+deploy をまとめても可）
azd deploy           # Remote build → ACR push → Hosted Agent 登録
azd ai agent show    # ステータスが Active か
azd ai agent invoke "こんにちは"   # 疎通
azd ai agent doctor  # 詰まったとき診断
```

> `azd ai agent up` というコマンドは**存在しない**。デプロイは `azd provision` + `azd deploy`（= `azd up`）。

**ファイル分析の最終確認（デプロイ後）**
```bash
azd ai agent files upload <local-file>          # Hosted Agent セッションへ投入
azd ai agent invoke "アップロードしたファイルの概要を教えて"
```
- ⬜ **セッションへアップロードしたファイルが `get_code_interpreter_tool()`（file_ids 無し）から
  参照できるか**を実機確認。見えなければ **Foundry Toolbox 経由の Code Interpreter** に切り替える。

---

## 5. 検証アーキテクチャ — 入口を汚さずに共有する

ファイル分析まで検証するには「定義の共有」が鍵。**別テストに定義を二重持ちしない**
（INSTRUCTIONS が片方だけ更新されて食い違うため）。

```
src/<agent>/
├── main.py            # デプロイ入口。create_agent() を file_ids 無しで起動
├── agent_def.py       # ★ INSTRUCTIONS + create_agent(file_ids=None, client=None) ＝単一の真実
├── requirements.txt   # ランタイム最小（イメージに入る）
└── tests/             # ★ ローカル検証専用（イメージから除外）
    ├── verify_local.py    # Files API でアップロード→file_ids付き create_agent→agent.run()→検証
    └── requirements-dev.txt   # dev 専用の追加依存（必要になったら置く）
```

- `main.py` と `tests/verify_local.py` が**同じ `create_agent()`** を import。差し込むのは `file_ids` だけ。
- **イメージから除外**: `.dockerignore`（Container deploy のビルド文脈）に `tests/` を追加。
  Code deploy を使う場合は `.agentignore` 側で除外する。
- dev 依存は **uv 管理のローカル `.venv`** にだけ入れる
  （`uv pip install --prerelease=allow -r tests/requirements-dev.txt`）。

---

## 6. ファイル供給の落とし穴（ローカル vs デプロイ）

Code Interpreter にファイルを渡す経路が**ローカルとデプロイで異なる**:

- **ローカル**: Files API にアップロードして `file_id` を取得し、
  `get_code_interpreter_tool(file_ids=[id])` で渡す。
  ```python
  openai_client = client.project_client.get_openai_client()
  uploaded = await openai_client.files.create(file=f, purpose="assistants")  # purpose は assistants
  ```
- **デプロイ後**: ファイルは **Hosted Agent セッション**に乗せる（`azd ai agent files upload` /
  Foundry ポータル）。`get_code_interpreter_tool()` は `file_ids` なしで構築する。
- ⚠️ そのため、デプロイ後にセッションファイルが Code Interpreter から見えるかは**実機で確認**し、
  見えなければ **Foundry Toolbox 経由の Code Interpreter** に切り替える。

---

## 7. 教訓（次の人へ）

- **scaffold 先行・空ディレクトリ。** 実装を先に手作りしてから `azd ai agent init` を被せると、
  manifest がプロジェクトルートにある状態で「雛形を既存ファイルの中にコピーできない」と落ちる。
  **init を空ディレクトリで先に走らせ、生成された雛形の中に実装する。**
- **雛形には最小介入。** 触るのは `main.py` の中身と `requirements.txt`。生成された
  `azure.yaml`/`infra`/`Dockerfile` は基本そのまま。
- **定義は1箇所・共有。** 入口とテストで `create_agent()` を共有し、二重持ちしない。
- **ログインは2種類。** `az login`（ローカル実行）と `azd auth login`（provision/deploy）。
- **`store: False`。** 履歴はホスト基盤が持つ。自前で会話 ID を管理しない。
- **依存を分ける。** ランタイム `requirements.txt` と dev 依存。テスト依存をイメージに入れない。
- **ファイル経路の差。** ローカルは file_ids、デプロイはセッション。ファイル分析の最終確認は実機で。
- **green までデプロイしない。**

---

## 8. 参照

- MAF docs:「Microsoft Foundry provider」「Code Interpreter」「Hosted agents in Foundry Agent Service」
- Foundry Hosted Agent クイックスタート（azd）:
  `https://learn.microsoft.com/azure/foundry/agents/quickstarts/quickstart-hosted-agent?pivots=azd`
- MAF samples (github.com/microsoft/agent-framework):
  - `python/samples/02-agents/providers/foundry/`（FoundryChatClient + 各ツール、file_ids 付き CI）
  - `python/samples/04-hosting/foundry-hosted-agents/responses/`（ResponsesHostServer + Dockerfile + agent.yaml）
