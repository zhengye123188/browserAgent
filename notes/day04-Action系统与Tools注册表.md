# Day 04 — Phase 4: Action 系统与 Tools 注册表

## Action 系统在架构中的位置

```
HTML/DOM → LLM → Action(结构化决策) → Tools Registry → CDP 命令 → 浏览器
```

Tools 是 LLM 决策和浏览器操作之间的翻译层。

## 设计模式：注册表模式

每个操作（click、type、scroll）是一个独立的 Pydantic 类，注册表把它们收集起来：

- **添加新操作**：写一个类，扔进注册表
- **LLM schema 自动生成**：Pydantic `model_json_schema()` 自动生成 function calling 参数
- **参数验证集中处理**：Pydantic 自带校验

如果不用注册表，每加一个操作要改 Agent 主循环的 if-else。用注册表后，加操作只需两行代码。

## 创建的文件

### `tools/views.py` — 数据模型

#### `ActionResult`（操作执行结果）

| 字段 | 类型 | 说明 |
|------|------|------|
| `is_done` | `bool` | 整个任务是否完成？只有 DoneAction 返回 True |
| `success` | `bool` | 当前操作是否成功？ |
| `content` | `str \| None` | 给 LLM 看的反馈文本，如 "Clicked button '登录'" |
| `error` | `str \| None` | 失败原因 |
| `attachments` | `list[str]` | 附件路径列表，默认 `[]` |

`is_done` 和 `success` 是分开的——操作可以成功执行但还没完成任务。

#### `ActionModel`（操作基类）

```python
class ActionModel(BaseModel):
    name: str = Field(default="", description="操作名称")
    
    async def execute(self, session, dom_service) -> ActionResult:
        raise NotImplementedError("子类必须实现")
```

三个设计点：
- **`name` 是 Pydantic 字段**（不是类变量），能被序列化生成 LLM schema
- **`execute` 抛 `NotImplementedError`**：子类忘了实现时大声报错，不静默失败
- **参数传 `session` + `dom_service`**：最小依赖原则——`session` 提供 CDP，`dom_service` 提供 selector_map

#### 6 个具体 Action

| Action | CDP 调用 | 复杂度 | 说明 |
|--------|---------|--------|------|
| `DoneAction` | 无 | ★ | `is_done=True`，标记任务完成 |
| `GoToUrlAction` | `Page.navigate` | ★ | 导航到 URL |
| `GoBackAction` | `Page.getNavigationHistory` + `Page.navigateToHistoryEntry` | ★★ | CDP 原生后退，需两步：查历史 → 跳转到上一个 |
| `ScrollAction` | `Runtime.evaluate('window.scrollBy(...)')` | ★★ | JS 方式滚动，`@field_validator` 校验 direction |
| `ClickElementAction` | `DOM.scrollIntoViewIfNeeded` + `DOM.getBoxModel` + `Input.dispatchMouseEvent` × 3 | ★★★ | 核心操作：滚到可见 → 拿坐标 → 算中心 → 模拟点击 |
| `InputTextAction` | 同 click + `Input.insertText` | ★★★ | 先点击聚焦，再 insertText |

### `tools/service.py` — Tools 注册表

```python
class Tools:
    _registry: dict[str, type[ActionModel]]  # 字符串 → 类（蓝图），不是实例
    
    def execute_action(self, action_name, params) -> ActionResult:
        action_cls = self._registry.get(action_name)  # 查表
        action = action_cls(**params)                  # 实例化（Pydantic 校验）
        return await action.execute(session, dom)      # 执行
    
    def get_action_schemas(self) -> list[dict]:
        # 遍历 _registry，用 model_json_schema() 生成 LLM function schema
```

核心：`execute_action` 三步走——查表 → 实例化 → 执行。

## 关键概念

### `action_cls(**params)` 的含义

- `action_cls` 是从注册表查出来的**类**（如 `ClickElementAction`），不是实例
- `**params` 把字典 `{"index": 3}` 展开成关键字参数 `index=3`
- `action_cls(**params)` 等价于 `ClickElementAction(index=3)`，创建实例

### selector_map 的时效性

`serialize_dom_tree()` 每次调用生成新的 selector_map，必须保存到 `self.selector_map`。页面刷新后旧映射会失效。

### `@field_validator` 的作用

在 Pydantic 创建实例时自动执行，早于 `execute`。拦截 LLM 可能输出的错误值（如 `direction="left"`），把异常转成 `ActionResult(error=...)` 返回给 LLM 自我修正。

### 为什么 `execute` 都用 try/except 兜底

网络请求可能失败、元素可能消失、CDP 可能超时。Action 层必须兜住异常，不能让它炸穿到 Agent 主循环。失败返回 `success=False` + error 文本，LLM 看了能决定重试还是放弃。

## 完整调用链

```
Agent:
  result = await tools.execute_action("click_element", {"index": 3})
    → Tools._registry["click_element"] → ClickElementAction 类
    → ClickElementAction(index=3) → Pydantic 自动校验 index ≥ 1
    → action.execute(session, dom_service)
        → dom_service.selector_map[3] → EnhancedDOMTreeNode
        → session._cdp_client.send.DOM.scrollIntoViewIfNeeded(...)
        → session._cdp_client.send.DOM.getBoxModel(...)
        → session._cdp_client.send.Input.dispatchMouseEvent(...) × 3
        → ActionResult(success=True, content="已点击 button '搜索'")
```
