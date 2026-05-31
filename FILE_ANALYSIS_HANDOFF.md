# デプロイ済みエージェントのファイル分析 — 調査ハンドオフ

> 作成: 2026-05-31 / 次のチャットで「修正」を行うための引き継ぎメモ。
> 結論だけ読むなら **TL;DR** と **5. 決定すべき方針** を見れば足りる。

---

## TL;DR

- デプロイは成功。テキスト対話（「こんにちは」など）は正常に動く。
- **`azd ai agent files upload` でアップロードしたファイルを、デプロイ済みエージェントの Code Interpreter (CI) が分析できない。**
- 原因は**実機＋コードで確定済み**: `files upload` が書く先（Hosted Agent の**セッションFS**）と、CI が読む先（**auto コンテナの `/mnt/data`**）は**別のファイルシステム**。両者を繋ぐ実行時の経路が SDK に無い。
- CI にファイルを入れる唯一の手段は **`get_code_interpreter_tool(file_ids=[...])` 構築時の `file_ids`**。今のコードは `file_ids=None` 固定なので CI は常に空。
- **やり方が間違っていたわけではない。** `azd ai agent files` はヘルプ上 *"debugging, seeding data, and agent setup"* 用で、CI へのファイル投入路ではない。
- 直すには下記 **3案のいずれか**（§5）。本命は **案B（リクエスト毎に file_ids を CI へ注入するコード改修）**。

---

## 1. 現在のデプロイ構成（確認済み）

| 項目 | 値 |
|---|---|
| リージョン | **japaneast** に統一 |
| azd 環境 (`AZURE_ENV_NAME`) | `maf-foundry-agent-dev` |
| リソースグループ | `rg-dev-ai`（japaneast） |
| AI Foundry アカウント | `proj-dev-ai`（japaneast, kind=AIServices） |
| プロジェクト | `proj-default` |
| モデルデプロイ | `gpt-5.4`（version 2026-03-05, **GlobalStandard**） |
| プロジェクト エンドポイント | `https://proj-dev-ai.services.ai.azure.com/api/projects/proj-default` |
| Hosted Agent | `maf-foundry-agent`（remote, デプロイ済み・応答OK） |
| `USE_EXISTING_AI_PROJECT` | `true`（既存 proj-dev-ai を採用） |

**後片付け（未実施）**: 旧 `proj-dev-ai-eastus`（eastus）が同じ RG に残存。japaneast 動作確認が済んだら削除推奨。
```bash
az cognitiveservices account delete -n proj-dev-ai-eastus -g rg-dev-ai
az cognitiveservices account purge  -n proj-dev-ai-eastus -g rg-dev-ai -l eastus
```

---

## 2. 症状（再現）

```
$ azd ai agent files upload src/maf-foundry-agent/tests/sample_sales_data.csv
Uploaded src/maf-foundry-agent/tests/sample_sales_data.csv -> sample_sales_data.csv

$ azd ai agent invoke "アップロードしたファイルの概要を教えて"
[maf-foundry-agent] アップロード済みファイルを確認しましたが、CSV / Excel ファイルは
見つかりませんでした。…
```

---

## 3. 確定した原因（実機 + コード両面で裏取り済み）

### 3-1. 実機エビデンス

**(a) ファイルはセッションFSに確かに存在**（`azd ai agent files list`）:
```json
{ "name": "sample_sales_data.csv", "size": 161700, "is_dir": false }
```

**(b) しかし CI サンドボックスの `/mnt/data` は空**（診断 invoke で CI 内を列挙）:
```
CWD= /home/sandbox
LS_CWD= ['.bash_logout','.bashrc','.cache','.config','.ipython','.local','.openai_internal','.profile','uvicorn_logging.config']
/mnt/data -> []          ← CI がファイルを置く標準マウント。空
/mnt -> ['data']
/home -> ['proxyuser','sandbox','vscode']
/session ERR FileNotFoundError
/workspace ERR FileNotFoundError
```

**(c) セッションは一致している**（ずれが原因ではない）:
`files list` のセッション = invoke のセッション = `def3298cbc7327113915340b684fc6ed364788aa2c7565f904eeaba21038a80`

> ⚠️ 注意: 診断時に `glob.glob("/**/*.csv", recursive=True)` を実行したら CI がルート全体を再帰探索して **600秒でタイムアウト**した。次回 CI 内を探索するときはルートからの再帰 glob を使わないこと。

### 3-2. コードエビデンス

- 入口は `file_ids` を渡していない:
  - [src/maf-foundry-agent/agent_def.py:61](src/maf-foundry-agent/agent_def.py#L61) … `tools=FoundryChatClient.get_code_interpreter_tool(file_ids=file_ids)`
  - `main.py` の `create_agent()` は `file_ids` 引数なし → **デプロイ時は `file_ids=None`**。
- CI ツールは**常に auto コンテナ + file_ids のみ**で構成（セッションFSと無関係）:
  - `agent_framework_foundry/_chat_client.py:343-363` `get_code_interpreter_tool`
    ```python
    resolved = resolve_file_ids(file_ids)
    tool_container = AutoCodeInterpreterToolParam(file_ids=resolved)  # container は常に auto
    return CodeInterpreterTool(container=tool_container, **kwargs)
    ```
- `resolve_file_ids`（同 `:97-119`）は**渡された file_ids を変換するだけ**。
- `_prepare_tools_for_openai`（同 `:252-257`）はツールを整形するのみ。**受信メッセージ中のファイルを CI コンテナへ合流させるコードは無い。**
- パッケージ内に **"Foundry Toolbox 経由 CI" に相当する API は無い**（`toolbox` は generic な `agent_framework/_tools.py` のみ。foundry プロバイダには無い）。CLAUDE.md §9 のその記述はポータル機能/別概念を指している可能性。

### 3-3. まとめ（経路別の可否）

| 経路 | CI(`/mnt/data`) に届くか | 根拠 |
|---|---|---|
| `azd ai agent files upload`（セッションFS） | ❌ | 実機で `/mnt/data` 空。別FS |
| ローカル `tests/verify_local.py` | ✅ | `get_code_interpreter_tool(file_ids=[id])` で構築 |
| デプロイ後にメッセージ添付（ポータル等） | ⚠️ 未検証 | SDK に自動合流コード無し。Agent Service 側が裏で繋ぐ可能性は残る |

---

## 4. なぜローカルは動いてデプロイは動かないのか（CLAUDE.md §9 の落とし穴そのもの）

- ローカル検証は Files API に `purpose="assistants"` でアップ → `file_id` を得 → `get_code_interpreter_tool(file_ids=[id])` で構築。だから `/mnt/data` にファイルが入る。
  ```python
  openai_client = client.project_client.get_openai_client()
  uploaded = await openai_client.files.create(file=f, purpose="assistants")
  # → uploaded.id を file_ids に渡す
  ```
- デプロイ後はこの `file_ids` を渡す主体がいない（入口が `None` 固定、実行時注入も無い）。

---

## 5. 決定すべき方針（次チャットで選ぶ）

### 案A: まず Foundry ポータルのチャット添付を検証（コード変更ゼロ）
- ポータルの playground/チャットで CSV を**メッセージ添付**して質問。
- Agent Service が「メッセージ添付ファイル → CI の auto コンテナ」を裏で繋いでいれば、それで完了。
- 長所: タダで可否が分かる。`files upload`（seed/debug 用）とは別経路。
- 短所: SDK 側に合流コードが無いため、繋がらない可能性も十分ある（未検証）。

### 案B（本命）: リクエスト毎に `file_ids` を CI へ注入するコード改修
- エージェントを「**受信リクエストから file_id を取り出し、その都度 `get_code_interpreter_tool(file_ids=[...])` で CI ツールを組み立てる**」形に変える。
- 確実に動く本番経路。`agent_def.py` の静的ツール構築（[:61](src/maf-foundry-agent/agent_def.py#L61)）を、middleware か run ループでの動的構築に置き換える。
- **次チャットで先に確認すべき未解決点（§6）あり。**
- 長所: 確実。短所: コード追加が要る（CLAUDE.md §0「まずシンプル」とのバランス要検討）。

### 案C: 当面ローカルのみ運用、デプロイ後分析は後回し
- `verify_local.py` は file_ids で既に動く。デプロイ版は「こんにちは」系の対話エージェントとして完成扱い。
- ファイル分析が今すぐ要らないなら最小コストで一旦クローズ。

---

## 6. 案Bを選ぶ場合に、次チャットで最初に確認すべき未解決点

1. **ResponsesHostServer は、受信リクエストの添付ファイルをエージェントの run にどう渡すか？**
   - 受信メッセージに `HostedFileContent`（`Content.from_hosted_file(file_id=...)`）として現れるか、あるいは別形式か。
   - 現れるなら、middleware でそれを抽出 → `file_ids` を作って CI ツールを再構築できる。
2. **動的にツールを差し替える最小の仕掛け**は何か（middleware か、リクエスト毎に `Agent` を組み直すか）。
3. **file_id の発生源**: クライアント/ポータルが Files API に上げて file_id を渡すのか、エージェント自身が受け取った生バイトを Files API に上げる必要があるのか。
4. （調査用）CI を**セッション結合コンテナ**に束ねる手段が SDK に無いか再確認（現状 `get_code_interpreter_tool` は `container` を実質 auto に固定している点を踏まえる）。

---

## 7. 参考: よく使ったコマンド

```bash
# セッションFSの中身（直近 invoke セッション基準）
azd ai agent files list

# デプロイ済みエージェントへ送信（セッションは連続 invoke で自動継続。--new-session でリセット）
azd ai agent invoke "メッセージ"

# CI サンドボックス内を覗く診断（ルート再帰 glob は厳禁＝タイムアウト）
azd ai agent invoke 'Code Interpreterで実行: import os; print(os.getcwd(), os.listdir("/mnt/data"))'

# 状態確認
azd ai agent show maf-foundry-agent
azd ai agent monitor --follow
```

## 8. 参考リンク（CLAUDE.md より）
- MAF docs: 「Microsoft Foundry provider」「Code Interpreter」「Hosted agents in Foundry Agent Service」
- MAF samples: `python/samples/02-agents/providers/foundry/`（file_ids 付き CI の実例）、
  `python/samples/04-hosting/foundry-hosted-agents/responses/`
