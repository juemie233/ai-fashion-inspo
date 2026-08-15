<script setup lang="ts">
/** 应用根组件：提供 Naive UI 全局配置和布局壳。 */
import { darkTheme, zhCN, dateZhCN } from 'naive-ui'
import AppLayout from '@/components/layout/AppLayout.vue'
</script>

<template>
  <n-config-provider :locale="zhCN" :date-locale="dateZhCN" :theme="null">
    <n-dialog-provider>
      <n-message-provider>
        <AppLayout>
          <router-view />
        </AppLayout>
      </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>
