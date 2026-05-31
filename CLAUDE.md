# CLAUDE.md — Microsoft Agent Framework (MAF) + Foundry Hosted Agent の作り方

Claude Code 向けの開発指示書。**MAF でエージェントを作り、Microsoft Foundry に Hosted Agent
として載せる**プロジェクトで参照する。新規プロジェクトにそのままコピーして使えるよう汎用化してある。

---

## 0. 最重要原則: まずシンプルに作る

**過剰実装を絶対に避ける。** 最小構成でまず動かし、テストを通すことを最優先する。

- 入れない: 独自の Web フレームワーク (FastAPI 等)、独自セッション管理、SSE、独自リトライ、
  リッチなエラーハンドリング、設定切替 UI、図やファイルの自前配信。
- 委譲する: 会話履歴・ファイル管理・スケーリングは **Foundry のホスト基盤に任せる**。
- 足すのは「テストが要求したときだけ」。先回りして作らない。

迷ったら「この行は無くてもテストが通るか？」を問う。通るなら消す。

---

## 1. 使うライブラリ（現行 API）

ローカル環境・依存は **uv** で管理する（`.venv` は `uv venv`、インストールは `uv pip install`）。
プレリリースの `agent-framework` 系を入れるため `--prerelease=allow` を付ける:

```bash
uv venv .venv --python 3.13 && source .venv/bin/activate
uv pip install --prerelease=allow agent-framework agent-framework-foundry agent-framework-foundry-hosting azure-identity python-dotenv
```

- 推論クライアント: `agent_framework.foundry.FoundryChatClient`
- エージェント: `agent_framework.Agent`
- ホスティング: `agent_framework_foundry_hosting.ResponsesHostServer`
- 認証: `azure.identity.DefaultAzureCredential`

> ⚠️ **古い API を使わない。** `AzureAIAgentClient` / `AzureAIProjectAgentProvider` /
> `AzureAIAgentsProvider` などは Python から**削除済み**。`agent_framework.foundry` 配下を使うこと。

**FoundryChatClient と FoundryAgent の使い分け:**

| 使う | こういうとき |
|---|---|
| `Agent(client=FoundryChatClient(...))` | instructions・tools・会話ループを**自分のコードで持つ**（基本こちら） |
| `FoundryAgent(...)` | 定義が Foundry 側にある既存エージェントに**接続するだけ** |

---

## 2. プロジェクト構成（azd 雛形 + 自分で足すもの）

**構成は `azd ai agent init` が生成する。** 自分でゼロから作らない。生成物に最小限を足すだけ:

```
<your-agent>/                  # ← 空ディレクトリで azd ai agent init して生成
├── azure.yaml                 # (init) project: src/<agent> / remoteBuild / モデル
├── infra/                     # (init) bicep
└── src/<agent>/
    ├── main.py                # (init) 入口。ここに「中身」を実装する
    ├── agent_def.py           # ★ 自分で追加: INSTRUCTIONS + create_agent()（入口とテストで共有）
    ├── agent.yaml             # (init) kind: hosted / responses / AZURE_AI_MODEL_DEPLOYMENT_NAME
    ├── Dockerfile             # (init) CMD ["python","main.py"]、EXPOSE 8088
    ├── requirements.txt       # (init+自分) ランタイム最小（dev/テスト依存は入れない）
    ├── .dockerignore          # tests/ を足してイメージから除外
    ├── .agentignore
    └── tests/                 # ★ 自分で追加: ローカル検証（イメージから除外）
        ├── verify_local.py    #   Files API でアップロード→file_ids付き create_agent→agent.run()
        └── requirements-dev.txt   # dev 専用の追加依存（必要になったら置く）
```

**触るのは `main.py` の中身・`agent_def.py`・`requirements.txt`・`tests/` だけ。**
`azure.yaml`/`infra/`/`Dockerfile`/`agent.yaml` は基本そのまま。新しいエージェントは
`agent_def.py`（何をするか）の指示・ツールを差し替えるだけ。

---

## 3. 心臓部の2ファイル

雛形の `main.py` は「フレンドリーなアシスタント」サンプル。**定義を `agent_def.py` に切り出し、
`main.py` から呼ぶ**。こうすると入口は薄く保て、テストと定義を共有できる。

```python
# src/<agent>/agent_def.py — 定義（入口とテストで共有する単一の真実）
import os
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

INSTRUCTIONS = "..."  # システム指示

def create_chat_client() -> FoundryChatClient:
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
# src/<agent>/main.py — 雛形の入口を薄く保つ
from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv
from agent_def import create_agent

load_dotenv()

def main() -> None:
    ResponsesHostServer(create_agent()).run()   # file_ids なし＝デプロイ用、:8088 で /responses

if __name__ == "__main__":
    main()
```

ポイント:
- `create_agent()` を **入口（main.py）とテストの両方から呼ぶ**。「ローカルで通ったもの = デプロイされるもの」。
- テストから差し込みたいものだけ引数に出す（例: `file_ids`, `client`）。それ以外は引数を増やさない。
- `default_options={"store": False}` を付ける（履歴はホスト基盤が持つ）。
- `requirements.txt` を `main.py`/`agent_def.py` の import に合わせて満たす（§5）。

---

## 4. 認証と設定

- **Entra ID で統一。** `DefaultAzureCredential` を使う。ローカルは `az login`、デプロイ時は
  エージェント専用のマネージド ID が自動で割り当たる。**コードに API キーを書かない。**
- 環境変数は2つだけ:

| 変数 | 用途 |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | `https://<project>.services.ai.azure.com` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | モデルデプロイ名。デプロイ時はホスト基盤が注入 |

---

## 5. 依存の分け方

- `src/<agent>/requirements.txt` … **コンテナ最小ランタイム**のみ。`main.py`/`agent_def.py` の import を
  満たす（agent-framework 系 + azure-identity + python-dotenv）。雛形の既定は最小なので、
  `agent-framework-foundry` 等の不足を足す。**テスト/開発専用の依存は入れない**。
- dev 依存（必要になったら）… `tests/requirements-dev.txt` 等に分け、
  **uv 管理のローカル `.venv` にだけ** install: `uv pip install --prerelease=allow -r tests/requirements-dev.txt`
  （`agent-framework` 系がプレリリースのため `--prerelease=allow` 必須）。
- イメージから除外: `.dockerignore`（Container deploy）に `tests/` を足す（Code deploy なら `.agentignore`）。

---

## 6. 開発フロー: scaffold が先、実装はその中、デプロイは最後

**順序が重要。** 実装を先に手作りしてから `azd ai agent init` を被せると、manifest が
プロジェクトルートにある状態で「雛形を既存ファイルの中にコピーできない」と落ちる。
**init を空ディレクトリで先に走らせ、生成された雛形の中に実装する。**

1. **scaffold** — 新規・空ディレクトリで、`az login` + `azd auth login`（**2種類要る**）後:
   ```bash
   mkdir <your-agent> && cd <your-agent>
   azd ai agent init     # Python / Basic(Responses, Agent Framework) / Container deploy / Remote build
   ```
   → `azure.yaml`・`infra/`・`src/<agent>/{main.py, agent.yaml, Dockerfile, requirements.txt}` が生成。
2. **実装** — 生成された `main.py` に指示とツールを入れる。定義は `agent_def.py` に切り出して共有（§3）。
   `requirements.txt` を import に合わせて満たす（§5）。
3. **ローカル検証** — `.env`（`FOUNDRY_PROJECT_ENDPOINT` / `AZURE_AI_MODEL_DEPLOYMENT_NAME`）を置き:
   - `azd ai agent run` → 別ターミナルで `azd ai agent invoke --local "..."`（起動 / 依存 / 推論 / CI 実行）
   - **実ファイル分析**は file_ids を使うローカル harness（`tests/verify_local.py`）で検証（§9）
4. **デプロイ** — green になってから `azd provision` → `azd deploy`（= `azd up`）。`azd ai agent show` で Active 確認。
   （`azd ai agent up` というコマンドは存在しない。Docker 無しは Remote build で ACR ビルド。詰まったら `azd ai agent doctor`。）
5. **デプロイ後の最終確認** — ファイル分析は Hosted Agent セッション経由:
   `azd ai agent files upload <file>` → `azd ai agent invoke "..."`。

**green になるまでデプロイしない。**

---

## 7. テストの書き方

- **実エンドポイントに対する E2E** を、共有の `create_agent()` を直接駆動して書く（§3）。入口を経由しない。
- **実ファイル分析**は file_ids 経路で検証する（§9）: Files API にアップロード → `create_agent(file_ids=[id])`
  → `agent.run("...")`。harness と dev 依存はイメージから除外する（§5）。
- LLM 出力は揺れるので、完全一致ではなく
  「**事実が含まれるか**」「**意図したツールが動いたか**」を緩く検証する（数値は桁区切りを正規化）。
- ツール実行の確認は応答の content 型を見る。例: code interpreter なら
  `code_interpreter_tool_call` / `code_interpreter_tool_result`。
- マルチターンは `session = agent.create_session()` → `agent.run(msg, session=session)`。
- fixture の teardown で `client.project_client.close()` を呼ぶ
  （イベントループ終了後のクローズ警告を防ぐ）。
- アサートが落ちたら、まず**エージェントの回答が実際に正しいか**を確認する。
  正しければテスト判定が厳しすぎ/バグ。**安易に実装をいじらず、判定を実態に合わせる。**
  ただし「テストを通すためだけの不当な緩和」はしない（本質的な検証は残す）。

---

## 8. ツール

`FoundryChatClient` の静的ファクトリで Foundry のホスト型ツールを付ける。インスタンス不要:

```python
tools=[
    FoundryChatClient.get_code_interpreter_tool(),  # サンドボックス Python 実行
    FoundryChatClient.get_web_search_tool(),        # Web 検索
    # get_file_search_tool / get_image_generation_tool / get_mcp_tool ...
]
```

ローカルの Python 関数をツールにするなら `@tool` を付けて渡す（プロセス内で実行される）。

---

## 9. ファイルの扱い（重要な落とし穴）

ファイルを Code Interpreter に渡す経路が**ローカルとデプロイで異なる**:

- **ローカル**: ファイルを Files API にアップロードして file_id を得て、
  `get_code_interpreter_tool(file_ids=[id])` で渡す。
  ```python
  openai_client = client.project_client.get_openai_client()
  uploaded = await openai_client.files.create(file=f, purpose="assistants")  # purpose は assistants
  ```
- **デプロイ後**: ファイルは **Hosted Agent セッション**に乗せる（`azd ai agent files upload` /
  Foundry ポータル）。`get_code_interpreter_tool()` は `file_ids` なしで構築する。
- ⚠️ そのため、ホストサーバへの素の `/responses` POST だけではファイル分析できない。
  デプロイ後にセッションファイルが CI から見えるかは**実機で確認**し、見えなければ
  **Foundry Toolbox 経由の code interpreter** に切り替える。

---

## 10. デプロイ用ファイル（`azd ai agent init` が生成。基本そのまま）

これらは init が生成する。手作りしない。中身の確認ポイント:

- `azure.yaml`: `services.<agent>` が `project: src/<agent>`、`docker: { remoteBuild: true }`（Docker 無しビルド）、
  `config.deployments` にモデル、`startupCommand: python main.py`。
- `Dockerfile`: `python:3.x-slim` / `requirements.txt` を install / `EXPOSE 8088` / `CMD ["python","main.py"]`。
- `agent.yaml`: `kind: hosted`、`protocols: [{protocol: responses, version: 1.0.0}]`、
  `resources` に cpu/memory、`environment_variables` に `AZURE_AI_MODEL_DEPLOYMENT_NAME`。
- `.dockerignore`: `.venv/ .env` などを除外。**`tests/` を足して**検証資産をイメージから外す。

---

## 11. Git 運用ルール

- **コミットは [Conventional Commits](https://www.conventionalcommits.org/) に準拠する。**
  形式は `<type>(<scope>): <要約>`。`scope` は任意。
- **要約（subject）は日本語で簡潔に書く。** 「何をしたか」を一行で。末尾に句点は付けない。
- 主な `type`: `feat`（機能追加）/ `fix`（バグ修正）/ `docs`（ドキュメント）/
  `refactor`（挙動を変えないリファクタ）/ `test`（テスト）/ `chore`（雑務・依存・設定）。
- 補足が要るときだけ本文を空行の後に日本語で足す。1コミット1論点を心がける。

例: `feat(agent): コードインタープリタにファイル分析を追加` / `docs: README にデプロイ手順を追記`

---

## 公式リファレンス

- MAF docs: 「Microsoft Foundry provider」「Code Interpreter」「Hosted agents in Foundry Agent Service」
- MAF samples (github.com/microsoft/agent-framework):
  - `python/samples/02-agents/providers/foundry/`（FoundryChatClient + 各ツール、file_ids 付き CI）
  - `python/samples/04-hosting/foundry-hosted-agents/responses/`（ResponsesHostServer + Dockerfile + agent.yaml）
