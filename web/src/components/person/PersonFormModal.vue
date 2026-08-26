<script setup lang="ts">
/** 人物新建/编辑对话框：按 kind（穿搭博主/职业模特）提交到对应 API。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { computed, ref, watch } from 'vue'
import { Message, type FormInstance, type FieldRule } from '@arco-design/web-vue'
import { bloggersApi, modelsApi, type PersonForm } from '@/api/persons'
import type { Person } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'

const props = defineProps<{
  show: boolean
  /** 人物种类：blogger（穿搭博主）/ model（职业模特） */
  kind: 'blogger' | 'model'
  /** 编辑模式时传入人物对象；新建时传 null */
  person: Person | null
  /** 打开弹窗后自动调用「AI 生成」填充简介（详情页简介区域按钮触发） */
  autoGenerateBio?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  /** 保存成功，回传保存后的人物对象（新建/编辑均回传） */
  (e: 'saved', person: Person): void
  /** 自动生成已触发（无论成功/失败），父组件据此复位 autoGenerateBio */
  (e: 'auto-generate-done'): void
}>()

const formRef = ref<FormInstance | null>(null)
const submitting = ref(false)
/** AI 生成简介的加载态（调用 /{kind}/{id}/generate-bio） */
const generatingBio = ref(false)

const kindLabel = props.kind === 'blogger' ? '穿搭博主' : '职业模特'

/** 表单模型（类型由页面 Tab 决定，不再内置 person_type 字段） */
const form = ref<PersonForm>({
  name: '',
  platform: 'other',
  platform_user_id: null,
  xhs_id: null,
  ip_location: null,
  profile_url: null,
  bio: null,
})

const rules: Record<string, FieldRule | FieldRule[]> = {
  name: [{ required: true, message: '请输入人物名称' }],
  profile_url: [
    {
      validator: (value: string | null | undefined, callback: (error?: string) => void) => {
        if (!value) {
          callback()
          return
        }
        try {
          const p = new URL(value)
          if (p.protocol === 'http:' || p.protocol === 'https:') {
            callback()
            return
          }
        } catch {
          /* 非法 URL 落入下方错误 */
        }
        callback('主页链接仅支持 http/https 协议')
      },
    },
  ],
}

/** 合法主页链接（http/https）：非空时提供「打开主页」可点击入口 */
const profileUrl = computed(() => {
  const value = form.value.profile_url
  if (!value) return ''
  try {
    const p = new URL(value)
    if (p.protocol === 'http:' || p.protocol === 'https:') return value
  } catch {
    /* 非法 URL 不显示链接 */
  }
  return ''
})

// 打开对话框时初始化表单：编辑模式回填，新建模式重置
watch(
  () => props.show,
  (show) => {
    if (!show) return
    const p = props.person
    form.value = {
      name: p?.name ?? '',
      platform: p?.platform ?? 'other',
      platform_user_id: p?.platform_user_id ?? null,
      xhs_id: p?.xhs_id ?? null,
      ip_location: p?.ip_location ?? null,
      profile_url: p?.profile_url ?? null,
      bio: p?.bio ?? null,
    }
  },
)

/** 调用本地大模型根据标签生成简介，写入表单（仍需点「保存」才入库）。
 *
 * 仅编辑模式可用（新建时还没有标签/素材，无从生成）。生成结果直接覆盖
 * form.bio，用户可在文本域里继续编辑；后端错误通过 Message 提示。
 */
async function handleGenerateBio() {
  if (!props.person) return
  generatingBio.value = true
  try {
    const api = props.kind === 'blogger' ? bloggersApi : modelsApi
    const { bio } = await api.generateBio(props.person.id)
    form.value.bio = bio
    Message.success('已生成简介，可继续编辑后保存')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '生成简介失败'))
  } finally {
    generatingBio.value = false
  }
}

// 详情页简介区域的「AI 生成」入口：打开弹窗后自动触发一次生成（复用上面的逻辑），
// 完成后通知父组件复位标志，避免下次打开弹窗重复生成
watch(
  () => [props.show, props.autoGenerateBio],
  ([show, auto]) => {
    if (show && auto) {
      handleGenerateBio().finally(() => emit('auto-generate-done'))
    }
  },
)

/** 提交：新建或更新（按 kind 路由到对应 API） */
async function handleSubmit() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    const api = props.kind === 'blogger' ? bloggersApi : modelsApi
    const saved = props.person
      ? await api.update(props.person.id, form.value)
      : await api.create(form.value)
    Message.success(props.person ? `已更新${kindLabel}` : `已创建${kindLabel}`)
    emit('saved', saved)
    emit('update:show', false)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '保存失败'))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <a-modal
    :visible="show"
    :title="person ? `编辑${kindLabel}` : `新建${kindLabel}`"
    :width="480"
    @update:visible="(v: boolean) => emit('update:show', v)"
  >
    <a-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-align="left"
      :label-col-style="{ width: '96px' }"
    >
      <a-form-item label="人物名称" field="name">
        <a-input v-model="form.name" placeholder="人物名 / 博主昵称" :max-length="128" />
      </a-form-item>

      <a-form-item label="平台" field="platform">
        <a-select
          v-model="form.platform"
          :options="
            Object.entries(PERSON_PLATFORM_LABELS).map(([value, label]) => ({ label, value }))
          "
        />
      </a-form-item>

      <a-form-item label="小红书号" field="xhs_id">
        <a-input
          :model-value="form.xhs_id ?? undefined"
          placeholder="如 zhn20050228，可留空"
          :max-length="64"
          @input="(v: string) => (form.xhs_id = v || null)"
        />
      </a-form-item>

      <a-form-item label="IP属地" field="ip_location">
        <a-input
          :model-value="form.ip_location ?? undefined"
          placeholder="如 浙江，可留空"
          :max-length="64"
          @input="(v: string) => (form.ip_location = v || null)"
        />
      </a-form-item>

      <a-form-item label="平台用户 ID" field="platform_user_id">
        <a-input
          :model-value="form.platform_user_id ?? undefined"
          placeholder="用于「按博主采集」，可留空"
          :max-length="128"
          @input="(v: string) => (form.platform_user_id = v || null)"
        />
      </a-form-item>

      <a-form-item label="主页链接" field="profile_url">
        <a-input
          :model-value="form.profile_url ?? undefined"
          placeholder="https://..."
          @input="(v: string) => (form.profile_url = v || null)"
        />
        <a-link
          v-if="profileUrl"
          :href="profileUrl"
          target="_blank"
          rel="noopener noreferrer"
          style="display: inline-block; margin-top: 6px; font-size: 12px"
        >
          🔗 打开主页（新窗口）
        </a-link>
      </a-form-item>

      <a-form-item field="bio">
        <template #label>
          <span>简介</span>
          <a-button
            v-if="person"
            type="text"
            size="mini"
            :loading="generatingBio"
            style="margin-left: 8px; padding: 0 4px; height: auto"
            @click="handleGenerateBio"
          >
            ✨ AI 生成
          </a-button>
        </template>
        <a-textarea
          :model-value="form.bio ?? undefined"
          placeholder="人物简介 / 风格描述；编辑模式下可点「AI 生成」由本地大模型根据标签生成"
          :auto-size="{ minRows: 3, maxRows: 8 }"
          @input="(v: string) => (form.bio = v || null)"
        />
      </a-form-item>
    </a-form>

    <template #footer>
      <a-space style="display: flex; justify-content: flex-end">
        <a-button @click="emit('update:show', false)">取消</a-button>
        <a-button type="primary" :loading="submitting" @click="handleSubmit">保存</a-button>
      </a-space>
    </template>
  </a-modal>
</template>
