<script setup lang="ts">
/** 前后端 schema 版本握手横幅：版本不一致或后端不可达时给出醒目提示。 */

import { onMounted } from 'vue'
import { useSchemaCheck } from '@/composables/useSchemaCheck'
import { EXPECTED_SCHEMA_VERSION } from '@/utils/schemaVersion'

const { checking, mismatch, serverVersion, error, check } = useSchemaCheck()

onMounted(check)
</script>

<template>
  <a-alert
    v-if="mismatch || error"
    type="warning"
    style="margin-bottom: 12px"
  >
    <template #title>
      <span v-if="error">无法校验版本</span>
      <span v-else-if="serverVersion === null">后端未返回版本号</span>
      <span v-else>前后端版本不一致</span>
    </template>

    <template v-if="error">{{ error }}</template>
    <template v-else-if="serverVersion === null">
      后端可能为旧版本，未提供 schema_version。请重启后端服务后点击「重新检测」。
    </template>
    <template v-else>
      前端期望 schema 版本 <b>{{ EXPECTED_SCHEMA_VERSION }}</b>，后端为
      <b>{{ serverVersion }}</b>。这通常表示后端已更新但未重启，或前后端版本不同步。
      请重启后端服务后刷新页面。
    </template>

    <template #action>
      <a-button size="small" :loading="checking" @click="check">重新检测</a-button>
    </template>
  </a-alert>
</template>
