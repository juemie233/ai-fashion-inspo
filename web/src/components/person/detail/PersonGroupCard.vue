<script setup lang="ts">
/** 人物组卡片（方案 B）：展示同组账号（跨平台同一人）、绑定/解绑/切主操作。
 *
 * 仅穿搭博主展示（职业模特暂不分组）。绑定流程：
 * 1. 未在组：输入目标博主名搜索 → 选中 → 「绑定为同一人」；
 * 2. 已在组：展示组内全部账号 + 「设为默认展示」（切主）+ 「移出本组」（解绑）。 */

import { onMounted, ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { bloggersApi, type PersonGroupInfo } from '@/api/persons'
import { PERSON_PLATFORM_LABELS, type Person } from '@shared/types/person'

const props = defineProps<{
  person: Person
}>()

const emit = defineEmits<{
  changed: [] // 组变更（绑定/解绑/切主）后通知父级刷新
}>()

/** 组信息（person_group_id 非空时加载） */
const groupInfo = ref<PersonGroupInfo | null>(null)
const loadingGroup = ref(false)

// ── 绑定：搜索目标博主 ──
const bindVisible = ref(false)
const targetOptions = ref<Person[]>([])
const targetSearching = ref(false)
const targetId = ref<number | undefined>(undefined)
const binding = ref(false)

const inGroup = () => props.person.person_group_id != null

async function loadGroupInfo() {
  const gid = props.person.person_group_id
  if (gid == null) {
    groupInfo.value = null
    return
  }
  loadingGroup.value = true
  try {
    groupInfo.value = await bloggersApi.fetchGroupInfo(gid)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载人物组信息失败'))
  } finally {
    loadingGroup.value = false
  }
}

/** 搜索目标博主（排除自身与已在同组的） */
async function searchTarget(kw: string) {
  if (!kw.trim()) {
    targetOptions.value = []
    return
  }
  targetSearching.value = true
  try {
    const items = await bloggersApi.suggest(kw.trim())
    // 排除自身；已同组的由后端 409 兜底，前端过滤自身即可
    targetOptions.value = items.filter((i) => i.id !== props.person.id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '搜索博主失败'))
  } finally {
    targetSearching.value = false
  }
}

async function confirmBind() {
  if (!targetId.value) {
    Message.warning('请先选择要绑定的博主')
    return
  }
  binding.value = true
  try {
    await bloggersApi.linkGroup({
      blogger_id: props.person.id,
      target_blogger_id: targetId.value,
    })
    Message.success('已绑定为同一人（人物组）')
    bindVisible.value = false
    targetId.value = undefined
    targetOptions.value = []
    await loadGroupInfo()
    emit('changed')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '绑定失败'))
  } finally {
    binding.value = false
  }
}

/** 解绑当前账号 */
function confirmUnlink() {
  Modal.confirm({
    title: '移出人物组',
    content: `将「${props.person.name}」移出该人物组？移出后成为独立账号；组内仅剩 1 个账号时组自动删除。`,
    onOk: async () => {
      try {
        await bloggersApi.unlinkGroup(props.person.id)
        Message.success('已移出人物组')
        await loadGroupInfo()
        emit('changed')
      } catch (e) {
        Message.error(getApiErrorMessage(e, '移出失败'))
      }
    },
  })
}

/** 设为组内默认展示（切主） */
async function setPrimary(bloggerId: number) {
  const gid = props.person.person_group_id
  if (gid == null) return
  try {
    await bloggersApi.setPrimaryGroup(gid, bloggerId)
    Message.success('已设为默认展示账号')
    await loadGroupInfo()
    emit('changed')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '设置失败'))
  }
}

onMounted(() => {
  loadGroupInfo()
})
</script>

<template>
  <a-card size="small" class="group-card" :loading="loadingGroup">
    <template #title>
      <span>同一个人物（跨平台账号）</span>
      <a-tooltip content="同一现实人物在抖音/小红书各有账号时，绑定为同一人，列表只显示一条">
        <a-typography-text type="secondary" style="font-size: 12px; margin-left: 8px"
          >同一人多个平台账号归为人物组</a-typography-text
        >
      </a-tooltip>
    </template>

    <!-- 未绑定：引导绑定 -->
    <div v-if="!inGroup()" class="group-empty">
      <a-typography-text type="secondary" style="font-size: 13px">
        该博主尚未绑定人物组。若他/她在其它平台（如抖音）也有账号，可绑定为同一人，避免列表重复。
      </a-typography-text>
      <a-button size="small" type="primary" style="margin-top: 10px" @click="bindVisible = true">
        绑定为同一人
      </a-button>
    </div>

    <!-- 已绑定：展示组员 -->
    <div v-else class="group-members">
      <div
        v-for="m in groupInfo?.members ?? []"
        :key="m.id"
        class="group-member-row"
        :class="{ primary: m.id === groupInfo?.primary_blogger_id }"
      >
        <a-tag :color="m.id === groupInfo?.primary_blogger_id ? 'arcoblue' : 'gray'">
          {{
            PERSON_PLATFORM_LABELS[m.platform as keyof typeof PERSON_PLATFORM_LABELS] || m.platform
          }}
        </a-tag>
        <span class="member-name">
          {{ m.name }}
          <template v-if="m.id === groupInfo?.primary_blogger_id">
            <span class="primary-mark">默认展示</span>
          </template>
        </span>
        <template v-if="m.id === props.person.id">
          <a-button size="mini" type="text" status="danger" @click="confirmUnlink">
            移出本组
          </a-button>
        </template>
        <a-button v-else size="mini" type="text" @click="setPrimary(m.id)"> 设为默认展示 </a-button>
      </div>
    </div>

    <!-- 绑定弹窗 -->
    <a-modal v-model:visible="bindVisible" title="绑定为同一人" :footer="false" :width="420">
      <div class="bind-tip">
        选择该博主的其它平台账号（如抖音账号），绑定后两者归为同一人物组，列表只显示默认展示账号。
      </div>
      <a-select
        v-model="targetId"
        :options="
          targetOptions.map((t) => ({
            label: `${t.name}（${PERSON_PLATFORM_LABELS[t.platform] || t.platform}）`,
            value: t.id,
          }))
        "
        :filter-option="false"
        :loading="targetSearching"
        placeholder="输入博主昵称搜索"
        allow-search
        allow-clear
        style="width: 100%; margin: 12px 0"
        @search="searchTarget"
      />
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button @click="bindVisible = false">取消</a-button>
        <a-button type="primary" :loading="binding" @click="confirmBind">确认绑定</a-button>
      </div>
    </a-modal>
  </a-card>
</template>

<style scoped>
.group-card {
  margin-bottom: 12px;
}
.group-empty {
  display: flex;
  flex-direction: column;
}
.group-members {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.group-member-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}
.group-member-row.primary {
  border-color: #2a78d6;
  background: #f7faff;
}
.member-name {
  font-weight: 500;
  font-size: 13px;
  flex: 1;
}
.primary-mark {
  font-size: 11px;
  color: #2a78d6;
  margin-left: 6px;
}
.bind-tip {
  font-size: 13px;
  color: #6b7280;
}
</style>
