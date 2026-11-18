<script setup lang="ts">
/** 应用整体布局：左侧「索引脊」导航 + 右侧「印样台」内容区。
 *
 * 设计（印样台）：侧栏是档案索引（品牌印章 + 分组导航），内容区顶部有一行
 * running head（当前档案页 + 实时同步状态）；内容区仍是唯一滚动容器。
 * 侧边栏在平板宽度（≤1024px，含 iPad 竖屏 768/810/820/834）收起为 64px 图标栏。
 */

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

/** 是否处于平板/窄屏宽度；初始值同步读取，避免首帧先渲染宽侧栏再收起 */
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

/** 当前档案页标题（running head 左侧） */
const currentSection = computed(() => (route.meta.title as string) || '工作台')
</script>

<template>
  <div class="app-shell">
    <!-- 索引脊：品牌 + 导航 + 同步状态 -->
    <aside class="sidebar" aria-label="主导航">
      <div class="brand">
        <span class="brand-seal" aria-hidden="true">衣</span>
        <span class="brand-text">
          <strong>穿搭档案</strong>
          <small>本地工作台</small>
        </span>
      </div>

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
    </aside>

    <!-- 印样台：running head + 页面内容（唯一滚动容器） -->
    <main class="main-content">
      <div class="running-head">
        <span class="running-title">
          <span class="running-root">穿搭档案</span>
          <span class="running-slash" aria-hidden="true">/</span>
          <span class="running-section">{{ currentSection }}</span>
        </span>
        <span class="running-meta">
          <span class="running-status" :class="wsConnected ? 'is-connected' : 'is-disconnected'">
            <span class="ws-dot" />
            <span class="ws-label">{{ wsConnected ? '实时同步' : '轮询降级' }}</span>
          </span>
          <span class="running-local">本地 · 单机</span>
        </span>
      </div>

      <!-- 前后端 schema 版本握手提示 -->
      <SchemaVersionBanner />
      <slot />
    </main>
  </div>
</template>

<style scoped>
/* 外壳：固定视口高度，滚动交给 .main-content。
 * 不能只用 min-height：长页面会把外壳一起撑高，body 滚动、侧栏跟着滚走。 */
.app-shell {
  display: flex;
  width: 100%;
  height: 100vh;
  /* iPad Safari 地址栏收放时 100vh 会溢出到屏幕外，动态视口高度更稳 */
  height: 100dvh;
  overflow: hidden;
  background: var(--paper);
  /* 全屏（添加到主屏幕）时避开 iPad 四边安全区 */
  padding-top: env(safe-area-inset-top);
  padding-right: env(safe-area-inset-right);
  padding-bottom: env(safe-area-inset-bottom);
  padding-left: env(safe-area-inset-left);
  box-sizing: border-box;
}

/* ── 索引脊（侧栏） ── */
.sidebar {
  width: 216px;
  min-width: 216px;
  height: 100%;
  min-height: 0;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--paper);
  border-right: 1px solid var(--line);
  overflow: hidden;
}

/* 品牌印章：朱砂方章 + 「衣」字，是整站唯一的高饱和色块 */
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px 16px;
}

.brand-seal {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--seal);
  color: var(--sheet);
  font-size: 22px;
  font-weight: 600;
  line-height: 1;
  border-radius: var(--radius-sm);
}

.brand-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.brand-text strong {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.3;
}

.brand-text small {
  font-size: 11px;
  color: var(--ink-3);
  line-height: 1.3;
}

/* 导航：档案索引，不用彩色胶囊；选中项左侧朱砂书签线 */
.sidebar :deep(.arco-menu) {
  flex: 1;
  overflow-y: auto;
  background: transparent;
  border: 0;
  padding: 4px 8px 12px;
}

.sidebar :deep(.arco-menu-item) {
  height: 44px;
  min-width: 44px;
  line-height: 44px;
  margin: 1px 0;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  color: var(--ink-2);
  font-size: 14px;
}

.sidebar :deep(.arco-menu-item:hover) {
  background: rgba(28, 32, 30, 0.05);
  color: var(--ink);
}

.sidebar :deep(.arco-menu-item.arco-menu-selected) {
  background: var(--sheet);
  color: var(--ink);
  font-weight: 600;
  box-shadow: inset 2px 0 0 var(--seal);
}

.sidebar :deep(.arco-menu-icon) {
  color: currentColor;
}

.sidebar :deep(.arco-menu-group-title) {
  font-size: 11px;
  color: var(--ink-3);
  line-height: 2.2;
  padding: 8px 12px 2px;
  margin-top: 4px;
}

/* ── 主内容区：running head + 内容 ── */
.main-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  padding: 0 32px 32px;
  overflow-y: auto;
  overflow-x: hidden;
  overscroll-behavior: contain;
  background: var(--paper);
  box-sizing: border-box;
}

/* 页眉（running head）：吸顶显示当前档案页 + 同步状态，滚动时仍可见 */
.running-head {
  position: sticky;
  top: 0;
  z-index: 20;
  margin: 0 -32px 24px;
  padding: 0 32px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: var(--paper);
  border-bottom: 1px solid var(--line);
}

.running-title {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  font-size: var(--type-13);
  white-space: nowrap;
}

.running-root {
  color: var(--ink-3);
}

.running-slash {
  color: var(--line-strong);
}

.running-section {
  color: var(--ink);
  font-weight: 600;
}

.running-meta {
  display: inline-flex;
  align-items: center;
  gap: 16px;
  font-size: var(--type-12);
  color: var(--ink-3);
}

.running-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.running-local {
  padding-left: 16px;
  border-left: 1px solid var(--line);
}

.ws-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.running-status.is-connected .ws-dot {
  background: var(--jade);
}

.running-status.is-disconnected .ws-dot {
  background: var(--ink-3);
}

/* ── 平板 / 窄屏：索引脊收成 64px 图标栏 ──
   断点与脚本里的 COMPACT_MEDIA_QUERY 一致。 */
@media (max-width: 1024px) {
  .sidebar {
    width: 64px;
    min-width: 64px;
  }

  .brand {
    justify-content: center;
    padding: 14px 0 10px;
  }

  .brand-text {
    display: none;
  }

  .brand-seal {
    width: 36px;
    height: 36px;
    font-size: 20px;
  }

  /* 图标栏模式下 Arco 仍会渲染分组标题，在 64px 里会溢出成竖排文字，直接隐藏 */
  .sidebar :deep(.arco-menu-group-title) {
    display: none;
  }

  .main-content {
    padding: 0 16px 24px;
  }

  .running-head {
    margin: 0 -16px 16px;
    padding: 0 16px;
    gap: 8px;
  }

  /* 窄屏下状态文字省略，只保留状态点 */
  .running-status .ws-label,
  .running-local {
    display: none;
  }
}

/* 375 窄屏：running head 压到极简，内容边距再收一档 */
@media (max-width: 600px) {
  .main-content {
    padding: 0 12px 20px;
  }

  .running-head {
    margin: 0 -12px 12px;
    padding: 0 12px;
    height: 40px;
  }

  .running-root,
  .running-slash {
    display: none;
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
