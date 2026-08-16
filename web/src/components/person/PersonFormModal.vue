<script setup lang="ts">
/** 人物新建/编辑对话框：包含内容类型（职业模特/穿搭博主）选择，落实 UI 区分。 */

import { ref, watch } from 'vue'
import { useMessage, type FormInst, type FormRules } from 'naive-ui'
import { createPerson, updatePerson, type PersonForm } from '@/api/persons'
import type { Person } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS, PERSON_TYPE_LABELS } from '@shared/types/person'

const props = defineProps<{
  show: boolean
  /** 编辑模式时传入人物对象；新建时传 null */
  person: Person | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'saved'): void
}>()

const message = useMessage()
const formRef = ref<FormInst | null>(null)
const submitting = ref(false)

/** 表单模型：默认「穿搭博主」（本项目主流人物为博主） */
const form = ref<PersonForm>({
  name: '',
  person_type: 'blogger',
  platform: 'other',
  platform_user_id: null,
  profile_url: null,
  bio: null,
})

const rules: FormRules = {
  name: { required: true, message: '请输入人物名称', trigger: ['input', 'blur'] },
}

// 打开对话框时初始化表单：编辑模式回填，新建模式重置
watch(
  () => props.show,
  (show) => {
    if (!show) return
    const p = props.person
    form.value = {
      name: p?.name ?? '',
      person_type: p?.person_type ?? 'blogger',
      platform: p?.platform ?? 'other',
      platform_user_id: p?.platform_user_id ?? null,
      profile_url: p?.profile_url ?? null,
      bio: p?.bio ?? null,
    }
  }
)

/** 提交：新建或更新 */
async function handleSubmit() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    if (props.person) {
      await updatePerson(props.person.id, form.value)
      message.success('已更新人物')
    } else {
      await createPerson(form.value)
      message.success('已创建人物')
    }
    emit('saved')
    emit('update:show', false)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    :title="person ? '编辑人物' : '新建人物'"
    style="width: 480px"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <n-form ref="formRef" :model="form" :rules="rules" label-placement="left" label-width="96">
      <n-form-item label="人物名称" path="name">
        <n-input v-model:value="form.name" placeholder="人物名 / 博主昵称" maxlength="128" />
      </n-form-item>

      <!-- 内容类型：UI 区分核心字段 -->
      <n-form-item label="内容类型" path="person_type">
        <n-radio-group v-model:value="form.person_type" name="person_type">
          <n-space>
            <n-radio-button value="blogger">
              {{ PERSON_TYPE_LABELS.blogger }}
            </n-radio-button>
            <n-radio-button value="model">
              {{ PERSON_TYPE_LABELS.model }}
            </n-radio-button>
          </n-space>
        </n-radio-group>
      </n-form-item>

      <n-form-item label="平台" path="platform">
        <n-select
          v-model:value="form.platform"
          :options="Object.entries(PERSON_PLATFORM_LABELS).map(([value, label]) => ({ label, value }))"
        />
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
