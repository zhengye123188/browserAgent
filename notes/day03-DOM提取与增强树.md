# Day 03 — Phase 3: DOM 提取与增强树

## 为什么不能直接用 `get_html()` 的原始 HTML

LLM 吃不下一整个网页的 HTML（几万行，大量无意义嵌套）。需要提取出精简的结构化视图：只包含可交互元素 + 位置信息。

## 三种 CDP 数据源

| CDP 调用 | 返回什么 | 独有信息 |
|---|---|---|
| `DOM.getDocument` | 完整 DOM 树 | 标签名、属性、父子层级、Shadow DOM |
| `Accessibility.getFullAXTree` | 无障碍访问树 | 语义角色（button/link/textbox）、名称（aria-label/文本）、disabled 状态 |
| `DOMSnapshot.captureSnapshot` | 布局快照 | 坐标（bounds）、computed styles、paint order、可见性 |

### 为什么三个都需要？

**三者信息互补，没有一个能单独提供全部：**

- **DOM 树** — 知道"有什么标签、什么属性"，但不知道元素在哪里、是否可见
- **AX 树** — 知道"这是什么（按钮？输入框？）、叫什么名字"，但不知道坐标和 CSS
- **Snapshot** — 知道"在页面上哪里、多大、是否隐藏"，但不知道语义和属性

关联 key：**`backendNodeId`** — CDP 内部跨帧唯一的节点标识符，DOM 节点和 AX 节点都带这个 ID。Snapshot 也用这个做 key。

## 数据流

```
CDP 调用（三路）
    │
    ├── DOM.getDocument ────────────┐
    ├── Accessibility.getFullAXTree ─┤── 融合成 EnhancedDOMTreeNode 树
    └── DOMSnapshot.captureSnapshot ┘
                                        │
                                        ▼
                                serialize_dom_tree()
                                （DFS 遍历 → 过滤 → 编号 → 文本 + selector_map）
                                        │
                                        ▼
                                文本喂给 LLM: "[0]<a>首页</a>\n[1]<button>登录</button>..."
                                selector_map 给 Agent: {0: node, 1: node, ...}
```

## 创建的文件

### `dom/views.py` — EnhancedDOMTreeNode

核心节点数据模型（pydantic `BaseModel`），融合三种数据源：

| 字段 | 来源 | 说明 |
|---|---|---|
| `node_id`, `backend_node_id`, `node_type`, `node_name`, `node_value`, `attributes`, `children_nodes` | DOM 树 | 标签、属性、层级 |
| `ax_role`, `ax_name` | AX 树 | 语义角色（button/link...）、无障碍名称 |
| `is_visible`, `is_scrollable` | Snapshot 计算 | 是否可见、是否可滚动 |

`@property tag_name` — 仅元素节点（node_type=1）返回小写标签名。

### `dom/service.py` — DomService

服务类（不继承 BaseModel，因为它是"做事情的"而非"存数据的"）：

| 方法 | 作用 |
|---|---|
| `_parse_attributes()` | CDP 平铺列表 `[k1,v1,k2,v2]` → dict |
| `_get_all_trees()` | 串行调 3 个 CDP API，返回原始数据 |
| `_build_ax_mapping()` | AX 节点列表 → `{backendNodeId: {role, name}}` 索引 |
| `_build_snapshot_visibility_map()` | Snapshot 列式数据 → `{backendNodeId: {is_visible, is_scrollable}}` 索引 |
| `_recursive_build_enhanced_node()` | 递归融合：DOM 节点 + AX 数据 + Snapshot 数据 → `EnhancedDOMTreeNode` |
| `build_enhanced_dom_tree()` | 公开入口：调以上所有方法，返回完整增强 DOM 树 |
| `_is_interactive()` | 判断节点是否可交互（标签白名单 + AX role 白名单） |
| `serialize_dom_tree()` | DFS 遍历 → 给可交互可见元素编号 → 返回 (文本, selector_map) |

## 关键概念

### 递归构建树（recursive build）

CDP 返回的 DOM 是嵌套结构，处理它的代码自然也是递归的。终止条件：节点没有 `children` 时，`for` 循环不执行，返回空 `children_nodes`。

### 列式存储（Snapshot）

Snapshot 不是嵌套树，而是多个平行数组按同一索引对齐：

```
layout.nodeName[3]    → 第 3 个节点的标签名
layout.backendNodeId[3] → 第 3 个节点的 backend id
layout.bounds[3]      → 第 3 个节点的坐标
layout.computedStyles[3] → 第 3 个节点的样式（按请求顺序：[display, visibility, cursor, opacity]）
```

这种"列式存储"比嵌套树紧凑，CDP 用它省带宽。

### selector_map 的作用

LLM 看文本里的 `[3]`，Agent 用 `selector_map[3]` 拿到完整节点对象（坐标、backendNodeId 等），然后调 CDP 执行操作。

```
LLM 输出: {"action": "click", "index": 3}
Agent 代码: node = selector_map[3]; cdp.send.DOM.click(backendNodeId=node.backend_node_id)
```

**LLM 只管决策（大脑），selector_map 是桥梁，CDP 是手。**

### 为什么 `is_visible` 只看 display 和 visibility

- `display: none` → 元素完全不渲染，没有盒子
- `visibility: hidden` → 渲染但不可见
- `opacity: 0` → 完全透明

简化版没处理父节点传递（父隐藏子也应隐藏）和 viewport 裁剪，后续可加。

## CDP 参数要点

- `DOM.getDocument(depth=-1, pierce=True)` — `depth=-1` 拿整棵树，`pierce=True` 穿透 Shadow DOM
- `DOMSnapshot.captureSnapshot(computedStyles=[...])` — 只取需要的 CSS 属性，computedStyles 按请求顺序排列为值数组
- CDP 返回的 `attributes` 是平铺列表 `[k1,v1,k2,v2,...]`，需转成 dict

## 参考

- browser-use 源码：`browser_use/dom/service.py`（1175 行）、`browser_use/dom/views.py`（1042 行）
- 本项目：`my_browser_use/dom/views.py`、`my_browser_use/dom/service.py`
