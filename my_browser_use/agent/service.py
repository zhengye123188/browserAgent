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
from my_browser_use.llm.messages import SystemMessage, UserMessage
from my_browser_use.tools.service import Tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个浏览器自动化助手。根据当前页面的状态，决定下一步操作。

你的输出必须是一个 JSON 对象，包含以下字段：
- thinking: 你的推理过程（可选）
- evaluation_previous_goal: 对上一次操作的评估（可选）
- memory: 需要记住的关键信息（可选）
- next_goal: 下一步目标（可选）
- action: 要执行的操作列表

可用操作：
{action_descriptions}

当前页面状态：
{page_state}"""


class Agent:
	"""浏览器自动化 Agent：循环执行 observe → think → act"""

	def __init__(
		self,
		task: str,
		llm: BaseChatModel,
		browser_session: BrowserSession,
		tools: Tools,
	):
		self.task = task
		self.llm = llm
		self.browser_session = browser_session
		self.tools = tools
		self.state = AgentState()
		self.history = AgentHistoryList()
		self.dom_service = DomService(browser_session)

		# 构建包含所有具体 Action 类型的 AgentOutput，让 LLM 的 JSON Schema 知道每个 action 有哪些字段
		action_classes = self.tools.get_action_classes()
		ActionUnion = Union[tuple(action_classes)]  # type: ignore
		self.AgentOutput = AgentOutput.type_with_custom_actions(ActionUnion)

	# =========================================================================
	# 辅助方法
	# =========================================================================

	def _get_action_descriptions(self) -> str:
		"""从工具注册表生成 LLM 能理解的操作说明"""
		schemas = self.tools.get_action_schemas()
		lines = []
		for s in schemas:
			name = s['function']['name']
			desc = s['function'].get('description', '')
			lines.append(f'- {name}: {desc}')
		return '\n'.join(lines)

	def _build_page_state(self, url: str, dom_text: str, step_info: AgentStepInfo | None = None) -> str:
		"""构建页面状态的文本描述"""
		parts = [f'当前 URL: {url}']
		if step_info:
			parts.append(f'步骤: {step_info.step_number + 1}/{step_info.max_steps}')
		parts.append(f'\n可交互元素 ({len(self.dom_service.selector_map)} 个):\n{dom_text}')
		return '\n'.join(parts)

	# =========================================================================
	# 单步执行
	# =========================================================================

	async def step(self, step_info: AgentStepInfo | None = None) -> None:
		"""执行一步：observe → think → act → record"""
		step_start = time.time()

		# ---- Phase 1: Observe ----
		url = await self.browser_session.get_current_url()
		dom_root = await self.dom_service.build_enhanced_dom_tree()
		dom_text, _ = self.dom_service.serialize_dom_tree(dom_root)
		page_state = self._build_page_state(url, dom_text, step_info)
		logger.debug(f'Step {self.state.n_steps}: URL={url}')

		# ---- Phase 2: Think ----
		action_desc = self._get_action_descriptions()
		system_msg = SystemMessage(
			content=SYSTEM_PROMPT.format(
				action_descriptions=action_desc,
				page_state=page_state,
			)
		)
		user_msg = UserMessage(content=f'任务: {self.task}\n\n请决定下一步操作。')

		logger.debug(f'Step {self.state.n_steps}: calling LLM...')
		response = await self.llm.ainvoke(
			[system_msg, user_msg],
			output_format=self.AgentOutput,
		)
		self.state.last_model_output = response
		logger.debug(
			f'Step {self.state.n_steps}: LLM returned {len(response.action)} action(s)'
		)

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

		# ---- Phase 4: Record ----
		browser_snapshot = BrowserStateHistory(
			url=url,
			title=await self.browser_session.get_title(),
		)
		metadata = StepMetadata(
			step_number=self.state.n_steps,
			step_start_time=step_start,
			step_end_time=time.time(),
		)
		history_item = AgentHistory(
			model_output=response,
			result=results,
			state=browser_snapshot,
			metadata=metadata,
		)
		self.history.history.append(history_item)
		self.state.n_steps += 1

	# =========================================================================
	# 主循环
	# =========================================================================

	async def run(self, max_steps: int = 50) -> AgentHistoryList:
		"""主循环：反复执行 step() 直到任务完成或步数耗尽"""
		logger.info(f'开始执行任务: {self.task}')

		while self.state.n_steps <= max_steps:
			# 检查是否已完成
			if self.history.is_done():
				logger.info(f'任务在第 {self.state.n_steps - 1} 步完成')
				break

			# 检查连续失败
			if self.state.consecutive_failures >= 5:
				logger.error(f'连续失败 {self.state.consecutive_failures} 次，终止')
				break

			# 构建步骤信息
			current_step = self.state.n_steps - 1  # 0-indexed
			step_info = AgentStepInfo(
				step_number=current_step,
				max_steps=max_steps,
			)

			try:
				await self.step(step_info)
				# 成功的步骤：有 last_result 且没有 error
				if self.state.last_result and all(
					r.error is None for r in self.state.last_result
				):
					self.state.consecutive_failures = 0
				else:
					self.state.consecutive_failures += 1
			except Exception as e:
				logger.error(f'Step {self.state.n_steps} 异常: {e}')
				self.state.consecutive_failures += 1
				self.state.n_steps += 1

			# 检查是否是最后一步
			if step_info.is_last_step():
				logger.info(f'达到最大步数 {max_steps}，强制结束')
				break

		logger.info(
			f'任务执行完毕，共 {len(self.history.history)} 步，'
			f'状态: {"成功" if self.history.is_done() else "未完成"}'
		)
		return self.history
