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
        output_format: type | None = None,
    ):
        """
        调用 LLM，返回决策结果。

        Args:
            messages: 统一消息列表（SystemMessage, UserMessage 等）
            tools: 工具列表，由 tools.get_action_schemas() 生成
            output_format: 可选，指定返回的 Pydantic 模型类型。
                           提供时走结构化 JSON 输出路线，返回该模型的实例。
                           不提供时走 function calling 路线，返回 dict。

        Returns:
            dict: {"action": "click_element", "params": {"index": 3}}（function calling 路线）
            BaseModel: output_format 指定的 Pydantic 实例（结构化输出路线）
        """