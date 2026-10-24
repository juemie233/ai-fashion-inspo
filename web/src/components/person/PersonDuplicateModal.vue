<script setup lang="ts">
/** 同名博主建议弹窗：归一化昵称相同的多条记录 → 合并为同一人 / 删掉空壳。
 *
 * 只给建议、由用户判断：同名未必是同一人（也可能是两个人恰好重名），所以不自动合并，
 * 每条都标出平台、来源、素材数，让人一眼看出哪个是真身、哪个是重复登记的空壳。
 * 已在同一人物组里的账号后端不会再报（那正是「同一个人」的正确表达方式）。
 */

import type { DuplicateNameGroup } from '@/api/persons'
import { SOURCE_LABELS } from '@/composables/usePersonList'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'
import { formatDate } from '@/utils/format'

const props = defineProps<{
  /** 弹窗开关（v-model:visible） */
  visible: boolean
  /** 同名分组 */
  groups: DuplicateNameGroup[]
  /** 加载中 */
  loading: boolean
  /** 动作进行中（按钮禁用防连点） */
  busy: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  /** 把一组同名账号合并为同一人（人物组） */
  merge: [group: DuplicateNameGroup]
  /** 删掉组内没有任何素材的空壳账号 */
  removeEmpty: [group: DuplicateNameGroup]
}>()

/** 组内是否还有「0 素材」的空壳（决定「删除空壳」按钮是否可用） */
function hasEmptyAccount(group: DuplicateNameGroup): boolean {
  return group.items.some((item) => item.inspiration_count === 0)
}

/** 平台中文（未知平台原样显示） */
function platformLabel(platform: string): string {
  // 同名建议里的 platform 是后端返回的字符串，可能不在已知枚举内 → 原样显示
  const labels = PERSON_PLATFORM_LABELS as Record<string, string | undefined>
  return labels[platform] ?? platform
}

/** 来源中文（未知来源原样显示） */
function sourceLabel(source?: string | null): string {
  return SOURCE_LABELS[source || 'manual'] ?? source ?? '手动'
}

function close(): void {
  emit('update:visible', false)
}
</script>

<template>
  <a-modal
    :visible="props.visible"
    title="同名博主建议"
    :footer="false"
    width="760px"
    @cancel="close"
  >
    <a-spin :loading="props.loading" style="display: block">
      <div v-if="!props.loading && props.groups.length === 0" class="dup-empty">
        没有需要处理的同名账号：重名账号要么已经合并为同一个人，要么根本不存在重名。
      </div>
      <div v-for="group in props.groups" :key="group.key" class="dup-group">
        <div class="dup-head">
          <span class="dup-name">{{ group.items[0]?.name || group.key }}</span>
          <span class="dup-count">{{ group.items.length }} 条记录</span>
          <a-space :size="8">
            <a-popconfirm
              :content="`把这 ${group.items.length} 条记录合并为同一人（人物组）？以素材最多的「${group.items[0]?.name}」为主账号，列表里会折叠成一行。`"
              @ok="emit('merge', group)"
            >
              <a-button size="mini" type="primary" :disabled="props.busy">合并为同一人</a-button>
            </a-popconfirm>
            <a-popconfirm
              content="删除本组里没有任何素材的空壳账号？有素材的会被后端拦下（不会误删）。"
              :disabled="!hasEmptyAccount(group)"
              @ok="emit('removeEmpty', group)"
            >
              <a-button
                size="mini"
                status="danger"
                :disabled="props.busy || !hasEmptyAccount(group)"
              >
                删除空壳
              </a-button>
            </a-popconfirm>
          </a-space>
        </div>
        <a-table
          :data="group.items"
          :pagination="false"
          :bordered="false"
          size="small"
          row-key="id"
          :columns="[
            { title: '昵称', dataIndex: 'name', width: 180 },
            { title: '平台', dataIndex: 'platform', width: 100 },
            { title: '来源', dataIndex: 'source', width: 100 },
            { title: '素材数', dataIndex: 'inspiration_count', width: 90 },
            { title: '创建时间', dataIndex: 'created_at', width: 160 },
          ]"
        >
          <template #platform="{ record }">
            {{ platformLabel(record.platform) }}
          </template>
          <template #source="{ record }">
            {{ sourceLabel(record.source) }}
          </template>
          <template #inspiration_count="{ record }">
            {{ record.inspiration_count }}
          </template>
          <template #created_at="{ record }">
            <span style="white-space: nowrap">{{ formatDate(record.created_at) }}</span>
          </template>
        </a-table>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.dup-empty {
  padding: 24px 8px;
  color: var(--color-text-3);
  text-align: center;
}

.dup-group {
  padding: 8px 0 16px;
  border-bottom: 1px solid var(--color-border-2);
}

.dup-group:last-child {
  border-bottom: none;
}

.dup-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.dup-name {
  font-weight: 600;
}

.dup-count {
  color: var(--color-text-3);
  font-size: 12px;
}
</style>
