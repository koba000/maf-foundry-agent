# Copyright (c) Microsoft. All rights reserved.

"""ローカル検証 harness — 実エンドポイントに対する E2E。

「ローカルで通ったもの＝デプロイされるもの」を満たすため、デプロイ入口と同じ
create_agent() を import し、file_ids 経路で実ファイル分析まで確認する。

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

        # 2) 共有の create_agent() に file_ids を差し込んで組み立てる。
        #    複数質問は同一セッションで会話履歴を引き継ぐ。
        agent = create_agent(file_ids=[file_id], client=client)
        session = agent.create_session()

        # 3) 引数で渡された質問を順に実行して回答を表示する。
        for question in questions:
            print(f"\n=== Q: {question}")
            response = await agent.run(question, session=session)
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
