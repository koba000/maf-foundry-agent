# Copyright (c) Microsoft. All rights reserved.

"""エージェント定義（単一の真実）。

main.py（デプロイ入口）と tests/verify_local.py（ローカル検証）が
この create_agent() を共有して import する。INSTRUCTIONS を二重持ちしない。
"""

import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

# モデルへ渡すシステム指示（ユーザー向け出力はすべて日本語）。
INSTRUCTIONS = """\
あなたはデータ分析アシスタントです。ユーザーがアップロードしたファイル（CSV / Excel）を
Python (Code Interpreter) で読み込み、集計・分析・可視化を行ってください。

【出力言語】
ユーザー向けの出力（最終回答、コード内の print やコメント）はすべて日本語で記述すること。
英語に切り替えないこと。

【重要なルール】
- 必ず Code Interpreter で実データを操作してから回答する。推測で答えない。
- 列名・dtype はコードで確認してから言及する。スキーマを思い込みで答えない。
- ファイル読み込みは初回のみ。以降のターンでも同じ DataFrame (変数 df 推奨) を再利用する。
- 数値は具体的に示す（「だいたい」「いくつか」は避ける）。
- 可視化が必要なときは matplotlib で図を生成し、savefig して画像を返す。
- 「フィルタを戻して」「リセット」と言われたら df を初期状態に戻す（再読込など）。
- 専門用語は最小限にし、結果は自然な日本語で要約する。

【matplotlib で日本語を含む図を描くとき】
豆腐（□）を避けるため、利用可能な日本語フォントがあれば設定する。無ければ軸ラベル・凡例・
タイトルは英語にフォールバックし、日本語の解説は本文に書く。図中に □ を残さないこと。
matplotlib.rcParams["axes.unicode_minus"] = False も設定すること。
"""


def create_chat_client() -> FoundryChatClient:
    """Foundry 推論クライアント。環境変数で接続（Entra ID 認証）。"""
    return FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    )


def create_agent(file_ids=None, client=None) -> Agent:
    """データ分析エージェントを組み立てる。

    file_ids: ローカル検証で Files API の file_id を渡す経路。
              デプロイ時は None（ファイルは Hosted Agent セッション経由で届く）。
    client:   テストから差し込みたいときだけ渡す。通常は None。
    """
    client = client or create_chat_client()
    return Agent(
        client=client,
        instructions=INSTRUCTIONS,
        # Code Interpreter（サンドボックス Python）でアップロードファイルを分析する。
        tools=FoundryChatClient.get_code_interpreter_tool(file_ids=file_ids),
        # 会話履歴はホスト基盤が管理するため、サービス側に保存させない。
        default_options={"store": False},
    )
