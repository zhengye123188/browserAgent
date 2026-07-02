"""统一消息类型schema，适配不同厂商的llm"""

from pydantic import BaseModel, Field,ConfigDict

class SystemMessage(BaseModel):
    """系统提示词"""
    model_config = ConfigDict(extra='forbid')
    content: str

class UserMessage(BaseModel):
    """用户消息（页面状态、任务描述等）"""
    model_config = ConfigDict(extra='forbid')
    content: str

class AssistantMessage(BaseModel):
    """助手历史消息"""
    model_config = ConfigDict(extra='forbid')
    content: str
    tool_calls: list[dict] | None = None  # 历史上的工具调用

class ToolMessage(BaseModel):
    """工具执行结果"""
    model_config = ConfigDict(extra='forbid')
    tool_call_id: str
    content: str  # ActionResult 序列化后的文本