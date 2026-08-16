<script setup lang="ts">
/** 源配置页签：Cookie 状态与导入、可用采集源、墓碑表、平台提示。
 *  状态由父级持有（useScraperConfig），此处通过 props/emit 交互，保证切换页签后状态保持。 */

import { PLATFORM_LABELS } from '@/composables/useScraperTasks'
import type { ScraperSource, CookieStatus } from '@/types/scraper'

defineProps<{
  sources: ScraperSource[]
  tombstoneCount: number
  cookieStatuses: Record<string, CookieStatus>
  showTombstone: boolean
  showCookieImport: boolean
  cookiePlatform: string
  cookieJsonInput: string
  deletingCookie: string | null
}>()

const emit = defineEmits<{
  (e: 'update:showTombstone', v: boolean): void
  (e: 'update:showCookieImport', v: boolean): void
  (e: 'update:cookiePlatform', v: string): void
  (e: 'update:cookieJsonInput', v: string): void
  /** 点击导入：父级执行 Cookie 导入 */
  (e: 'import-cookie'): void
  /** 点击删除：父级执行 Cookie 删除 */
  (e: 'delete-cookie', platform: string): void
}>()

function openCookieImport(plat: string) {
  emit('update:cookiePlatform', plat)
  emit('update:showCookieImport', true)
}

function onUpdateCookieImportShow(v: boolean) {
  emit('update:showCookieImport', v)
}

function onUpdateCookieJson(v: string) {
  emit('update:cookieJsonInput', v)
}

/** Cookie 时效文案：未配置 / N 小时前导入 / 已过期 */
function cookieAgeHint(cs: CookieStatus): string {
  if (!cs.exists) return '尚未导入'
  if (cs.age_hours === undefined || cs.age_hours === null) return '已导入'
  if (!cs.valid) return `已过期（${cs.age_hours} 小时前导入）`
  if (cs.age_hours < 1) return '刚刚导入'
  return `${cs.age_hours} 小时前导入`
}
</script>

<template>
<!-- Cookie 状态 -->
<div class="cookie-cards">
  <n-card v-for="(cs, plat) in cookieStatuses" :key="plat" size="small" style="flex:1;min-width:260px">
    <template #header>
      {{ PLATFORM_LABELS[plat] || plat }} Cookie
      <n-tag :type="cs.valid?'success':'error'" size="small" style="margin-left:8px">{{ cs.exists?(cs.valid?'有效':'已过期'):'未配置' }}</n-tag>
    </template>
    <p style="font-size:12px;color:#666;margin:0">{{ cookieAgeHint(cs) }}</p>
    <p style="font-size:12px;color:#666;margin:4px 0 0">{{ cs.hint }}</p>
    <template #action>
      <n-space size="small">
        <n-button size="tiny" @click="openCookieImport(plat)">导入</n-button>
        <n-popconfirm v-if="cs.exists" @positive-click="emit('delete-cookie', plat)">
          <template #trigger><n-button size="tiny" type="error" ghost :loading="deletingCookie===plat">删除</n-button></template>
          确定删除 {{ PLATFORM_LABELS[plat]||plat }} 的 Cookie？
        </n-popconfirm>
      </n-space>
    </template>
  </n-card>
</div>

<!-- Cookie 导入对话框 -->
<n-modal :show="showCookieImport" @update:show="onUpdateCookieImportShow" preset="card" title="导入 Cookie" style="max-width:500px">
  <p style="font-size:12px;color:#666;margin-bottom:8px">粘贴 {{ PLATFORM_LABELS[cookiePlatform]||cookiePlatform }} 的 Cookie JSON 数据</p>
  <n-input :value="cookieJsonInput" @update:value="onUpdateCookieJson" type="textarea" :autosize="{minRows:4,maxRows:12}" placeholder='[{"name":"...","value":"...","domain":"..."}]' style="font-family:monospace;font-size:12px" />
  <n-button type="primary" block style="margin-top:12px" @click="emit('import-cookie')">导入</n-button>
</n-modal>

<!-- 采集源 -->
<n-card title="可用采集源" style="margin-bottom:16px" size="small">
  <n-list>
    <n-list-item v-for="src in sources" :key="src.platform">
      <template #prefix><n-tag :type="src.status==='available'?'success':'warning'" size="small">{{ src.status==='available'?'可用':'有限' }}</n-tag></template>
      <n-thing :title="src.name" :description="src.note">
        <template #header-extra><n-tag v-for="f in src.features" :key="f" size="tiny" :bordered="false">{{ f }}</n-tag></template>
      </n-thing>
    </n-list-item>
  </n-list>
</n-card>

<!-- 墓碑表 -->
<n-card size="small" style="margin-bottom:16px">
  <div class="tombstone-header" @click="emit('update:showTombstone', !showTombstone)" style="cursor:pointer;user-select:none">
    <span>{{ showTombstone?'▼':'▶' }} 已采集 URL 记录</span>
    <n-tag type="info" size="small">{{ tombstoneCount }} 个</n-tag>
  </div>
  <div v-if="showTombstone" class="tombstone-body">
    <p style="margin:8px 0 0;font-size:12px;color:#666">📌 墓碑表记录了所有曾下载过的图片 URL，采集时自动跳过，确保永不重复入库。当前共 <b>{{ tombstoneCount }}</b> 条。</p>
  </div>
</n-card>

<!-- 平台提示 -->
<div v-for="hint in sources.map(src=>{
  let level:'warning'|'info'|'error'='info',tips:string[]=[]
  if(src.platform==='xiaohongshu'){level='warning';tips=['需要有效的登录 Cookie','反爬检测严格','搜索功能依赖页面 DOM 结构']}
  else if(src.platform==='douyin'){level='error';tips=['网页版功能严重受限','搜索结果可能为空','推荐使用浏览器插件代替']}
  else{tips=['最可靠的采集方式','支持一键抓取当前页面']}
  return {...src,level,tips}
})" :key="hint.platform" style="margin-bottom:12px">
  <n-alert :type="hint.level" :title="hint.name+' — '+(hint.level==='error'?'⚠️ 可靠性低':hint.level==='warning'?'⚡ 需要配置':'✅ 推荐使用')">
    <ul style="margin:4px 0;padding-left:18px;font-size:13px"><li v-for="t in hint.tips" :key="t">{{ t }}</li></ul>
  </n-alert>
</div>
</template>

<style scoped>
.cookie-cards{display:flex;gap:12px;margin-bottom:16px}
.tombstone-header{display:flex;justify-content:space-between;align-items:center;font-size:14px}
.tombstone-body{border-top:1px solid #eee;margin-top:8px}
</style>
