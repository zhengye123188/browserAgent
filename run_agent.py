"""Browser-Use Agent —— 对话式交互。

用法：
    python run_agent.py              # 用 Chrome
    python run_agent.py --edge       # 用 Edge
    python run_agent.py --headless   # 无头模式
"""

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from my_browser_use.agent.service import BrowserAgent
from my_browser_use.browser.profile import BrowserProfile
from my_browser_use.browser.session import BrowserSession
from my_browser_use.dom.service import DomService
from my_browser_use.llm.openai_chat import ChatOpenAICompatible
from my_browser_use.tools.service import Tools

# ---- 日志：全部静默 ----
logging.basicConfig(level=logging.CRITICAL, force=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--edge', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument(
        '--profile', nargs='?', const='', default=os.getenv('PROFILE_DIR'),
        help='浏览器 profile 目录（默认从 .env 的 PROFILE_DIR 读取）',
    )
    args = parser.parse_args()

    api_key = os.getenv('LLM_API_KEY', '')
    base_url = os.getenv('LLM_BASE_URL', 'https://api.deepseek.com/v1')
    model = os.getenv('LLM_MODEL', 'deepseek-chat')

    if not api_key or 'your-' in api_key:
        print('请在 .env 中配置 LLM_API_KEY')
        sys.exit(1)

    browser_type = 'edge' if args.edge else os.getenv('BROWSER_TYPE', 'chrome')
    profile = BrowserProfile(browser_type=browser_type, headless=args.headless, profile_dir=args.profile)
    session = BrowserSession(profile)

    # 使用系统 profile 时，必须先关掉浏览器（仅交互模式提示）
    if args.profile is not None and sys.stdin.isatty():
        import subprocess
        proc_name = 'Microsoft Edge' if browser_type == 'edge' else 'Google Chrome'
        result = subprocess.run(
            ['pgrep', '-x', proc_name], capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f'⚠ 检测到 {proc_name} 正在运行，需要先关闭再继续。')
            print('  关闭浏览器后按回车...')
            input()
    dom_service = DomService(session)
    tools = Tools(session, dom_service=dom_service)
    llm = ChatOpenAICompatible(api_key=api_key, base_url=base_url, model=model)

    agent = BrowserAgent(
        task='',  # 第一个任务通过 new_task 设置
        llm=llm,
        browser_session=session,
        tools=tools,
        dom_service=dom_service,
    )

    try:
        while True:
            try:
                task = input('\n\033[1mYou:\033[0m ').strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not task:
                continue
            if task.lower() in ('exit', 'quit', 'q'):
                break

            agent.new_task(task)
            history = await agent.run(max_steps=20)

            if history.is_done():
                result = history.final_result()
                if result:
                    print(f'\033[1mAgent:\033[0m {result}')
            else:
                print(f'\033[1mAgent:\033[0m (未完成，共 {len(history.history)} 步)')
    finally:
        if session._cdp_client:
            await session.stop()


if __name__ == '__main__':
    asyncio.run(main())
