/** 采集源配置域：Cookie 导入/删除与墓碑表展开状态。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'

/** 源配置页签状态与操作，由 ScraperView 消费（状态存父级，切换页签后仍保持）。 */
export function useScraperConfig() {
  
  const showTombstone = ref(false)
  const showingCookieImport = ref(false)
  const cookiePlatform = ref('xiaohongshu')
  const cookieJsonInput = ref('')
  const deletingCookie = ref<string | null>(null)

  /** 导入 Cookie；成功返回 true，父级据此刷新全量数据 */
  async function importCookie(): Promise<boolean> {
    try {
      const r = await apiClient.post('/scraper/cookie-import', { platform: cookiePlatform.value, cookies: JSON.parse(cookieJsonInput.value) })
      Message.success(`Cookie 已导入${r.data.imported ? `（${r.data.imported} 条）` : ''}`)
      showingCookieImport.value = false
      cookieJsonInput.value = ''
      return true
    } catch (e) {
      Message.error('导入失败: ' + (getApiErrorMessage(e, 'JSON 格式错误')))
      return false
    }
  }

  /** 删除平台 Cookie；成功返回 true，父级据此刷新 Cookie 状态 */
  async function deleteCookie(platform: string): Promise<boolean> {
    try {
      deletingCookie.value = platform
      await apiClient.delete(`/scraper/cookie/${platform}`)
      Message.success('Cookie 已删除')
      return true
    } catch (e) {
      Message.error('删除失败: ' + (getApiErrorMessage(e, '')))
      return false
    } finally { deletingCookie.value = null }
  }

  return { showTombstone, showingCookieImport, cookiePlatform, cookieJsonInput, deletingCookie, importCookie, deleteCookie }
}
