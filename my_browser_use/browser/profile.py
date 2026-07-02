from pydantic import BaseModel, Field


class BrowserProfile(BaseModel):
    """Chrome / Edge 浏览器启动配置

    所有字段都带默认值，所以 BrowserProfile() 就能直接用，
    想自定义就传 BrowserProfile(headless=False, browser_type='edge')
    """

    # 浏览器类型：'chrome' 或 'edge'（都是 Chromium 内核，CDP 协议一样）
    browser_type: str = Field(
        default='chrome',
        description="'chrome' 或 'edge'",
    )

    # 是否无头模式（True = 不显示窗口，False = 能看到浏览器界面）
    headless: bool = Field(default=True, description='是否无头模式')

    # Chrome / Edge 远程调试端口，CDP 就是通过这个端口连上的
    debugging_port: int = Field(default=9222, description='CDP 远程调试端口')

    # 浏览器窗口大小
    window_width: int = Field(default=1280, description='窗口宽度')
    window_height: int = Field(default=720, description='窗口高度')

    # Profile 目录：None=临时目录，""=系统默认（你的登录态），指定路径=自定义
    profile_dir: str | None = Field(
        default=None,
        description='浏览器用户数据目录：None=临时，""=系统默认',
    )

    # ---- 根据 browser_type 自动查找可执行文件路径 ----

    @property
    def executable_path(self) -> str:
        """根据 browser_type 返回浏览器可执行文件的完整路径"""
        if self.browser_type == 'edge':
            return BrowserProfile._find_edge()
        return BrowserProfile._find_chrome()

    @staticmethod
    def _find_chrome() -> str:
        """查找 Chrome 可执行文件"""
        import subprocess
        from pathlib import Path

        mac_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        if Path(mac_path).exists():
            return mac_path

        linux_paths = ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable']
        for p in linux_paths:
            if Path(p).exists():
                return p

        result = subprocess.run(['which', 'google-chrome'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()

        raise FileNotFoundError(
            '找不到 Chrome，请安装 Chrome 或改用 Edge（BrowserProfile(browser_type="edge")）'
        )

    @staticmethod
    def _find_edge() -> str:
        """查找 Edge 可执行文件"""
        import subprocess
        from pathlib import Path

        mac_path = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
        if Path(mac_path).exists():
            return mac_path

        linux_paths = [
            '/usr/bin/microsoft-edge',
            '/usr/bin/microsoft-edge-stable',
        ]
        for p in linux_paths:
            if Path(p).exists():
                return p

        result = subprocess.run(['which', 'microsoft-edge'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()

        raise FileNotFoundError(
            '找不到 Edge，请安装 Edge 或改用 Chrome（BrowserProfile(browser_type="chrome")）'
        )

    # ---- Chrome / Edge 命令行参数（Chromium 系通用） ----

    @property
    def chrome_args(self) -> list[str]:
        """把配置拼成浏览器命令行参数列表"""
        import tempfile
        import os as _os

        args = [
            f'--remote-debugging-port={self.debugging_port}',
            f'--window-size={self.window_width},{self.window_height}',
            '--no-first-run',
            '--no-default-browser-check',
        ]

        # user-data-dir：None = 临时目录（默认），"" = 系统默认 profile
        if self.profile_dir is None:
            user_data_dir = _os.path.join(
                tempfile.gettempdir(),
                f'browser-use-{self.browser_type}-{_os.getpid()}',
            )
            args.append(f'--user-data-dir={user_data_dir}')
        elif self.profile_dir:
            args.append(f'--user-data-dir={self.profile_dir}')
        if self.headless:
            args.append('--headless=new')
        return args
