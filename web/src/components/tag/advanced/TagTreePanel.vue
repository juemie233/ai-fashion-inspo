<script setup lang="ts">
/** 层级树面板：a-tree 懒加载（按需展开）+ 拖拽移动子树（循环预检 + 后端校验）。 */

import { onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { TreeNodeData } from '@arco-design/web-vue'
import { IconFolder, IconTag } from '@arco-design/web-vue/es/icon'
import { getApiErrorMessage } from '@/utils/apiError'
import { fetchTree, moveTags } from '@/api/tagAdvanced'
import { CATEGORY_LABELS } from '@/api/tags'
import type { TreeItem } from '@/types/tagAdvanced'

/** 树节点（Arco a-tree 结构 + 自定义字段） */
interface TreeNode {
  key: number
  title: string
  isLeaf: boolean
  children: TreeNode[]
  _usage: number
  _category: string
}

const treeData = ref<TreeNode[]>([])
const loadingRoot = ref(false)
const expandedKeys = ref<number[]>([])

function toTreeNode(item: TreeItem): TreeNode {
  return {
    key: item.id,
    title: item.name,
    isLeaf: !item.has_children,
    children: [],
    _usage: item.usage_count,
    _category: item.category,
  }
}

function nodeTitle(node: TreeNode): string {
  const cat = CATEGORY_LABELS[node._category]
  return `${node.title}${cat ? `（${cat}）` : ''} · ${node._usage}`
}

/** 加载根节点 */
async function loadRoot() {
  loadingRoot.value = true
  try {
    const data = await fetchTree(null, 1, 500)
    treeData.value = data.items.map((i) => toTreeNode(i))
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载层级树失败'))
  } finally {
    loadingRoot.value = false
  }
}

/** 懒加载子节点（Arco load-more：返回 Promise 自动处理加载态） */
async function onLoadMore(node: TreeNodeData) {
  const t = node as unknown as TreeNode
  try {
    const data = await fetchTree(t.key, 1, 500)
    t.children = data.items.map((i) => toTreeNode(i))
    t.isLeaf = data.items.length === 0
  } catch {
    t.isLeaf = true
  }
}

/** 在已加载树中查找节点，返回其祖先链（不含自身）；未找到返回 null */
function findAncestors(nodes: TreeNode[], key: number, ancestors: number[] = []): number[] | null {
  for (const n of nodes) {
    if (n.key === key) return ancestors
    const r = findAncestors(n.children ?? [], key, [...ancestors, n.key])
    if (r !== null) return r
  }
  return null
}

/** 拖拽落下：dropPosition 0=放入目标内，-1/1=目标前/后（挂到目标父级） */
async function onDrop(info: {
  dragNode: TreeNodeData
  dropNode: TreeNodeData
  dropPosition: number
}) {
  const dragKey = Number(info.dragNode.key)
  const dropKey = Number(info.dropNode.key)
  if (dragKey === dropKey) return

  // 计算新父节点
  let newParent: number | null
  if (info.dropPosition === 0) {
    newParent = dropKey
  } else {
    const chain = findAncestors(treeData.value, dropKey)
    newParent = chain && chain.length ? chain[chain.length - 1] : null
  }
  // 循环预检：新父节点的祖先链上不得出现被拖节点
  if (newParent != null) {
    const chain = findAncestors(treeData.value, newParent)
    if (chain?.includes(dragKey)) {
      Message.warning('不能移动到自己的后代标签下')
      return
    }
  }

  try {
    const data = await moveTags([{ tag_id: dragKey, parent_id: newParent }])
    if (data.moved === 1) {
      Message.success('已移动')
      loadRoot()
    } else if (data.errors.length) {
      Message.warning(data.errors.map((e) => e.message).join('；'))
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '移动失败'))
  }
}

function expandAll() {
  expandedKeys.value = []
  const collect = (nodes: TreeNode[]) => {
    for (const n of nodes) {
      if (!n.isLeaf) {
        expandedKeys.value.push(n.key)
        collect(n.children ?? [])
      }
    }
  }
  collect(treeData.value)
  loadRoot()
}

onMounted(() => {
  loadRoot()
})
</script>

<template>
  <div class="tree-panel">
    <div class="tree-toolbar">
      <a-space>
        <a-button size="small" :loading="loadingRoot" @click="loadRoot">刷新</a-button>
        <a-button size="small" @click="expandAll">展开/收起</a-button>
      </a-space>
      <span class="tree-hint"
        >拖拽标签可移动到其它标签下；移动到根需拖到空白处（后端按「移到根」处理）</span
      >
    </div>

    <a-spin :loading="loadingRoot" class="tree-spin">
      <div v-if="!treeData.length && !loadingRoot" class="empty-tip">暂无标签</div>
      <a-tree
        v-else
        :data="treeData"
        :load-more="onLoadMore"
        :expanded-keys="expandedKeys"
        draggable
        block-node
        show-line
        @drop="onDrop"
      >
        <template #icon="{ isLeaf }">
          <IconFolder v-if="!isLeaf" style="color: #d97706" />
          <IconTag v-else style="color: #9ca3af" />
        </template>
        <template #title="node">
          {{ nodeTitle(node) }}
        </template>
      </a-tree>
    </a-spin>
  </div>
</template>

<style scoped>
.tree-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  overflow-y: auto;
}
.tree-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}
.tree-hint {
  font-size: 12px;
  color: #9ca3af;
}
.tree-spin {
  flex: 1;
  min-height: 0;
}
.empty-tip {
  padding: 40px;
  text-align: center;
  color: #9ca3af;
}
</style>
