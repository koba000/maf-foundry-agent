# Copyright (c) Microsoft. All rights reserved.

"""エージェント定義（単一の真実）。

main.py（デプロイ入口）と tests/verify_local.py（ローカル検証）が
この create_agent() を共有して import する。INSTRUCTIONS を二重持ちしない。
"""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent_framework import Agent, AgentContext, AgentMiddleware, Content, Message
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

# Hosted Agent セッションFS（azd ai agent files upload の書き込み先）のコンテナ内マウント。
# デプロイ時のみ存在する（FOUNDRY_AGENT_SESSION_ID で判定）。実機検証 2026-07-04。
_SESSION_FILES_DIR = Path("/home/session")

# モデルへ渡すシステム指示（ユーザー向け出力はすべて日本語）。
INSTRUCTIONS = """\
あなたはデータ分析アシスタントです。ユーザーがアップロードしたファイル（CSV / Excel）を
Python (Code Interpreter) で読み込み、集計・分析・可視化を行ってください。

【出力言語】
ユーザー向けの出力（最終回答、コード内の print やコメント）はすべて日本語で記述すること。
英語に切り替えないこと。

【重要なルール】
- アップロードされたファイルは Code Interpreter の /mnt/data 配下にある
  （ファイル名の先頭に ID が付くことがある）。まず os.listdir("/mnt/data") で確認してから読み込む。
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


class CodeInterpreterFileInjector(AgentMiddleware):
    """アップロードファイルの file_ids を集め、CI ツールを run 毎に組み立てて注入する。

    get_code_interpreter_tool は構築時の file_ids でしかファイルを CI サンドボックスに
    渡せないため、run-level tools として毎回組み立てる。Agent 既定 tools と名前ベースで
    マージされるので、既定には CI を置かない（二重登録防止）。file_ids の供給源は2つ:

    1. messages の hosted_file Content（/responses の input_file を ホスティング層が変換）。
       hosted_file はモデル入力に残すと input_file として送信され PDF 以外は
       400 (unsupported_file) になるため、抽出後にテキストの目印へ置き換える。
       元の Message は履歴側で再利用されるため破壊せず、新しい Message を作る。
    2. Hosted Agent セッションFS（azd ai agent files upload の書き込み先）。デプロイ時は
       /home/session にマウントされるので、コンテナ内から Files API にアップロードして
       file_id 化する（Foundry ゲートウェイは input_file 付き /responses を 500 で落とすため、
       デプロイ後の実用経路はこちら）。アップロード済みはサイズ・mtime で再利用する。
    """

    def __init__(self, client: FoundryChatClient) -> None:
        self._client = client
        self._uploaded: dict[tuple[str, str, int, int], str] = {}  # (session, path, size, mtime) -> file_id

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        file_ids: dict[str, None] = {}  # 順序維持の重複排除（履歴中の過去添付も再登場する）
        new_messages = []
        for message in context.messages:
            contents = []
            for content in message.contents:
                if content.type == "hosted_file" and content.file_id:
                    file_ids.setdefault(content.file_id)
                    contents.append(
                        Content.from_text(
                            f"[添付ファイル: {content.name or content.file_id}"
                            " — Code Interpreter の /mnt/data から読み込めます]"
                        )
                    )
                else:
                    contents.append(content)
            new_messages.append(Message(role=message.role, contents=contents))
        context.messages[:] = new_messages

        for file_id in await self._upload_session_files():
            file_ids.setdefault(file_id)

        context.tools = [FoundryChatClient.get_code_interpreter_tool(file_ids=list(file_ids) or None)]
        await call_next()

    async def _upload_session_files(self) -> list[str]:
        session_id = os.environ.get("FOUNDRY_AGENT_SESSION_ID")
        if not session_id or not _SESSION_FILES_DIR.is_dir():
            return []
        file_ids: list[str] = []
        for path in sorted(_SESSION_FILES_DIR.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            stat = path.stat()
            key = (session_id, str(path), stat.st_size, stat.st_mtime_ns)
            file_id = self._uploaded.get(key)
            if file_id is None:
                openai_client = self._client.project_client.get_openai_client()
                with path.open("rb") as f:
                    uploaded = await openai_client.files.create(file=f, purpose="assistants")
                file_id = uploaded.id
                self._uploaded[key] = file_id
            file_ids.append(file_id)
        return file_ids


def create_agent(client=None) -> Agent:
    """データ分析エージェントを組み立てる。

    client: テストから差し込みたいときだけ渡す。通常は None。
    Code Interpreter は既定 tools に置かず、middleware が messages 中の file_ids を
    見て run 毎に注入する（本番・ローカル検証とも同一経路）。
    """
    client = client or create_chat_client()
    return Agent(
        client=client,
        instructions=INSTRUCTIONS,
        middleware=[CodeInterpreterFileInjector(client)],
        # 会話履歴はホスト基盤が管理するため、サービス側に保存させない。
        default_options={"store": False},
    )
