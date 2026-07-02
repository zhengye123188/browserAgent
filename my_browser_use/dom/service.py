import logging

from my_browser_use.browser.session import BrowserSession
from my_browser_use.dom.views import EnhancedDOMTreeNode

logger = logging.getLogger(__name__)

class DomService:
    """从 CDP 获取 DOM / AX / Snapshot 数据，构建增强 DOM 树"""
    INTERACTIVE_TAGS = {
        "a", "button", "input", "select", "textarea",
        "option", "label", "summary", "video", "audio"
    }
    INTERACTIVE_ROLES = {
        "button", "link", "textbox", "combobox",
        "checkbox", "radio", "menuitem"
    }
    # 需要展示的常用属性
    SHOW_ATTRS = {"type", "placeholder", "href", "value", "name", "id"}

    def __init__(self, browser_session: BrowserSession):
        self.selector_map: dict[int, EnhancedDOMTreeNode] = {}
        self.browser_session = browser_session

    @staticmethod
    def _parse_attributes(attr_list: list[str]) -> dict[str, str]:
        """CDP 的 attributes 是 [key1, val1, key2, val2, ...] 平铺列表，转成 dict"""
        return dict(zip(attr_list[::2], attr_list[1::2]))

    async def _get_all_trees(self) -> dict:
        """并行获取 DOM 树、AX 树、布局快照"""
        cdp = self.browser_session._cdp_client
        assert cdp, '浏览器未启动，先调用 browser_session.start()'

        # ============================================================
        # 1. DOM.getDocument — 完整 DOM 树
        # ============================================================
        dom_result=await cdp.send.DOM.getDocument(
            params={'depth':-1,'pierce':True}
        )
        dom_tree = dom_result['root']
        logger.info('已获取 DOM 树')
        # ============================================================
        # 2. Accessibility.getFullAXTree — 无障碍访问树
        # ============================================================
        ax_result=await cdp.send.Accessibility.getFullAXTree()
        ax_nodes = ax_result.get('nodes', [])
        logger.info(f'已获取 AX 树，共 {len(ax_nodes)} 个节点')
        # ============================================================
        # 3. DOMSnapshot.captureSnapshot — 布局快照
        # ============================================================
        snapshot_result = await cdp.send.DOMSnapshot.captureSnapshot(
            params={'computedStyles': ['display', 'visibility', 'cursor', 'opacity']}
        )
        snapshot = snapshot_result  # 里面是 documents[0].layout 的列式数据
        logger.info('已获取布局快照')

        return {
            'dom_tree': dom_tree,
            'ax_nodes': ax_nodes,
            'snapshot': snapshot,
        }

    def _build_ax_mapping(self, ax_nodes: list[dict]) -> dict[int, dict]:
        """构建 backend_node_id => ax 节点信息 索引表"""
        ax_map = {}
        for ax_node in ax_nodes:
            backend_id = ax_node.get('backendDOMNodeId')
            if backend_id is None:
                continue
            ax_map[backend_id] = {
                'role': ax_node.get('role', {}).get('value'),
                'name': ax_node.get('name', {}).get('value'),
            }
        return ax_map

    def _build_snapshot_visibility_map(self, snapshot: dict) -> dict[int, dict]:
        """
        解析 DOMSnapshot 列式数据，生成 backend_node_id -> 可视属性映射
        """
        visibility_map = {}
        documents = snapshot.get('documents', [])
        if not documents:
            return visibility_map

        doc = documents[0]
        layout = doc.get('layout', {})  # ← 修复点 1：是 layout 不是 nodes
        backend_ids = layout.get('backendNodeId', [])
        computed_styles = layout.get('computedStyles', [])

        # ← 修复点 2：computedStyles 已是按请求顺序排列的值
        style_names = ['display', 'visibility', 'cursor', 'opacity']

        for idx, backend_id in enumerate(backend_ids):
            style_values = computed_styles[idx] if idx < len(computed_styles) else []
            style_dict = dict(zip(style_names, style_values))

            display = style_dict.get('display', 'block')
            visibility = style_dict.get('visibility', 'visible')
            opacity = float(style_dict.get('opacity', '1.0'))

            is_visible = not (display == 'none' or visibility == 'hidden' or opacity <= 0)

            visibility_map[backend_id] = {
                'is_visible': is_visible,
                # ← 修复点 3：暂时不判断可滚动，后续按 overflow 处理
                'is_scrollable': False,
            }
        return visibility_map

    def _recursive_build_enhanced_node(
            self,
            raw_dom_node: dict,
            ax_map: dict[int, dict],
            vis_map: dict[int, dict],
    ) -> EnhancedDOMTreeNode:
        """递归转换单个 CDP 原生 DOM 节点为 EnhancedDOMTreeNode"""

        node_id = raw_dom_node.get('nodeId', 0)
        backend_node_id = raw_dom_node.get('backendNodeId', 0)
        node_type = raw_dom_node.get('nodeType', 9)
        node_name = raw_dom_node.get('nodeName', '#document')
        node_value = raw_dom_node.get('nodeValue')
        raw_attrs = raw_dom_node.get('attributes', [])
        attr_dict = self._parse_attributes(raw_attrs)

        ax_info = ax_map.get(backend_node_id, {})
        ax_role = ax_info.get('role')
        ax_name = ax_info.get('name')

        # ← 修复点 4：不在 snapshot 中的节点默认可见
        vis_info = vis_map.get(backend_node_id, {'is_visible': True, 'is_scrollable': False})
        is_visible = vis_info['is_visible']
        is_scrollable = vis_info['is_scrollable']

        children = []
        for child_raw in raw_dom_node.get('children', []):
            child_node = self._recursive_build_enhanced_node(child_raw, ax_map, vis_map)
            children.append(child_node)

        return EnhancedDOMTreeNode(
            node_id=node_id,
            backend_node_id=backend_node_id,
            node_type=node_type,
            node_name=node_name,
            node_value=node_value,
            attributes=attr_dict,
            children_nodes=children,
            ax_role=ax_role,
            ax_name=ax_name,
            is_visible=is_visible,
            is_scrollable=is_scrollable,
        )

    async def build_enhanced_dom_tree(self) -> EnhancedDOMTreeNode:
        """
        对外暴露主方法：
        1. 获取全部 CDP 三棵树数据
        2. 构建 AX、Snapshot 索引映射
        3. 递归转换完整 DOM 树为 EnhancedDOMTreeNode 根节点
        """
        tree_data = await self._get_all_trees()
        raw_dom_root = tree_data['dom_tree']
        ax_nodes = tree_data['ax_nodes']
        snapshot = tree_data['snapshot']

        ax_mapping = self._build_ax_mapping(ax_nodes)
        vis_mapping = self._build_snapshot_visibility_map(snapshot)

        root_node = self._recursive_build_enhanced_node(raw_dom_root, ax_mapping, vis_mapping)
        logger.info(f'增强 DOM 树构建完成，根节点 backend_id={root_node.backend_node_id}')
        return root_node

    # 追加到 DomService 类中
    def serialize_dom_tree(self, root: EnhancedDOMTreeNode) -> tuple[str, dict[int, EnhancedDOMTreeNode]]:
        """
        遍历增强DOM树，给可见可交互元素分配序号
        返回：(格式化文本字符串, 序号映射字典 index -> 对应DOM节点)
        输出示例：
        [0]<a href="/home">首页</a>
        [1]<button>登录</button>
        [2]<input type='text' placeholder='搜索'/>
        """
        output_lines = []
        selector_map: dict[int, EnhancedDOMTreeNode] = {}
        node_index = 0  # 仅可交互元素自增编号

        def dfs(node: EnhancedDOMTreeNode):
            nonlocal node_index
            # 父节点不可见，整棵子树直接跳过，不再递归子节点
            if not node.is_visible:
                return

            tag = node.tag_name
            # 非元素节点（文本/文档等）只递归子节点，不参与编号输出
            if tag is None:
                for child in node.children_nodes:
                    dfs(child)
                return

            # 判断是否可交互
            is_interactive = (tag in self.INTERACTIVE_TAGS) or (node.ax_role in self.INTERACTIVE_ROLES)
            if not is_interactive:
                # 不可交互元素仅递归子节点
                for child in node.children_nodes:
                    dfs(child)
                return

            # ===== 可交互节点，生成文本行并存入映射 =====
            selector_map[node_index] = node

            # 拼接需要展示的属性
            attr_parts = []
            for k, v in node.attributes.items():
                if k in self.SHOW_ATTRS and v:
                    attr_parts.append(f"{k}='{v}'")
            attr_str = f" {' '.join(attr_parts)}" if attr_parts else ""

            # 递归获取全部子孙文本，截断100字符避免超长
            full_text = node.get_all_text().strip()
            inner_text = full_text[:100]

            # 自闭合标签判断
            self_closing_tags = {"input", "br", "img", "hr"}
            if tag in self_closing_tags:
                line = f"[{node_index}]<{tag}{attr_str}/>"
            else:
                line = f"[{node_index}]<{tag}{attr_str}>{inner_text}</{tag}>"

            output_lines.append(line)
            node_index += 1

            # 继续递归当前可交互节点的子节点
            for child in node.children_nodes:
                dfs(child)

        # 启动深度优先遍历
        dfs(root)
        self.selector_map = selector_map
        return "\n".join(output_lines), selector_map