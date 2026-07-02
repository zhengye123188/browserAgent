# Day 05 — Phase 5: LLM 抽象层

## 为什么需要抽象层

不同 LLM 厂商的 API 格式各不相同，但 Agent 需要以统一方式调用它们：

| | OpenAI/DeepSeek | Anthropic Claude | Google Gemini |
|---|---|---|---|
| 消息格式 | `{"role": "user", "content": "..."}` | `{"role": "user", "content": "..."}` (简单文本一致) | `{"role": "user", "parts": [...]}` |
| 工具定义 | `{type: "function", function: {name, parameters}}` | `{name, input_schema}` | `{functionDeclarations: [...]}` |
| 工具调用返回 | `msg.tool_calls[0].function.name` | 遍历 `content` 找 `type=="tool_use"` | `candidates[0].content.parts[0].functionCall` |
| System 消息 | messages 数组里 | 顶层 `system` 参数 | `systemInstruction` 参数 |

**抽象层的作用：把这些差异藏在一个统一接口后面，Agent 不关心底层是哪个模型。**

## 设计模式：策略模式

```
          ┌──────────────┐
          │ BaseChatModel │  ← 定义统一接口（ainvoke）
          └──────┬───────┘
     ┌───────────┼───────────┐
     ▼           ▼           ▼
OpenAI兼容  Anthropic   Gemini
(DeepSeek,
 OpenAI,
 Groq...)
```

## 创建的文件

### `llm/messages.py` — 统一消息类型

四种内部消息类型，Agent 只用这些，不和厂商格式打交道：

| 类型 | 用途 | 关键字段 |
|------|------|---------|
| `SystemMessage` | 设定助手角色和行为 | `content: str` |
| `UserMessage` | 任务描述、页面状态 | `content: str` |
| `AssistantMessage` | LLM 历史决策 | `content: str`, `tool_calls: list[dict] \| None` |
| `ToolMessage` | 工具执行结果 | `tool_call_id: str`, `content: str` |

`tool_call_id` 很关键——LLM 可能同时调多个工具，返回结果时必须关联。

### `llm/base.py` — 抽象基类

```python
class BaseChatModel(ABC):
    def __init__(self, model: str):
        self.model = model
    
    @abstractmethod
    async def ainvoke(self, messages, tools=None) -> dict:
        """子类必须实现"""
        ...
```

两个关键设计：
- **`ABC` + `@abstractmethod`**：子类忘了实现 `ainvoke` 时，实例化直接报错（不是调用时报错）
- **返回 `dict` 而非复杂类型**：`{"action": "...", "params": {...}}`，简单够用

### `llm/openai_chat.py` — OpenAI 兼容家族

一个类覆盖 DeepSeek、OpenAI、Groq、通义千问、Moonshot 等所有 OpenAI 协议兼容的厂商。

```python
class ChatOpenAICompatible(BaseChatModel):
    def __init__(self, api_key, base_url, model):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    
    async def ainvoke(self, messages, tools=None) -> dict:
        openai_messages = self._convert_messages(messages)    # ① 转换
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=tools,                                       # ② 调用
        )
        return self._parse_response(response)                  # ③ 解析
```

三层转换：
1. **消息转换**：内部消息类型 → OpenAI 字典格式（基本一对一）
2. **API 调用**：tools 已是 OpenAI 格式（`get_action_schemas()` 生成的），不用再转
3. **响应解析**：`msg.tool_calls[0].function.name` + `json.loads(arguments)` → dict

切换厂商只需改三行配置：
```python
# DeepSeek → OpenAI 只需改 base_url + api_key + model
ChatOpenAICompatible(api_key="...", base_url="https://api.openai.com/v1", model="gpt-4o")
```

### `llm/anthropic_chat.py` — Anthropic Claude

和 OpenAI 兼容家族差异最大的实现。三个关键差异：

#### 差异 1：System 消息提取

Anthropic 的 system 提示词不是消息数组里的一项，而是顶层独立参数：
```python
# OpenAI: 混在 messages 里
[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

# Anthropic: 单独提取
messages = [{"role": "user", "content": "..."}]
system = "你是浏览器助手"  # 独立参数
```

#### 差异 2：工具格式转换

```python
# OpenAI 格式 → Anthropic 格式
{"type": "function", "function": {"name": "...", "parameters": {...}}}
# 转换为：
{"name": "...", "input_schema": {...}}  # parameters → input_schema
```

#### 差异 3：响应解析

```python
# OpenAI: tool_calls 在 message 层级
msg.tool_calls[0].function.name

# Anthropic: tool_use 在 content 数组里
for block in response.content:
    if block.type == "tool_use":
        block.name    # action 名
        block.input   # 参数，已经是 dict（不用 json.loads）
```

#### AssistantMessage 的 tool_calls 处理

Anthropic 的 assistant 消息里，工具调用放在 content 数组里，不是顶层 `tool_calls` 字段：
```python
# Anthropic 格式的 assistant 消息：
{"role": "assistant", "content": [
    {"type": "tool_use", "id": "...", "name": "click_element", "input": {"index": 3}}
]}
```

#### max_tokens 是必传参数

Anthropic API 要求必须传 `max_tokens`，不像 OpenAI/DeepSeek 可选。需要在 `__init__` 设默认值（如 4096）。

## 关键概念

### 抽象基类 vs 协议

| | `ABC`（继承） | `Protocol`（结构化） |
|---|---|---|
| 关系 | 显式 `class X(Base)` | 有方法就算，不需继承 |
| 检查时机 | 实例化时报错 | mypy 静态检查 |
| 适合场景 | 自己写所有子类 | 第三方类想兼容接口 |

browser-use 用 `Protocol`（更灵活），学习项目用 `ABC`（更直观）。

### 为什么把转换逻辑抽成静态方法

`_convert_messages` 和 `_parse_response` 不依赖 `self`——是纯函数。好处：
- **可单独测试**：不需要启动 API 就能验证转换逻辑
- **职责清楚**：`ainvoke` 只负责编排流程

### 信息只存一份

`get_action_schemas()` 自动从 Pydantic 类生成 JSON Schema。改了字段，schema 自动更新。不需要手写 JSON 维护两份信息。

## 对后续 Agent 的影响

Agent 只看到 `BaseChatModel` 接口，运行时选择实现：

```python
# Agent 内部：
response = await self.llm.ainvoke(messages, tools)
# → {"action": "click_element", "params": {"index": 3}}

# 不管 self.llm 是 DeepSeek、OpenAI 还是 Claude，用法完全一样
```
