from abc import ABC, abstractmethod


class BaseChatModel(ABC):
    """LLM 抽象基类。所有厂商的实现都继承这个。"""

    def __init__(
        self,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abstractmethod
    async def ainvoke(
        self,
        messages,  # list[SystemMessage | UserMessage | AssistantMessage | ToolMessage]
        tools: list[dict] | None = None,
    ) -> dict:
        """
        调用 LLM，返回统一格式的决策结果。

        Args:
            messages: 统一消息列表（SystemMessage, UserMessage 等）
            tools: 工具列表，由 tools.get_action_schemas() 生成

        Returns:
            {"action": "click_element", "params": {"index": 3}}
            或
            {"action": "done", "params": {"text": "任务完成"}}
        """