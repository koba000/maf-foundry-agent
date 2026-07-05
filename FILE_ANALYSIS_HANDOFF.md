# デプロイ済みエージェントのファイル分析 — 実装まとめ

> **ステータス: ✅ 解決済み。** `azd ai agent files upload` でアップロードしたファイルを、
> デプロイ済みエージェントの Code Interpreter (CI) が正しく分析できることを実機で確認済み
> （実データに基づく正答がローカル検算と一致、マルチターンでも継続）。

---

## 1. 何が課題だったか

### 症状

```
$ azd ai agent files upload src/maf-foundry-agent/tests/sample_sales_data.csv
Uploaded src/maf-foundry-agent/tests/sample_sales_data.csv -> sample_sales_data.csv

$ azd ai agent invoke "アップロードしたファイルの概要を教えて"
[maf-foundry-agent] アップロード済みファイルを確認しましたが、CSV / Excel ファイルは
見つかりませんでした。…
```

テキスト対話（「こんにちは」等）は正常に動くが、アップロードしたファイルを CI が認識しない。

### 原因

`azd ai agent files upload` が書き込む先（Hosted Agent の **セッションFS**）と、CI が読む先
（サンドボックスの **`/mnt/data`**）は別のファイルシステムで、両者をつなぐ経路は SDK に存在しない。

実機で確認すると、セッションFSにはファイルがあるのに CI 内は空だった:

```
# files list ではファイルが見える
{ "name": "sample_sales_data.csv", "size": 161700, "is_dir": false }

# しかし CI サンドボックス内で確認すると空
CWD= /home/sandbox
/mnt/data -> []          ← CI がファイルを置く標準マウント。空
```

CI にファイルを渡す唯一の手段は、`get_code_interpreter_tool(file_ids=[...])` を**構築するときに
渡す `file_ids`**。ところがデプロイ時のエントリポイント（`main.py` → `create_agent()`）はこの
`file_ids` を渡す主体を持たず、常に空の CI ツールが使われていた
（`agent_framework_foundry` の `get_code_interpreter_tool` は container を実質 `auto` 固定にし
渡された `file_ids` を解決するだけで、セッションFSと CI サンドボックスを結びつける API はパッケージ内に無い）。

---

## 2. 解決策のアーキテクチャ

[agent_def.py](src/maf-foundry-agent/agent_def.py) の `CodeInterpreterFileInjector`
（`AgentMiddleware`）が、**run のたびに file_ids を集めて CI ツールを動的に組み立てて注入**する。
Agent の既定 `tools` には CI を置かない（run-level tools と名前ベースでマージされるため、既定にも
置くと二重登録になる）。

file_ids の供給元は2つある:

| 供給元 | 経路 | 実運用で使うか |
|---|---|---|
| ① `/responses` の `input_file`（file_id 付き） | ホスティング層が `hosted_file` Content に変換して `agent.run()` の messages に載せる | ローカル（ホストサーバ直POST）では動くが、**デプロイ後は Foundry ゲートウェイが `input_file` 付きリクエストをコンテナに届く前に 500 で落とす**ため実用不可 |
| ② `azd ai agent files upload` のセッションFS | デプロイ時、コンテナ内の **`/home/session`** にマウントされる（`FOUNDRY_AGENT_SESSION_ID` で判定）。middleware が run 毎にそこを走査し、コンテナ内から Files API (`purpose="assistants"`) へアップロードして file_id 化する | **こちらが実際に使われる経路** |

```python
class CodeInterpreterFileInjector(AgentMiddleware):
    async def process(self, context: AgentContext, call_next):
        # ① messages 中の hosted_file Content から file_id を収集
        # ② セッションFS (/home/session) 配下のファイルを Files API へアップロードして file_id 化
        # → 両方を合わせて run-level の CI ツールを組み立てる
        ...
        context.tools = [FoundryChatClient.get_code_interpreter_tool(file_ids=list(file_ids) or None)]
        await call_next()
```

### 実装上の注意点

- middleware の context 型は `AgentContext`。シグネチャは `process(self, context, call_next)` で
  `call_next()` は引数なし。`context.tools` の書き換えは有効。
- `hosted_file` Content をそのままモデル入力に残すと `input_file` として送信され、
  PDF 以外は 400 (unsupported_file) になる。file_id 抽出後、テキストの目印
  `[添付ファイル: <name> — Code Interpreter の /mnt/data から読み込めます]` に置き換える
  （元 Message は履歴で再利用されるため破壊せず、新しい Message を作る）。
- マルチターン: ホスティング層は毎ターン「履歴 + 今回入力」を全部 messages で渡すため、
  過去ターンの添付も middleware から見える。過去ターンの file_id が消える心配は不要。
- セッションFS からのアップロードは `(session, path, size, mtime)` をキーに再利用し、
  同じファイルを毎ターン再アップロードしない。

---

## 3. 検証方法

### ローカル

[tests/verify_local.py](src/maf-foundry-agent/tests/verify_local.py) が本番と同じ経路
（Files API アップロード → `create_agent()` → `Content.from_hosted_file(file_id)` をメッセージに
添付 → middleware が file_ids を注入）を再現する:

```bash
cd src/maf-foundry-agent
python tests/verify_local.py <file.xlsx> "質問1" ["質問2" ...]
```

### デプロイ後 E2E

```bash
azd ai agent files upload <file>
azd ai agent invoke "アップロードしたファイルの合計を教えて"
```

応答に `code_interpreter_tool_call` と実データに基づく分析結果（ローカル検算と一致）が
含まれることを確認する。

---

## 4. 運用上の注意点

- **デプロイ直後の一時障害**: コンテナ内マネージド ID が `No token received` で全リクエスト
  500 になることがある（プラットフォーム側の一時障害）。再デプロイで直る。
- **後片付け（未実施）**: 旧 `proj-dev-ai-eastus`（eastus）が同じリソースグループに残存している。
  japaneast での動作確認が済んでいるため削除してよい:
  ```bash
  az cognitiveservices account delete -n proj-dev-ai-eastus -g rg-dev-ai
  az cognitiveservices account purge  -n proj-dev-ai-eastus -g rg-dev-ai -l eastus
  ```

---

## 5. 現在のデプロイ構成

| 項目 | 値 |
|---|---|
| リージョン | japaneast に統一 |
| azd 環境 (`AZURE_ENV_NAME`) | `maf-foundry-agent-dev` |
| リソースグループ | `rg-dev-ai`（japaneast） |
| AI Foundry アカウント | `proj-dev-ai`（japaneast, kind=AIServices） |
| プロジェクト | `proj-default` |
| モデルデプロイ | `gpt-5.4`（version 2026-03-05, GlobalStandard） |
| プロジェクト エンドポイント | `https://proj-dev-ai.services.ai.azure.com/api/projects/proj-default` |
| Hosted Agent | `maf-foundry-agent`（remote, デプロイ済み・応答OK） |
| `USE_EXISTING_AI_PROJECT` | `true`（既存 `proj-dev-ai` を採用） |
| SDK バージョン | `agent-framework` / `agent-framework-foundry` 1.7.0、`agent-framework-foundry-hosting` 1.0.0a260528 |

---

## 6. 参考コマンド

```bash
# セッションFSの中身（直近 invoke セッション基準）※ CI とは別FS
azd ai agent files list

# デプロイ済みエージェントへ送信（セッションは連続 invoke で自動継続。--new-session でリセット）
azd ai agent invoke "メッセージ"

# リクエストボディを JSON で直接送る（input_file + file_id の E2E に使う）
azd ai agent invoke -f request.json

# CI サンドボックス内を覗く診断（ルート再帰 glob は厳禁＝タイムアウト。実際に600秒で発生した）
azd ai agent invoke 'Code Interpreterで実行: import os; print(os.getcwd(), os.listdir("/mnt/data"))'

# 状態確認
azd ai agent show maf-foundry-agent
azd ai agent monitor --follow
```

---

## 7. 参考リンク

- MAF docs: 「Microsoft Foundry provider」「Code Interpreter」「Hosted agents in Foundry Agent Service」
- MAF samples (github.com/microsoft/agent-framework):
  - `python/samples/02-agents/providers/foundry/`（file_ids 付き CI の実例）
  - `python/samples/04-hosting/foundry-hosted-agents/responses/`（ResponsesHostServer + Dockerfile + agent.yaml）
  - `python/samples/03-workflows/orchestrations/handoff_with_code_interpreter_file.py`
    （CI が生成したファイルを hosted_file Content として取り回す実例）
