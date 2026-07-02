from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EnhancedDOMTreeNode(BaseModel):
      """DOM 树核心节点数据结构，融合 CDP DOM 节点 + Accessibility AX 树信息"""

      model_config = ConfigDict(extra='forbid')

      # CDP 原生 DOM 基础字段
      node_id: int
      backend_node_id: int
      node_type: int  # 1=元素, 3=文本, 9=文档
      node_name: str
      node_value: str | None = None
      attributes: dict[str, str] = {}
      children_nodes: list[EnhancedDOMTreeNode] = []

      # 融合 AX 无障碍树信息
      ax_role: str | None = None
      ax_name: str | None = None

      # 页面可视 / 交互属性
      is_visible: bool = False
      is_scrollable: bool = False

      @property
      def tag_name(self) -> str | None:
              """仅元素节点 (node_type=1) 返回小写标签名，其余节点返回 None"""
              if self.node_type == 1:
                      return self.node_name
              return None

      def get_all_text(self):
          """递归收集自己和所有子节点的文本内容"""
          parts = []
          if self.node_value and self.node_type == 3:  # 3 = 文本节点
              parts.append(self.node_value)
          for child in self.children_nodes:
              parts.append(child.get_all_text())
          return "".join(parts)