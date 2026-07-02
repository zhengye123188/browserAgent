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
    """管理一个 Chrome / Edge 浏览器实例的完整生命周期"""

    def __init__(self, profile: BrowserProfile | None = None):
        self.profile = profile or BrowserProfile()
        self._chrome_process: subprocess.Popen | None = None
        self._cdp_client: CDPClient | None = None
        self._ws_url: str | None = None

    # =========================================================================
    # 启动 / 停止
    # =========================================================================

    async def start(self) -> None:
        """启动浏览器并建立 CDP 连接"""
        # Step 1: 启动浏览器进程
        await self._launch_browser()
        # Step 2: 拿到 WebSocket 地址
        self._ws_url = await self._get_ws_url()
        # Step 3: 连上 WebSocket 并启动 CDP 客户端
        self._cdp_client = CDPClient(self._ws_url)
        await self._cdp_client.start()
        logger.info(f'已连接到浏览器 (CDP)')

    async def stop(self) -> None:
        """关闭 CDP 连接，关掉浏览器"""
        if self._cdp_client:
            await self._cdp_client.stop()
            self._cdp_client = None
        if self._chrome_process:
            self._chrome_process.terminate()
            self._chrome_process.wait()
            self._chrome_process = None
        logger.info('浏览器已关闭')

    # =========================================================================
    # 内部：启动浏览器进程
    # =========================================================================

    async def _launch_browser(self) -> None:
        """启动浏览器子进程"""
        exe_path = self.profile.executable_path
        args = [exe_path] + self.profile.chrome_args
        logger.info(f'启动浏览器: {exe_path}')

        self._chrome_process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # =========================================================================
    # 内部：拿到 WebSocket 地址
    # =========================================================================

    async def _get_ws_url(self) -> str:
        """
        浏览器启动后会开一个 HTTP 调试端点 http://localhost:9222/json
        访问这个地址拿到的 JSON 里包含 WebSocket 连接地址
        """
        url = f'http://localhost:{self.profile.debugging_port}/json'
        async with httpx.AsyncClient() as client:
            for _ in range(30):  # 最多重试 30 次（等浏览器启动，30 × 0.5s = 15s）
                try:
                    resp = await client.get(url, timeout=2.0)
                    if resp.status_code == 200:
                        tabs = resp.json()
                        # 优先找 page 类型的 tab（不是 extension 的 background_page）
                        page_tab = None
                        for tab in tabs:
                            if tab.get('type') == 'page':
                                page_tab = tab
                                break
                        # 如果没有 page，退而求其次用第一个 tab
                        tab = page_tab or (tabs[0] if tabs else None)
                        if tab:
                            ws_url = tab.get('webSocketDebuggerUrl')
                            if ws_url:
                                return ws_url
                except Exception:
                    pass  # 浏览器还没准备好，继续重试
                await asyncio.sleep(0.5)
        raise RuntimeError(f'无法获取浏览器 WebSocket 地址，{self.profile.browser_type} 可能启动失败')

    # =========================================================================
    # CDP 操作方法
    # =========================================================================

    async def navigate(self, url: str) -> dict:
        """导航到指定 URL，等待页面加载完成"""
        assert self._cdp_client, '浏览器未启动，先调用 start()'
        result = await self._cdp_client.send.Page.navigate(
            params={'url': url}
        )
        logger.info(f'导航到: {url}')
        # 等待页面加载（简单粗暴但有效）
        await asyncio.sleep(2.0)
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
