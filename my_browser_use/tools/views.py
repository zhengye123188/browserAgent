from pydantic import BaseModel, ConfigDict,Field,field_validator

from my_browser_use.browser.session import BrowserSession
from my_browser_use.dom.service import DomService


class ActionResult(BaseModel):
    """操作执行结果，Agent 根据这个来决定下一步"""
    model_config = ConfigDict(extra='forbid')

    is_done: bool          # 整个任务是否完成？（绝大多数操作是 False）
    success: bool           # 当前操作是否成功
    content: str | None = None   # 给 LLM 看的反馈文本
    error: str | None = None     # 失败原因
    attachments: list[str] = []  # 附件路径列表（截图等）

class ActionModel(BaseModel):
    """所有浏览器操作的基类。每个具体操作（点击、输入、滚动等）都是它的子类。"""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(
        default="",  # 子类必须覆盖这个默认值
        description="操作名称，对应 LLM function calling 里的 function name"
    )

    async def execute(
            self,
            session: BrowserSession,
            dom_service: DomService,
    ) -> ActionResult:
        """执行这个操作。子类必须实现。"""
        raise NotImplementedError("子类必须实现 execute 方法")

class DoneAction(ActionModel):
    name: str = Field(default="done", description="标记任务完成")
    text: str = Field(description="任务完成的总结文本")

    async def execute(self, session, dom_service) -> ActionResult:
        return ActionResult(
            is_done=True,
            success=True,
            content=self.text,
        )

class GoBackAction(ActionModel):
    name: str = Field(default="go_back", description="返回上一页")
    async def execute(self, session, dom_service) -> ActionResult:
        try:
            # 第一步：拿导航历史
            history = await session._cdp_client.send.Page.getNavigationHistory()
            current_index = history['currentIndex']
            entries = history['entries']

            # 边界检查：已经在第一页，没有可后退的
            if current_index <= 0:
                return ActionResult(
                    success=False,
                    is_done=False,
                    error="无法后退，没有上一页了",
                )
            # 第二步：跳到前一条历史记录
            previous_entry_id = entries[current_index - 1]['id']
            await session._cdp_client.send.Page.navigateToHistoryEntry(
                params={'entryId': previous_entry_id}
            )
            return ActionResult(
                success=True,
                is_done=False,
                content='已返回上一页',
            )
        except Exception as e:
            return ActionResult(success=False, is_done=False, error=str(e))

class ScrollAction(ActionModel):
    name: str = Field(default="scroll", description="滚动页面")
    direction: str = Field(
        default="down",
        description="滚动方向，只能是 'up' 或 'down'"
    )
    amount: int = Field(
        default=300,
        ge=1,
        le=5000,
        description="滚动像素数，1 到 5000"
    )

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        if v not in ("up", "down"):
            raise ValueError(f"direction 只能是 'up' 或 'down'，收到: '{v}'")
        return v
    async def execute(self, session, dom_service) -> ActionResult:
        try:
            delta = self.amount if self.direction == "down" else -self.amount
            js = f"window.scrollBy(0, {delta})"
            await session._cdp_client.send.Runtime.evaluate(
                params={'expression': js}
            )
            direction_text = "向下" if self.direction == "down" else "向上"
            return ActionResult(
                success=True,
                is_done=False,
                content=f"页面已{direction_text}滚动 {self.amount} 像素",
            )
        except Exception as e:
            return ActionResult(success=False, is_done=False, error=str(e))

class ClickElementAction(ActionModel):
    name: str = Field(default="click_element")
    index: int = Field(ge=1, description="要点击的元素编号，来自browser_state")

    async def execute(self, session, dom_service) -> ActionResult:
        # dom_service 里存了上次 serialize_dom_tree 生成的 selector_map
        node = dom_service.selector_map.get(self.index)
        if node is None:
            return ActionResult(
                success=False, is_done=False,
                error=f"元素编号 {self.index} 不存在，页面可能已变化"
            )