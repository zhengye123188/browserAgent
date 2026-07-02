from pydantic import BaseModel

class BrowserStateHistory(BaseModel):
    """用于持久化存储的浏览器状态快照"""
    url: str
    title: str
    screenshot_path: str | None = None