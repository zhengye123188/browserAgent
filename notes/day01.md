# Day 01 — 项目架构：从数据流角度理解各包职责

## 五个包的角色

一个网页任务从输入到执行，数据在五个包之间流转：

| 包 | 比喻 | 职责 |
|---|------|------|
| `agent/` | 大脑 | 主循环：observe → think → act，唯一调用 LLM 的地方 |
| `llm/` | 神经 | 封装 OpenAI / Anthropic / Ollama 等厂商差异，对上层提供统一接口 |
| `browser/` | 身体 | 启动 Chrome、管理 CDP 连接、管理标签页生命周期 |
| `dom/` | 眼睛 | 抓取 DOM 树 + 无障碍树 → 过滤不可见元素 → 序列化成带编号的纯文本 |
| `tools/` | 双手 | 动作注册表，统一入口执行 click / type / scroll / navigate 等 |

## 数据流向图

```
用户任务：比如 "去淘宝搜笔记本电脑"
    │
    ▼
agent/        ← 主循环：observe → think → act
    │
    ├──▶ browser/    ← 连 Chrome，发 CDP 指令
    │       │
    │       └──▶ dom/    ← 拿到页面 DOM 树，序列化成文本
    │
    ├──▶ llm/        ← 把页面文本 + 任务喂给 AI，AI 决策下一步
    │
    └──▶ tools/      ← 执行 AI 决定的动作（click、type、scroll...）
            │
            └──▶ browser/  ← 动作最终通过 CDP 发到 Chrome
                    │
                    └──▶ 页面变化 → dom/ 重新抓取 → agent/ 下一轮循环
```

## 为什么这样拆分

1. **单一职责**：每个包只管一件事，改了 browser 不影响 llm
2. **可替换**：换 LLM 厂商只改 `llm/`，换浏览器只改 `browser/`
3. **可测试**：每个模块能独立写测试，不需要真连 Chrome 也能测 agent 逻辑

## self 什么时候写，什么时候不写

### 类里面声明字段时 — 不写 self

```python
class BrowserProfile(BaseModel):
    headless: bool = Field(default=True)  # 不写 self，因为这是"类的蓝图"
```

声明字段发生在**类定义时**——Python 读到 `class` 那一行就开始执行类体。这时候实例还没出生，`self` 不存在，写了就报错。

### 方法里面调用时 — 写 self

```python
@property
def chrome_args(self):
    return f'...{self.debugging_port}'  # 写 self，因为这是"实例的方法"
```

方法体里的代码发生在**调用时**——等你 `profile.chrome_args` 的时候才执行。那时候实例已经创建完毕，`self` 就是当前这个实例自己。

### 打个比方

| | 什么时候 | 有实例吗 | 能写 self 吗 |
|---|---|---|---|
| 字段声明 `headless: bool = ...` | 类定义时（蓝图） | ❌ | ❌ |
| 方法体 `self.headless` | 方法被调用时（运行时） | ✅ | ✅ |

蓝图不会写"我这个房子有 3 扇窗"，只写"房子有 3 扇窗"。盖好的房子里你才说"我这间有 3 扇窗"。

## 参考

- browser-use 源码地址：`/Users/zy/PycharmProjects/browser-use`
- 本项目地址：`/Users/zy/PycharmProjects/browerAgent`
