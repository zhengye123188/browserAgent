from pydantic import BaseModel, Field




class BrowserProfile(BaseModel):
    """Chrome 浏览器启动配置

    所有字段都带默认值，所以 BrowserProfile() 就能直接用，
    想自定义就传 BrowserProfile(headless=False, debugging_port=9333)
    """

    # 是否无头模式（True = 不显示窗口，False = 能看到浏览器界面）
    headless: bool = Field(default=True, description='是否无头模式')

    # Chrome 远程调试端口，CDP 就是通过这个端口连上的
    debugging_port: int = Field(default=9222, description='CDP 远程调试端口')

    # 浏览器窗口大小
    window_width: int = Field(default=1280, description='窗口宽度')
    window_height: int = Field(default=720, description='窗口高度')

    @property
    def chrome_args(self) -> list[str]:
        """把配置拼成 Chrome 命令行参数列表

        返回的是列表而不是字符串，因为 subprocess.Popen 接受列表，
        可以正确处理含空格的参数
              """
        args = [
            f'--remote-debugging-port={self.debugging_port}',
            f'--window-size={self.window_width},{self.window_height}',
            '--no-first-run',              # 跳过 Chrome 首次运行向导
            '--no-default-browser-check',  # 不检查是否为默认浏览器
        ]
        if self.headless:
            # --headless=new 是 Chrome 112+ 的新无头模式
            # 渲染行为和正常模式完全一致，只是不显示窗口
            args.append('--headless=new')
        return args