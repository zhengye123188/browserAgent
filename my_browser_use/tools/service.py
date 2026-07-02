from my_browser_use.browser.session import BrowserSession
from my_browser_use.dom.service import DomService
from my_browser_use.tools.views import ActionModel
from my_browser_use.tools.views import (
    ActionResult, ActionModel,
    DoneAction, GoToUrlAction, GoBackAction,
    ScrollAction, ClickElementAction, InputTextAction,
)

class Tools:
    """操作注册表：收集所有可用 Action，负责查找和执行"""

    def __init__(self, browser_session: BrowserSession, dom_service: DomService | None = None):
        self.browser_session = browser_session
        self.dom_service = dom_service or DomService(browser_session)
        self._registry: dict[str, type[ActionModel]] = {}
        self._register_actions()


    # 注册
    def _register_actions(self) -> None:
        """把所有 Action 类注册到 _registry 字典中"""
        self._registry['click_element'] = ClickElementAction
        self._registry['done'] = DoneAction
        self._registry['go_back'] = GoBackAction
        self._registry['go_to_url'] = GoToUrlAction
        self._registry['input_text'] = InputTextAction
        self._registry['scroll'] = ScrollAction

    # 执行
    async def execute_action(self, action_name: str, params: dict) -> ActionResult:
        """根据 action 名称查找对应类，用 params 实例化，执行并返回结果"""
        action_cls = self._registry.get(action_name) # 这是个类，获取需要的action类
        if action_cls is None:
            return ActionResult(
                success=False,
                is_done=False,
                error=f"未知操作 '{action_name}'，可用操作: {list(self._registry.keys())}",
            )

        try:
            action = action_cls(**params)# 用蓝图 + 参数，造出实例。等价于：action = ClickElementAction(index=3)
        except Exception as e:
            return ActionResult(
                success=False,
                is_done=False,
                error=f'参数校验失败: {e}',
            )

        return await action.execute(
            session=self.browser_session,
            dom_service=self.dom_service,
        )

    # 获取所有注册的 Action 类（用于构建 Union 类型）
    def get_action_classes(self) -> list[type[ActionModel]]:
        """返回所有注册 Action 的类列表"""
        return list(self._registry.values())

    # LLM Schema 生成
    def get_action_schemas(self) -> list[dict]:
        """生成所有 Action 的 JSON Schema，用于 LLM function calling
            传给 LLM:
            tools: [
                {"function": {"name": "click_element", "parameters": {...}}},
                {"function": {"name": "input_text", "parameters": {...}}},
                {"function": {"name": "scroll", "parameters": {...}}},
            ...
            ]
        """
        schemas = []
        for action_name, action_cls in self._registry.items():
            schema = action_cls.model_json_schema()
            func_def = {
                'type': 'function',
                'function': {
                    'name': action_name,
                    'description': schema.get('description', ''),
                    'parameters': schema,
                },
            }
            schemas.append(func_def)
        return schemas