<script setup lang="ts">
/** 人物新建/编辑对话框：按 kind（穿搭博主/职业模特）提交到对应 API。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, watch } from 'vue'
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
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  /** 保存成功，回传保存后的人物对象（新建/编辑均回传） */
  (e: 'saved', person: Person): void
}>()

const formRef = ref<FormInstance | null>(null)
const submitting = ref(false)

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
  }
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
    const saved = props.person ? await api.update(props.person.id, form.value) : await api.create(form.value)
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
    <a-form ref="formRef" :model="form" :rules="rules" label-align="left" :label-col-style="{ width: '96px' }">
      <a-form-item label="人物名称" field="name">
        <a-input v-model="form.name" placeholder="人物名 / 博主昵称" :max-length="128" />
      </a-form-item>

      <a-form-item label="平台" field="platform">
        <a-select
          v-model="form.platform"
          :options="Object.entries(PERSON_PLATFORM_LABELS).map(([value, label]) => ({ label, value }))"
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
      </a-form-item>

      <a-form-item label="简介" field="bio">
        <a-textarea
          :model-value="form.bio ?? undefined"
          placeholder="人物简介 / 风格描述"
          @input="(v: string) => (form.bio = v || null)"
        />
      </a-form-item>
    </a-form>

    <template #footer>
      <a-space style="display:flex;justify-content:flex-end">
        <a-button @click="emit('update:show', false)">取消</a-button>
        <a-button type="primary" :loading="submitting" @click="handleSubmit">保存</a-button>
      </a-space>
    </template>
  </a-modal>
</template>
