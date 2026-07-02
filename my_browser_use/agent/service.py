import time
import logging
from typing import Union

from my_browser_use.agent.views import (
	AgentOutput,
	AgentState,
	AgentHistory,
	AgentHistoryList,
	AgentStepInfo,
	StepMetadata,
)
from my_browser_use.browser.views import BrowserStateHistory
from my_browser_use.browser.session import BrowserSession
from my_browser_use.dom.service import DomService
from my_browser_use.llm.base import BaseChatModel
from my_browser_use.llm.messages import SystemMessage, UserMessage, AssistantMessage
from my_browser_use.tools.service import Tools

logger = logging.getLogger(__name__)


def _silent_stream():
	"""流式收集，不打印任何东西"""
	return lambda text: None


SYSTEM_PROMPT = """你是一个浏览器自动化助手。根据当前页面的状态，决定下一步操作。

你必须严格返回以下 JSON 格式（不要加额外文字）：

{{
  "thinking": "推理过程（可选）",
  "evaluation_previous_goal": "对上次操作的评估（可选）",
  "memory": "需要记住的关键信息（可选）",
  "next_goal": "下一步目标（可选）",
  "action": [
    {{"name": "action_name", "param1": "value1"}},
    {{"name": "another_action", "param": "value"}}
  ]
}}

action 是数组，包含要执行的操作。每个操作对象必须有 "name" 字段
（操作名称），再加上该操作需要的参数。参数名见下方"可用操作"列表。

可用操作（必选参数标 *，可选参数标 ?）：
{action_descriptions}

当前页面状态：
{page_state}"""


PLANNING_PROMPT = """你是一个智能助手，可以决定是否需要使用浏览器来完成任务。

你必须严格返回以下 JSON 格式（不要加额外文字）：

{{
  "thinking": "分析推理过程",
  "evaluation_previous_goal": null,
  "memory": "需要跨步骤记住的关键信息或上下文",
  "next_goal": "下一步目标",
  "action": [
    {{"name": "action_name", "param1": "value1"}}
  ]
}}

如何选择操作：
- 如果任务需要使用浏览器（搜索、浏览网页、查信息等），请使用 go_to_url
  导航到合适的网站。例如打开百度搜索：go_to_url(url="https://www.baidu.com")
- 如果任务不需要浏览器（纯对话、计算、问答等），请直接使用 done
  在 text 字段中给出回答

重要：请用 "memory" 字段记录任务的关键信息（用户是谁、在聊什么、
需要记住的上下文等），这些信息会在后续步骤中保留。

可用操作（必选参数标 *，可选参数标 ?）：
{action_descriptions}"""


class BrowserAgent:
	"""浏览器自动化 Agent——对话驱动，浏览器按需启动，带记忆"""

	def __init__(
		self,
		task: str,
		llm: BaseChatModel,
		browser_session: BrowserSession | None = None,
		tools: Tools | None = None,
		dom_service: DomService | None = None,
	):
		self.task = task
		self.llm = llm
		self.browser_session = browser_session
		self.tools = tools
		self.dom_service = dom_service

		# ---- 记忆（跨任务持久） ----
		self._llm_memory: str | None = None
		self._conversation: list[dict] = []

		self._reset_state()

		if tools:
			action_classes = self.tools.get_action_classes()
			ActionUnion = Union[tuple(action_classes)]  # type: ignore
			self.AgentOutput = AgentOutput.type_with_custom_actions(ActionUnion)
		else:
			self.AgentOutput = AgentOutput

	# =========================================================================
	# 辅助方法
	# =========================================================================

	def _reset_state(self) -> None:
		"""重置单次任务的状态（保留跨任务记忆）"""
		self.state = AgentState()
		self.history = AgentHistoryList()

	def new_task(self, task: str) -> None:
		"""开始新任务（保留之前的记忆和对话历史）"""
		self.task = task
		self._reset_state()

	def _is_browser_ready(self) -> bool:
		return (
			self.browser_session is not None
			and self.browser_session._cdp_client is not None
		)

	def _get_action_descriptions(self) -> str:
		if not self.tools:
			return '- done: 标记任务完成\n    text(string)*: 任务完成的总结文本'
		schemas = self.tools.get_action_schemas()
		lines = []
		for s in schemas:
			name = s['function']['name']
			desc = s['function'].get('description', '')
			params_schema = s['function'].get('parameters', {})
			props = params_schema.get('properties', {})
			required = params_schema.get('required', [])

			param_parts = []
			for pname, pinfo in props.items():
				if pname == 'name':
					continue
				ptype = pinfo.get('type', 'string')
				pdesc = pinfo.get('description', '')
				marker = '*' if pname in required else '?'
				param_parts.append(f'    {pname}({ptype}){marker}: {pdesc}')

			params_str = '\n'.join(param_parts) if param_parts else '    无参数'
			lines.append(f'- {name}: {desc}\n{params_str}')
		return '\n'.join(lines)

	def _build_page_state(self, url: str, dom_text: str, step_info: AgentStepInfo | None = None) -> str:
		parts = [f'当前 URL: {url}']
		if step_info:
			parts.append(f'步骤: {step_info.step_number + 1}/{step_info.max_steps}')
		selector_count = len(self.dom_service.selector_map) if self.dom_service else 0
		parts.append(f'\n可交互元素 ({selector_count} 个):\n{dom_text}')
		return '\n'.join(parts)

	# =========================================================================
	# 构建消息（含对话历史 + 记忆）
	# =========================================================================

	def _build_messages(
		self,
		system_prompt: str,
		action_desc: str,
		current_context: str,
	) -> list:
		"""构建发给 LLM 的消息列表，包含历史对话和记忆"""
		messages = []

		# System message
		system_content = system_prompt.format(
			action_descriptions=action_desc,
			page_state=current_context,
		) if '{page_state}' in system_prompt else system_prompt.format(
			action_descriptions=action_desc,
		)
		messages.append(SystemMessage(content=system_content))

		# 注入多轮对话历史（最近几轮）
		recent = self._conversation[-6:]  # 最近 3 轮（每轮 user + assistant）
		for entry in recent:
			if entry['role'] == 'user':
				messages.append(UserMessage(content=entry['content']))
			elif entry['role'] == 'assistant':
				messages.append(AssistantMessage(content=entry['content']))

		# 当前上下文（含记忆）
		user_content = f'任务: {self.task}\n\n'
		if self._llm_memory:
			user_content += f'【你之前记住的关键信息】\n{self._llm_memory}\n\n'
		user_content += current_context
		messages.append(UserMessage(content=user_content))

		return messages

	# =========================================================================
	# 记录对话
	# =========================================================================

	def _record_conversation(self, response: AgentOutput, results_text: str) -> None:
		"""把本轮 LLM 响应和结果记入对话历史"""
		# 记录 assistant 的响应（简化为 action 列表）
		actions_desc = ', '.join(
			f'{a.name}({a.model_dump(exclude={"name"})})' for a in response.action
		)
		self._conversation.append({
			'role': 'assistant',
			'content': f'执行: {actions_desc}',
		})
		# 记录结果
		self._conversation.append({
			'role': 'user',
			'content': f'结果: {results_text}',
		})
		# 更新记忆
		if response.memory:
			self._llm_memory = response.memory

	# =========================================================================
	# 规划步骤（浏览器未启动时）
	# =========================================================================

	async def _planning_step(self) -> None:
		step_start = time.time()

		action_desc = self._get_action_descriptions()
		context = (
			f'浏览器状态: 未启动\n'
			f'请判断任务是否需要浏览器，如需则 go_to_url，否则 done。'
		)

		messages = self._build_messages(PLANNING_PROMPT, action_desc, context)

		logger.info('分析任务中（浏览器未启动）...')
		response = await self.llm.ainvoke(
			messages,
			output_format=self.AgentOutput,
			stream_callback=_silent_stream(),
		)
		if response.thinking:
			print(f'  \033[90m{response.thinking}\033[0m')
		self.state.last_model_output = response

		# ---- Check if browser is needed ----
		browser_actions = {'go_to_url', 'click_element', 'input_text', 'scroll', 'go_back'}
		needs_browser = any(a.name in browser_actions for a in response.action)

		if needs_browser and not self._is_browser_ready():
			logger.info('任务需要浏览器，正在启动...')
			assert self.browser_session, '需要浏览器但未配置 BrowserSession'
			await self.browser_session.start()

		# ---- Act ----
		results = []
		for action in response.action:
			params = action.model_dump(exclude={'name'})
			logger.info(f'Step {self.state.n_steps}: executing {action.name}({params})')
			result = await self.tools.execute_action(action.name, params)
			results.append(result)
			if result.is_done:
				break
		self.state.last_result = results

		# ---- 记录对话 ----
		results_text = '; '.join(
			r.content or r.error or '' for r in results
		)
		self._record_conversation(response, results_text)

		# ---- Record ----
		url = ''
		title = ''
		if self._is_browser_ready():
			try:
				url = await self.browser_session.get_current_url()
				title = await self.browser_session.get_title()
			except Exception:
				pass

		metadata = StepMetadata(
			step_number=self.state.n_steps,
			step_start_time=step_start,
			step_end_time=time.time(),
		)
		self.history.history.append(AgentHistory(
			model_output=response,
			result=results,
			state=BrowserStateHistory(url=url, title=title),
			metadata=metadata,
		))
		self.state.n_steps += 1

	# =========================================================================
	# 正常步骤（浏览器已启动）
	# =========================================================================

	async def step(self, step_info: AgentStepInfo | None = None) -> None:
		step_start = time.time()

		# ---- Phase 1: Observe ----
		url = await self.browser_session.get_current_url()
		dom_root = await self.dom_service.build_enhanced_dom_tree()
		dom_text, _ = self.dom_service.serialize_dom_tree(dom_root)
		page_state = self._build_page_state(url, dom_text, step_info)

		# ---- Phase 2: Think ----
		action_desc = self._get_action_descriptions()
		messages = self._build_messages(SYSTEM_PROMPT, action_desc, page_state)

		response = await self.llm.ainvoke(
			messages,
			output_format=self.AgentOutput,
			stream_callback=_silent_stream(),
		)
		if response.thinking:
			print(f'  \033[90m{response.thinking}\033[0m')
		self.state.last_model_output = response

		# ---- Phase 3: Act ----
		results = []
		for action in response.action:
			params = action.model_dump(exclude={'name'})
			logger.info(f'Step {self.state.n_steps}: executing {action.name}({params})')
			result = await self.tools.execute_action(action.name, params)
			results.append(result)
			if result.is_done:
				break
		self.state.last_result = results

		# ---- 记录对话 ----
		results_text = '; '.join(
			r.content or r.error or '' for r in results
		)
		self._record_conversation(response, results_text)

		# ---- Phase 4: Record ----
		metadata = StepMetadata(
			step_number=self.state.n_steps,
			step_start_time=step_start,
			step_end_time=time.time(),
		)
		self.history.history.append(AgentHistory(
			model_output=response,
			result=results,
			state=BrowserStateHistory(
				url=url,
				title=await self.browser_session.get_title(),
			),
			metadata=metadata,
		))
		self.state.n_steps += 1

	# =========================================================================
	# 主循环
	# =========================================================================

	async def run(self, max_steps: int = 50) -> AgentHistoryList:
		logger.info(f'开始执行任务: {self.task}')

		while self.state.n_steps <= max_steps:
			if self.history.is_done():
				logger.info(f'任务在第 {self.state.n_steps - 1} 步完成')
				break

			if self.state.consecutive_failures >= 5:
				logger.error(f'连续失败 {self.state.consecutive_failures} 次，终止')
				break

			current_step = self.state.n_steps - 1
			step_info = AgentStepInfo(
				step_number=current_step,
				max_steps=max_steps,
			)

			try:
				if self._is_browser_ready():
					await self.step(step_info)
				else:
					await self._planning_step()

				if self.state.last_result and all(
					r.error is None for r in self.state.last_result
				):
					self.state.consecutive_failures = 0
				else:
					self.state.consecutive_failures += 1
			except Exception as e:
				logger.error(f'Step {self.state.n_steps} 异常: {e}', exc_info=True)
				self.state.consecutive_failures += 1
				self.state.n_steps += 1

			if step_info.is_last_step():
				logger.info(f'达到最大步数 {max_steps}，强制结束')
				break

		logger.info(
			f'任务执行完毕，共 {len(self.history.history)} 步，'
			f'状态: {"成功" if self.history.is_done() else "未完成"}'
		)
		return self.history
