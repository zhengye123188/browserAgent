import asyncio
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
    """ClickElementAction 的核心流程

        selector_map[self.index]  →  拿到 EnhancedDOMTreeNode
        │
        ▼
        backend_node_id  →  CDP: DOM.scrollIntoViewIfNeeded(...)  ← 滚到可见
        │
        ▼
        CDP: DOM.getBoxModel(backendNodeId)  →  拿到元素四个角的坐标
        │
        ▼
        算中心点: (x + width/2, y + height/2)
        │
        ▼
        CDP: Input.dispatchMouseEvent(type='mouseMoved')   ← 鼠标移到中心
        │
        ▼
        CDP: Input.dispatchMouseEvent(type='mousePressed')  ← 按下左键
        │
        ▼
        CDP: Input.dispatchMouseEvent(type='mouseReleased') ← 松开左键
        │
        ▼
        ActionResult(success=True, content="Clicked button '登录'")"""
    name: str = Field(default="click_element")
    index: int = Field(ge=1, description="要点击的元素编号，来自browser_state")

    async def execute(self, session, dom_service) -> ActionResult:
        try:
            # 1. 找节点
            node = dom_service.selector_map.get(self.index)
            if node is None:
                return ActionResult(
                    success=False, is_done=False,
                    error=f"元素 {self.index} 不存在，请刷新页面状态"
                )

            # 2. 滚到可见
            await session._cdp_client.send.DOM.scrollIntoViewIfNeeded(
                params={'backendNodeId': node.backend_node_id}
            )
            await asyncio.sleep(0.05)

            # 3. 拿坐标
            box = await session._cdp_client.send.DOM.getBoxModel(
                params={'backendNodeId': node.backend_node_id}
            )
            content = box['model']['content']

            # 4. 算中心点
            center_x = sum(content[i] for i in range(0, 8, 2)) / 4
            center_y = sum(content[i] for i in range(1, 8, 2)) / 4

            # 5-7. 模拟点击
            await session._cdp_client.send.Input.dispatchMouseEvent(
                params={'type': 'mouseMoved', 'x': center_x, 'y': center_y}
            )
            await session._cdp_client.send.Input.dispatchMouseEvent(
                params={'type': 'mousePressed', 'x': center_x, 'y': center_y,
                        'button': 'left', 'clickCount': 1}
            )
            await asyncio.sleep(0.08)
            await session._cdp_client.send.Input.dispatchMouseEvent(
                params={'type': 'mouseReleased', 'x': center_x, 'y': center_y,
                        'button': 'left', 'clickCount': 1}
            )
            # 生成友好的反馈文本
            desc = self._describe_element(node)
            return ActionResult(
                success=True, is_done=False,
                content=f"已点击 {desc}",
            )
        except Exception as e:
            return ActionResult(success=False, is_done=False, error=str(e))

    def _describe_element(self, node):
        """生成元素的文本描述，比如 '按钮"登录"' """
        tag = node.tag_name or "元素"
        text = ""
        if node.ax_name:
            text = f'"{node.ax_name}"'
        elif node.node_value:
            text = f'"{node.node_value[:30]}"'
        role = node.ax_role or tag
        return f"{role} {text}".strip()

class InputTextAction(ActionModel):
    name: str = Field(default="input_text")
    index: int = Field(ge=1, description="输入框的元素编号")
    text: str = Field(description="要输入的文本")

    async def execute(self, session, dom_service) -> ActionResult:
        try:
            # 1. 找节点
            node = dom_service.selector_map.get(self.index)
            if node is None:
                return ActionResult(
                    success=False, is_done=False,
                    error=f"元素 {self.index} 不存在",
                )

            # 2. 滚到可见
            await session._cdp_client.send.DOM.scrollIntoViewIfNeeded(
                params={'backendNodeId': node.backend_node_id}
            )
            await asyncio.sleep(0.05)

            # 3. 拿坐标 + 算中心点（和 click 一样）
            box = await session._cdp_client.send.DOM.getBoxModel(
                params={'backendNodeId': node.backend_node_id}
            )
            coords = box['model']['content']
            cx = sum(coords[i] for i in range(0, 8, 2)) / 4
            cy = sum(coords[i] for i in range(1, 8, 2)) / 4

            # 4. 点击聚焦
            await session._cdp_client.send.Input.dispatchMouseEvent(
                params={'type': 'mouseMoved', 'x': cx, 'y': cy}
            )
            await session._cdp_client.send.Input.dispatchMouseEvent(
                params={'type': 'mousePressed', 'x': cx, 'y': cy,
                        'button': 'left', 'clickCount': 1}
            )
            await session._cdp_client.send.Input.dispatchMouseEvent(
                params={'type': 'mouseReleased', 'x': cx, 'y': cy,
                        'button': 'left', 'clickCount': 1}
            )
            await asyncio.sleep(0.05)

            # 5. 插入文本
            await session._cdp_client.send.Input.insertText(
                params={'text': self.text}
            )

            return ActionResult(
                success=True, is_done=False,
                content=f"已在 {node.tag_name} 中输入 '{self.text}'",
            )
        except Exception as e:
            return ActionResult(success=False, is_done=False, error=str(e))

class GoToUrlAction(ActionModel):
    name: str = Field(default="go_to_url")
    url: str = Field(description="要导航到的 URL")

    async def execute(self, session, dom_service) -> ActionResult:
        try:
            await session.navigate(self.url)
            return ActionResult(
                success=True,
                is_done=False,
                content=f'已导航到 {self.url}',
            )
        except Exception as e:
            return ActionResult(
                success=False,
                is_done=False,
                error=str(e),
            )