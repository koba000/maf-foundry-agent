# maf-foundry-agent (azd project)

MAF + Foundry Hosted Agent の**データ分析エージェント**。アップロードした CSV/Excel を
Code Interpreter で分析し、日本語で答える。これがこのエージェントの**正式なデプロイ対象**
（`azd ai agent init` 生成の azd プロジェクト）。

- **アプリ本体と詳細**: [src/maf-foundry-agent/README.md](src/maf-foundry-agent/README.md)
  （概要・技術スタック・実装フェーズ・ローカル検証・デプロイ手順）
- **開発ルール**: [CLAUDE.md](CLAUDE.md)（init 先行・定義共有・依存分離）
- **汎用手順書**: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

## リポジトリ構成

```
maf-foundry-agent/
├── azure.yaml              # サービス定義（gpt-5.4 / remoteBuild）
├── infra/                  # bicep（azd 生成）
└── src/maf-foundry-agent/  # ★ アプリ本体（agent_def.py / main.py / tests/）
```

## 進捗

- [x] **A. Scaffold + 実装**
- [x] **B. ローカル検証** — 実エンドポイントで file_ids 経路の Excel 分析を確認（green 済み）
- [ ] **C. デプロイ** — `azd auth login` → `azd provision` → `azd deploy` → `azd ai agent show`
- [ ] **D. デプロイ後のファイル分析確認** — セッションファイルが file_ids 無し Code Interpreter から
      見えるか実機確認（見えなければ Foundry Toolbox 経由へ切替）

各フェーズの手順・検証方法は [src/maf-foundry-agent/README.md](src/maf-foundry-agent/README.md) を参照。
