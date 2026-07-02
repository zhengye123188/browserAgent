import logger
import asyncio
import logging
import subprocess
from pathlib import Path
import httpx
from cdp_use import CDPClient
from my_browser_use.browser.profile import BrowserProfile
logger = logging.getLogger(__name__)

class BrowserSession:
    """管理一个 Chrome 浏览器实例的完整生命周期"""

    def __init__(self, profile: BrowserProfile | None = None):
        self.profile = profile or BrowserProfile()
        self._chrome_process: subprocess.Popen | None = None
        self._cdp_client: CDPClient | None = None
        self._ws_url: str | None = None

    # =========================================================================
    # 启动 / 停止
    # =========================================================================

    async def start(self) -> None:
        """启动 Chrome 并建立 CDP 连接"""
        # Step 1: 启动 Chrome 进程
        await self._launch_chrome()
        # Step 2: 拿到 WebSocket 地址
        self._ws_url = await self._get_ws_url()
        # Step 3: 连上 WebSocket
        self._cdp_client = CDPClient(self._ws_url)
        logger.info(f'已连接到 Chrome')

    async def stop(self) -> None:
        """关闭 CDP 连接，关掉 Chrome"""
        if self._cdp_client:
            await self._cdp_client.stop()
            self._cdp_client = None
        if self._chrome_process:
            self._chrome_process.terminate()
            self._chrome_process.wait()
            self._chrome_process = None
        logger.info('Chrome 已关闭')

    # =========================================================================
    # 内部：启动 Chrome 进程
    # =========================================================================

    async def _launch_chrome(self) -> None:
        """启动 Chrome 子进程"""
        chrome_path = self._find_chrome()
        args = [chrome_path] + self.profile.chrome_args
        logger.info(f'启动Chrome')

        self._chrome_process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _find_chrome() -> str:
        """查找 Chrome 可执行文件"""
        # macOS 默认路径
        mac_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        if Path(mac_path).exists():
            return mac_path

        # Linux 常见路径
        linux_path = '/usr/bin/google-chrome'
        if Path(linux_path).exists():
            return linux_path

        # 最后尝试系统 PATH 里找
        result = subprocess.run(['which', 'google-chrome'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()

        raise FileNotFoundError('找不到Chrome，请确保已安装')

    # =========================================================================
    # 内部：拿到 WebSocket 地址
    # =========================================================================

    async def _get_ws_url(self) -> str:
        """
        Chrome 启动后会开一个 HTTP 调试端点 http://localhost:9222/json
        访问这个地址拿到的 JSON 里包含 WebSocket 连接地址
        """
        url = f'http://localhost:{self.profile.debugging_port}/json'
        async with httpx.AsyncClient() as client:
            for _ in range(30):  # 最多重试 30 次（等 Chrome 启动，30 × 0.5s = 15s）
                try:
                    resp = await client.get(url, timeout=2.0)
                    if resp.status_code == 200:
                        tabs = resp.json()
                        if tabs:
                            ws_url = tabs[0].get('webSocketDebuggerUrl')
                            if ws_url:
                                return ws_url
                except Exception:
                    pass  # Chrome 还没准备好，继续重试
                await asyncio.sleep(0.5)
        raise RuntimeError('无法获取 Chrome WebSocket 地址，Chrome 可能启动失败')

    # =========================================================================
    # CDP 操作方法
    # =========================================================================

    async def navigate(self, url: str) -> dict:
        """导航到指定 URL"""
        assert self._cdp_client, '浏览器未启动，先调用 start()'
        result = await self._cdp_client.send.Page.navigate(
            params={'url': url}
        )
        logger.info(f'导航到: {url}')
        return result

    async def get_title(self) -> str:
        """通过执行 JS 获取页面标题"""
        assert self._cdp_client, '浏览器未启动，先调用 start()'
        result = await self._cdp_client.send.Runtime.evaluate(
            params={'expression': 'document.title'}
        )
        return result.get('result', {}).get('value', '')

    async def get_html(self) -> str:
        """获取当前页面完整HTML"""
        assert self._cdp_client, '浏览器未启动，先调用 start()'
        result = await self._cdp_client.send.Runtime.evaluate(
            params={'expression': 'document.documentElement.outerHTML'}
        )
        return result.get('result', {}).get('value', '')

    async def get_current_url(self) -> str:
        """获取当前页面的 URL"""
        assert self._cdp_client, '浏览器未启动，先调用 start()'
        result = await self._cdp_client.send.Runtime.evaluate(
            params={'expression': 'window.location.href'}
        )
        return result.get('result', {}).get('value', '')





