from pydantic import BaseModel
from pydantic.dataclasses import dataclass

from my_browser_use.browser.views import BrowserStateHistory
from my_browser_use.tools.views import ActionModel, ActionResult


class AgentOutput(BaseModel):
    """LLM 的 JSON 输出格式"""
    thinking: str | None=None          # 思维链，可选
    evaluation_previous_goal: str | None=None  # 上一步目标评估
    memory: str | None=None            # 记忆（跨步骤的关键信息）
    next_goal: str | None=None         # 下一步目标
    action: list[ActionModel]     # 要执行的动作列表


class AgentState(BaseModel):
    """Agent 运行时状态 — 当前进度、失败计数、上一步结果"""
    n_steps: int = 1                        # 当前在第几步
    consecutive_failures: int = 0           # 连续失败次数
    last_result: list[ActionResult] | None = None   # 上一步的执行结果
    last_model_output: AgentOutput | None = None    # 上一步 LLM 的输出
    paused: bool = False                    # 是否暂停
    stopped: bool = False                   # 是否停止

class StepMetadata(BaseModel):
    step_number: int
    step_start_time: float
    step_end_time: float
    step_interval: float | None = None  # 距上一步结束的间隔

@dataclass  # 注意：用 dataclass 不是 BaseModel，因为这个对象不会被序列化
class AgentStepInfo:
    step_number: int
    max_steps: int

    def is_last_step(self) -> bool:
        return self.step_number >= self.max_steps - 1

class AgentHistory(BaseModel):
    model_output: AgentOutput | None   # LLM 这步的决策（可能为 None，比如初始化步骤）
    result: list[ActionResult]         # 执行后的结果列表
    state: BrowserStateHistory         # 执行后的浏览器状态
    metadata: StepMetadata | None      # 时间信息

class AgentHistoryList(BaseModel):
    """所有步骤的历史记录容器"""
    history: list[AgentHistory] = []

    def is_done(self) -> bool:
        """最后一步是否标记了任务完成"""
        if not self.history:
            return False
        last_results = self.history[-1].result
        return any(r.is_done for r in last_results)

    def final_result(self) -> str | None:
        """返回最后一步 done action 的 content"""
        if not self.history:
            return None
        for r in self.history[-1].result:
            if r.is_done and r.content:
                return r.content
        return None