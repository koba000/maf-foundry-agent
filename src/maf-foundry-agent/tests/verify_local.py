# Copyright (c) Microsoft. All rights reserved.

"""ローカル検証 harness — 実エンドポイントに対する E2E。

「ローカルで通ったもの＝デプロイされるもの」を満たすため、デプロイ入口と同じ
create_agent() を import し、本番と同じ hosted_file 添付経路で実ファイル分析まで確認する:
Files API にアップロード → Content.from_hosted_file(file_id) をメッセージに添付 →
CodeInterpreterFileInjector middleware が file_ids を CI に注入する。

固定の仮データは使わない。任意のサンプルファイルと質問を引数で渡して検証する:

    cd src/maf-foundry-agent
    az login                                  # 未ログインなら
    uv venv .venv --python 3.13 && source .venv/bin/activate
    uv pip install --prerelease=allow -r requirements.txt
    python tests/verify_local.py <file.xlsx> "質問1" ["質問2" ...]

例:
    python tests/verify_local.py ~/data/uriage.xlsx "売上の合計は？" "月別に集計して"
"""

import asyncio
import sys
from pathlib import Path

from agent_framework import Content, Message
from dotenv import load_dotenv

# agent_def.py は親ディレクトリ（src/maf-foundry-agent/）にある。
_SRC_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SRC_DIR))

from agent_def import create_agent, create_chat_client  # noqa: E402

# .env は src/maf-foundry-agent/.env。
load_dotenv(_SRC_DIR / ".env")


async def _run(file_path: Path, questions: list[str]) -> None:
    client = create_chat_client()
    try:
        # 1) 指定ファイルを Files API にアップロードして file_id を得る（purpose は assistants）。
        openai_client = client.project_client.get_openai_client()
        with open(file_path, "rb") as f:
            uploaded = await openai_client.files.create(file=f, purpose="assistants")
        file_id = uploaded.id
        print(f"uploaded: {file_path.name} -> file_id={file_id}")

        # 2) 共有の create_agent() をそのまま組み立てる（file_ids は middleware が注入する）。
        agent = create_agent(client=client)

        # 3) 初回質問にファイルを hosted_file として添付し、以降は履歴＋新質問を毎回
        #    まとめて messages で渡す（デプロイ時のホスティング層と同じ渡し方。
        #    middleware が履歴中の hosted_file からも file_ids を拾えることを検証する）。
        history: list[Message] = []
        for i, question in enumerate(questions):
            print(f"\n=== Q: {question}")
            contents = [Content.from_text(question)]
            if i == 0:
                contents.insert(0, Content.from_hosted_file(file_id, name=file_path.name))
            history.append(Message(role="user", contents=contents))
            response = await agent.run(history)
            history.extend(response.messages)
            print(response.text or str(response))
    finally:
        # イベントループ終了後のクローズ警告を防ぐため明示的に閉じる（close は async）。
        await client.project_client.close()


def main() -> None:
    if len(sys.argv) < 3:
        print(
            'usage: python tests/verify_local.py <file.xlsx> "質問1" ["質問2" ...]',
            file=sys.stderr,
        )
        raise SystemExit(2)

    file_path = Path(sys.argv[1]).expanduser()
    if not file_path.exists():
        print(f"file not found: {file_path}", file=sys.stderr)
        raise SystemExit(2)

    asyncio.run(_run(file_path, sys.argv[2:]))


if __name__ == "__main__":
    main()
