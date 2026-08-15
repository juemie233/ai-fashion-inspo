<script setup lang="ts">
/** 向量搜索入口：语义搜索 + 以图搜图。 */

import { ref, computed } from 'vue'

const props = defineProps<{
  /** 语义搜索执行中 */
  loading: boolean
  /** 语义搜索输入文本 */
  semanticText: string
}>()

const emit = defineEmits<{
  (e: 'update:semanticText', value: string): void
  (e: 'semanticSearch'): void
  (e: 'imagePicked', file: File): void
}>()

/** 语义搜索输入（双向绑定到父级） */
const semanticModel = computed({
  get: () => props.semanticText,
  set: (v: string) => emit('update:semanticText', v),
})

/** 图片文件选择框引用 */
const imageFileInput = ref<HTMLInputElement | null>(null)

/** 打开以图搜图文件选择 */
function openImagePicker() {
  imageFileInput.value?.click()
}

/** 选择图片后触发以图搜图（同时重置 input 以便下次选择同一文件） */
function onImagePicked(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  emit('imagePicked', file)
  if (input) input.value = ''
}
</script>

<template>
  <div class="vector-search-section">
    <n-space align="center">
      <n-input
        v-model:value="semanticModel"
        placeholder="语义搜索：输入描述，如「复古红格裙」「白色系甜美穿搭」"
        clearable
        size="small"
        style="width: 360px"
        @keyup.enter="emit('semanticSearch')"
      />
      <n-button type="primary" secondary size="small" :loading="loading" @click="emit('semanticSearch')">
        🧠 语义搜索
      </n-button>
      <n-divider vertical style="margin: 0 4px" />
      <n-button size="small" :loading="loading" @click="openImagePicker">
        🖼️ 以图搜图
      </n-button>
      <input
        ref="imageFileInput"
        type="file"
        accept="image/*"
        style="display: none"
        @change="onImagePicked"
      />
    </n-space>
  </div>
</template>

<style scoped>
.vector-search-section {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
</style>
