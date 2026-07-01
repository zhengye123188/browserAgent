# Day 02 — Phase 2: CDP 基础，连接 Chrome

## CDP (Chrome DevTools Protocol) 工作流程

```
1. 启动 Chrome 子进程，带 --remote-debugging-port=9222
2. Chrome 自动在 localhost:9222 开一个 HTTP 调试端点
3. 访问 http://localhost:9222/json → 拿 WebSocket 地址
4. 用 cdp-use 连上 WebSocket
5. 发 CDP 命令（Page.navigate, Runtime.evaluate 等）控制浏览器
```

## 创建的文件

### `browser/profile.py` — 浏览器配置
Pydantic model，定义 Chrome 启动参数：
- `headless`: 是否无头模式
- `debugging_port`: CDP 端口
- `chrome_args` 属性：拼出完整的 Chrome 命令行参数列表

### `browser/session.py` — 浏览器会话
管理 Chrome 的完整生命周期：
- `start()`: 启动 Chrome → 拿 WebSocket 地址 → 连上 CDP
- `stop()`: 断开 CDP → 关闭 Chrome
- `navigate(url)`: 导航到网页（CDP Page.navigate）
- `get_title()`: 获取页面标题（CDP Runtime.evaluate 执行 document.title）
- `get_html()`: 获取完整 HTML

## 关键依赖

| 包 | 作用 |
|---|------|
| `cdp-use` | 封装 CDP WebSocket 通信，提供类型化的 CDP 命令接口 |
| `httpx` | 异步 HTTP 客户端，用来访问 localhost:9222/json |
| `pydantic` | 数据类 + 配置校验 |

## BrowserSession 架构设计

### `__init__` — 状态初始化

所有状态变量初始值都是 `None`，因为 `__init__` 只构造实例，不启动浏览器。启动是 `start()` 的事。

```python
self.profile = profile or BrowserProfile()  # 不传参 = 默认配置
self._chrome_process = None   # 浏览器还没开，当然没有进程
self._cdp_client = None       # CDP 还没连
self._ws_url = None           # WebSocket 地址还不知道
```

### `start()` — 三步启动

1. `_launch_chrome()` — 找 Chrome 路径 → 拼命令行 → `subprocess.Popen` 启动子进程
2. `_get_ws_url()` — 轮询 `http://localhost:9222/json`，等 Chrome 准备好
3. `CDPClient(ws_url)` — 连上 WebSocket

### 类型注解 `str | None`

Python 3.10+ 的新写法，等价于旧 `Optional[str]`。`str | None` = 这个值要么是字符串，要么是 None。

### `assert` 断言

`assert self._cdp_client, '浏览器未启动'` = 如果 `_cdp_client` 是 None，立即抛异常。
用于"程序员写错代码"的场景，不是处理用户输入。

### 职责分离

`session.py` 只管浏览器生命支持和暴露 CDP 连接。click、type、scroll 等用户动作属于 `tools/`，DOM 提取属于 `dom/`。
现在 session 里的 `navigate()`/`get_title()`/`get_html()` 是临时脚手架，验证 CDP 通了就会被替换。

## 关键代码示例

```python
# 连上 Chrome 后，发 CDP 命令导航
await cdp_client.send.Page.navigate(params={'url': 'https://example.com'})

# 执行 JS 拿页面标题
await cdp_client.send.Runtime.evaluate(params={'expression': 'document.title'})
```
