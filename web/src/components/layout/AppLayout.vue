<script setup lang="ts">
/** 应用整体布局：侧边导航 + 主内容区。
 *
 * 侧边栏在平板宽度（≤1024px，含 iPad 竖屏 768/810/820/834）收起为 64px 图标栏，
 * 把宽度让给素材网格；桌面端保持 200px 完整菜单。 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  IconImage,
  IconSearch,
  IconUpload,
  IconRobot,
  IconCamera,
  IconTags,
  IconScan,
  IconDesktop,
  IconSettings,
  IconList,
  IconUser,
  IconSafe,
  IconBarChart,
  IconBookmark,
} from '@arco-design/web-vue/es/icon'
import SchemaVersionBanner from './SchemaVersionBanner.vue'
import { useUiStore } from '@/stores/ui'
import { connectTaskWebSocket } from '@/composables/useWebSocket'

const route = useRoute()
const uiStore = useUiStore()
const { wsConnected } = storeToRefs(uiStore)

// 应用启动即建立全局 WebSocket 连接（任务进度推送；断线自动重连，
// 断开期间各页面自动降级为轮询）
connectTaskWebSocket()

// ── 平板适配：≤1024px 侧边栏收为图标栏 ──

/** 与 AppLayout 样式中的 @media (max-width: 1024px) 断点保持一致 */
const COMPACT_MEDIA_QUERY = '(max-width: 1024px)'

/** 侧边栏图标栏宽度，需与样式里的 64px 一致 */
const COMPACT_SIDEBAR_WIDTH = 64

/** 是否处于平板/窄屏宽度；初始值同步读取，避免首帧先渲染 200px 再收起 */
const isCompact = ref(
  typeof window !== 'undefined' && window.matchMedia(COMPACT_MEDIA_QUERY).matches,
)

let compactQuery: MediaQueryList | null = null

function handleCompactChange(event: MediaQueryListEvent) {
  isCompact.value = event.matches
}

onMounted(() => {
  compactQuery = window.matchMedia(COMPACT_MEDIA_QUERY)
  isCompact.value = compactQuery.matches
  compactQuery.addEventListener('change', handleCompactChange)
})

onUnmounted(() => {
  compactQuery?.removeEventListener('change', handleCompactChange)
})

/** 详情类路由映射回所属一级菜单，保持侧边栏高亮（如人物详情 → 人物管理） */
const menuKey = computed(() => {
  const name = route.name as string
  const mapping: Record<string, string> = {
    'person-detail': 'persons',
    detail: 'home',
  }
  return mapping[name] ?? name
})
</script>

<template>
  <div class="app-shell">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <a-menu
        :selected-keys="[menuKey]"
        auto-open-selected
        :collapsed="isCompact"
        :collapsed-width="COMPACT_SIDEBAR_WIDTH"
        @menu-item-click="(key: string) => $router.push({ name: key })"
      >
        <a-menu-item-group title="浏览">
          <a-menu-item key="home"><IconImage />素材库</a-menu-item>
          <a-menu-item key="upload"><IconUpload />上传素材</a-menu-item>
          <a-menu-item key="generate"><IconRobot />本地出图</a-menu-item>
          <a-menu-item key="model-photos"><IconCamera />添加模特照片</a-menu-item>
          <a-menu-item key="search"><IconSearch />高级搜索</a-menu-item>
          <a-menu-item key="collections"><IconBookmark />收藏合集</a-menu-item>
        </a-menu-item-group>
        <a-menu-item-group title="管理">
          <a-menu-item key="tags"><IconTags />标签管理</a-menu-item>
          <a-menu-item key="tags-advanced"><IconTags />标签高级管理</a-menu-item>
          <a-menu-item key="persons"><IconUser />人物管理</a-menu-item>
          <a-menu-item key="scraper"><IconScan />采集管理</a-menu-item>
          <a-menu-item key="models"><IconDesktop />AI 模型</a-menu-item>
          <a-menu-item key="admin"><IconSettings />素材管理</a-menu-item>
          <a-menu-item key="admin-governance"><IconSafe />数据治理</a-menu-item>
          <a-menu-item key="admin-insights"><IconBarChart />数据洞察</a-menu-item>
          <a-menu-item key="tasks"><IconList />任务管理</a-menu-item>
        </a-menu-item-group>
      </a-menu>

      <!-- 实时推送连接状态：WS 已连接=推送驱动，断开=自动降级轮询 -->
      <div class="ws-status" :class="wsConnected ? 'is-connected' : 'is-disconnected'">
        <span class="ws-dot" />
        <span class="ws-label">{{ wsConnected ? '实时推送已连接' : '轮询模式' }}</span>
      </div>
    </aside>

    <!-- 主内容区 -->
    <main class="main-content">
      <!-- 前后端 schema 版本握手提示 -->
      <SchemaVersionBanner />
      <slot />
    </main>
  </div>
</template>

<style scoped>
/* 外壳：flex 布局，占据全屏 */
.app-shell {
  display: flex;
  width: 100%;
  min-height: 100vh;
  /* iPad Safari 地址栏收放时 100vh 会溢出到屏幕外，动态视口高度更稳 */
  min-height: 100dvh;
  /* 全屏（添加到主屏幕）时避开 iPad 底部 Home 指示条 */
  padding-bottom: env(safe-area-inset-bottom);
  box-sizing: border-box;
}

/* 侧边栏：固定宽度 */
.sidebar {
  width: 200px;
  min-width: 200px;
  flex-shrink: 0;
  border-right: 1px solid #e5e7eb;
  background: #fff;
  display: flex;
  flex-direction: column;
}

/* 让菜单占据剩余空间，连接状态固定在侧边栏底部 */
.sidebar :deep(.arco-menu) {
  flex: 1;
  overflow-y: auto;
}

/* WebSocket 连接状态指示（侧边栏底部） */
.ws-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  font-size: 12px;
  color: #86909c;
  border-top: 1px solid #e5e7eb;
  white-space: nowrap;
}

.ws-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.ws-status.is-connected .ws-dot {
  background: #00b42a;
}

.ws-status.is-disconnected .ws-dot {
  background: #c9cdd4;
}

/* 主内容区：占据剩余空间，可垂直滚动 */
.main-content {
  flex: 1;
  min-width: 0;
  padding: 24px;
  overflow-y: auto;
  overflow-x: hidden;
  background: #f9fafb;
  box-sizing: border-box;
}

/* ── 平板适配（iPad 竖屏 768~834 / 横屏 1024）──
   断点与脚本里的 COMPACT_MEDIA_QUERY 一致：
   侧边栏收成 64px 图标栏，把 136px 宽度还给素材网格；内容区紧收内边距。 */
@media (max-width: 1024px) {
  .sidebar {
    width: 64px;
    min-width: 64px;
  }

  /* 图标栏模式下 Arco 仍会渲染分组标题（「浏览」「管理」），在 64px 里会溢出成
     竖排文字，直接隐藏；分组信息由图标顺序与选中态承担 */
  .sidebar :deep(.arco-menu-group-title) {
    display: none;
  }

  /* 图标栏放不下连接状态文案，只留状态圆点 */
  .ws-label {
    display: none;
  }

  .ws-status {
    justify-content: center;
    padding: 10px 0;
  }

  .main-content {
    padding: 16px;
  }
}
</style>

<style>
/* 触摸端（iPad / 手机）全局：去掉点击时整块变灰的高亮，点卡片不再闪灰色方块 */
@media (hover: none) {
  body {
    -webkit-tap-highlight-color: transparent;
  }
}
</style>
