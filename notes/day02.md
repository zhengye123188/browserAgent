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

## 关键代码示例

```python
# 连上 Chrome 后，发 CDP 命令导航
await cdp_client.send.Page.navigate(params={'url': 'https://example.com'})

# 执行 JS 拿页面标题
await cdp_client.send.Runtime.evaluate(params={'expression': 'document.title'})
```
