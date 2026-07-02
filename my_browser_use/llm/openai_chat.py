import json
from openai import AsyncOpenAI
from my_browser_use.llm.base import BaseChatModel
from my_browser_use.llm.messages import (
    SystemMessage, UserMessage, AssistantMessage, ToolMessage,
)


class ChatOpenAICompatible(BaseChatModel):
    """OpenAI 协议兼容的 LLM 实现。支持 DeepSeek, OpenAI, Groq, 通义千问 等。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        super().__init__(model=model)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    # -------- 消息转换 --------

    @staticmethod
    def _convert_messages(messages) -> list[dict]:
        """内部消息类型 → OpenAI API 字典格式"""
        result = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            elif isinstance(msg, UserMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AssistantMessage):
                item = {"role": "assistant", "content": msg.content or None}
                if msg.tool_calls:
                    item["tool_calls"] = msg.tool_calls
                result.append(item)
            elif isinstance(msg, ToolMessage):
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })
        return result

    # -------- 响应解析 --------

    @staticmethod
    def _parse_response(response) -> dict:
        """OpenAI 响应 → {"action": "...", "params": {...}}"""
        msg = response.choices[0].message
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            return {
                "action": tc.function.name,
                "params": json.loads(tc.function.arguments),
            }
        return {"action": "done", "params": {"text": msg.content or ""}}

    # -------- 核心 --------

    async def ainvoke(self, messages, tools=None, output_format=None):
        openai_messages = self._convert_messages(messages)

        # 结构化输出路线：LLM 直接返回符合 schema 的 JSON
        if output_format is not None:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=openai_messages,
                response_format={
                    'type': 'json_schema',
                    'json_schema': {
                        'name': 'agent_output',
                        'schema': output_format.model_json_schema(),
                        'strict': True,
                    },
                },
            )
            content = response.choices[0].message.content
            return output_format.model_validate_json(content)

        # Function calling 路线（保留原有逻辑）
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=tools,
        )
        return self._parse_response(response)