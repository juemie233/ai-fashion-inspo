<script setup lang="ts">
/** 应用根组件：提供 Arco Design 全局配置（中文语言包）和布局壳。 */
import zhCN from '@arco-design/web-vue/es/locale/lang/zh-cn'
import AppLayout from '@/components/layout/AppLayout.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
</script>

<template>
  <a-config-provider :locale="zhCN">
    <AppLayout>
      <!-- 路由视图包在错误边界内：单页渲染错误降级为占位，不影响整体布局 -->
      <ErrorBoundary>
        <router-view />
      </ErrorBoundary>
    </AppLayout>
  </a-config-provider>
</template>
