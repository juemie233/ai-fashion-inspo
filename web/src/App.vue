<script setup lang="ts">
/** 应用根组件：提供 Arco Design 全局配置（中文语言包）和布局壳。 */
import zhCN from '@arco-design/web-vue/es/locale/lang/zh-cn'
import AppLayout from '@/components/layout/AppLayout.vue'
</script>

<template>
  <a-config-provider :locale="zhCN">
    <AppLayout>
      <router-view />
    </AppLayout>
  </a-config-provider>
</template>
