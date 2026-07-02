import asyncio
import json
import logging
from collections.abc import Callable

from openai import AsyncOpenAI

from my_browser_use.llm.base import BaseChatModel
from my_browser_use.llm.messages import (
	SystemMessage,
	UserMessage,
	AssistantMessage,
	ToolMessage,
)

logger = logging.getLogger(__name__)


class ChatOpenAICompatible(BaseChatModel):
	"""OpenAI 协议兼容的 LLM 实现。支持 DeepSeek, OpenAI, Groq, 通义千问 等。"""

	def __init__(self, api_key: str, base_url: str, model: str):
		super().__init__(model=model)
		self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

	@staticmethod
	def _convert_messages(messages) -> list[dict]:
		result = []
		for msg in messages:
			if isinstance(msg, SystemMessage):
				result.append({'role': 'system', 'content': msg.content})
			elif isinstance(msg, UserMessage):
				result.append({'role': 'user', 'content': msg.content})
			elif isinstance(msg, AssistantMessage):
				item = {'role': 'assistant', 'content': msg.content or None}
				if msg.tool_calls:
					item['tool_calls'] = msg.tool_calls
				result.append(item)
			elif isinstance(msg, ToolMessage):
				result.append({
					'role': 'tool',
					'tool_call_id': msg.tool_call_id,
					'content': msg.content,
				})
		return result

	async def _stream_and_accumulate(
		self,
		openai_messages: list[dict],
		stream_callback: Callable[[str], None],
	) -> str:
		response = await self.client.chat.completions.create(
			model=self.model,
			messages=openai_messages,
			response_format={'type': 'json_object'},
			stream=True,
		)
		chunks: list[str] = []
		async for chunk in response:
			delta = chunk.choices[0].delta if chunk.choices else None
			if delta and delta.content:
				chunks.append(delta.content)
				stream_callback(delta.content)
		return ''.join(chunks)

	async def _invoke_non_streaming(self, openai_messages: list[dict]) -> str:
		response = await self.client.chat.completions.create(
			model=self.model,
			messages=openai_messages,
			response_format={'type': 'json_object'},
		)
		return response.choices[0].message.content or ''

	async def ainvoke(
		self, messages, tools=None, output_format=None, stream_callback=None
	):
		openai_messages = self._convert_messages(messages)

		if output_format is not None:
			last_error = None
			for attempt in range(5):
				try:
					if stream_callback:
						content = await self._stream_and_accumulate(
							openai_messages, stream_callback
						)
					else:
						content = await self._invoke_non_streaming(openai_messages)

					content = content.strip()
					if content.startswith('```'):
						lines = content.split('\n')
						if lines[-1].strip() == '```':
							lines = lines[1:-1]
						content = '\n'.join(lines)

					if not content:
						logger.warning(f'LLM 返回空内容，重试 {attempt + 1}/5')
						last_error = ValueError('LLM returned empty response')
						await asyncio.sleep(0.8 * (attempt + 1))
						continue

					return output_format.model_validate_json(content)

				except Exception as e:
					err_msg = str(e).lower()
					if 'validation error' in err_msg or 'json_invalid' in err_msg:
						raise
					last_error = e
					logger.warning(f'LLM 调用失败（重试 {attempt + 1}/5）: {e}')
					await asyncio.sleep(0.8 * (attempt + 1))

			logger.error(f'LLM 重试 5 次后仍失败: {last_error}')
			raise last_error

		# Function calling 路线（保留）
		response = await self.client.chat.completions.create(
			model=self.model,
			messages=openai_messages,
			tools=tools,
		)
		return _parse_response(response)


def _parse_response(response) -> dict:
	msg = response.choices[0].message
	if msg.tool_calls:
		tc = msg.tool_calls[0]
		return {
			'action': tc.function.name,
			'params': json.loads(tc.function.arguments),
		}
	return {'action': 'done', 'params': {'text': msg.content or ''}}
