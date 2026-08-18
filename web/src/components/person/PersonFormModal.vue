<script setup lang="ts">
/** 人物新建/编辑对话框：按 kind（穿搭博主/职业模特）提交到对应 API。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, watch } from 'vue'
import { useMessage, type FormInst, type FormRules } from 'naive-ui'
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

const message = useMessage()
const formRef = ref<FormInst | null>(null)
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

const rules: FormRules = {
  name: { required: true, message: '请输入人物名称', trigger: ['input', 'blur'] },
  profile_url: {
    trigger: ['input', 'blur'],
    validator: (_rule, value: string | null) => {
      if (!value) return true
      try {
        const p = new URL(value)
        if (p.protocol === 'http:' || p.protocol === 'https:') return true
      } catch {
        /* 非法 URL 落入下方错误 */
      }
      return new Error('主页链接仅支持 http/https 协议')
    },
  },
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
    message.success(props.person ? `已更新${kindLabel}` : `已创建${kindLabel}`)
    emit('saved', saved)
    emit('update:show', false)
  } catch (e) {
    message.error(getApiErrorMessage(e, '保存失败'))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    :title="person ? `编辑${kindLabel}` : `新建${kindLabel}`"
    style="width: 480px"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <n-form ref="formRef" :model="form" :rules="rules" label-placement="left" label-width="96">
      <n-form-item label="人物名称" path="name">
        <n-input v-model:value="form.name" placeholder="人物名 / 博主昵称" maxlength="128" />
      </n-form-item>

      <n-form-item label="平台" path="platform">
        <n-select
          v-model:value="form.platform"
          :options="Object.entries(PERSON_PLATFORM_LABELS).map(([value, label]) => ({ label, value }))"
        />
      </n-form-item>

      <n-form-item label="小红书号" path="xhs_id">
        <n-input v-model:value="form.xhs_id" placeholder="如 zhn20050228，可留空" maxlength="64" />
      </n-form-item>

      <n-form-item label="IP属地" path="ip_location">
        <n-input v-model:value="form.ip_location" placeholder="如 浙江，可留空" maxlength="64" />
      </n-form-item>

      <n-form-item label="平台用户 ID" path="platform_user_id">
        <n-input
          v-model:value="form.platform_user_id"
          placeholder="用于「按博主采集」，可留空"
          maxlength="128"
        />
      </n-form-item>

      <n-form-item label="主页链接" path="profile_url">
        <n-input v-model:value="form.profile_url" placeholder="https://..." />
      </n-form-item>

      <n-form-item label="简介" path="bio">
        <n-input v-model:value="form.bio" type="textarea" placeholder="人物简介 / 风格描述" />
      </n-form-item>
    </n-form>

    <template #footer>
      <n-space justify="end">
        <n-button @click="emit('update:show', false)">取消</n-button>
        <n-button type="primary" :loading="submitting" @click="handleSubmit">保存</n-button>
      </n-space>
    </template>
  </n-modal>
</template>
