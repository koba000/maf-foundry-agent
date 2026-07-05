# Copyright (c) Microsoft. All rights reserved.

"""デプロイ入口。定義は agent_def.py に集約し、ここは薄く保つ。"""

from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv

from agent_def import create_agent

# Load environment variables from .env file
load_dotenv()


def main() -> None:
    # ファイルは /responses の input_file(file_id) → hosted_file Content として届き、
    # agent_def.py の middleware が run 毎に Code Interpreter へ注入する。
    ResponsesHostServer(create_agent()).run()


if __name__ == "__main__":
    main()
