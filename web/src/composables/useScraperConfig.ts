/** 采集源配置域：Cookie 导入与墓碑表展开状态。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'

/** 源配置页签状态与操作，由 ScraperView 消费（状态存父级，切换页签后仍保持）。 */
export function useScraperConfig() {
  const message = useMessage()

  const showTombstone = ref(false)
  const showingCookieImport = ref(false)
  const cookiePlatform = ref('xiaohongshu')
  const cookieJsonInput = ref('')

  /** 导入 Cookie；成功返回 true，父级据此刷新全量数据 */
  async function importCookie(): Promise<boolean> {
    try {
      await apiClient.post('/scraper/cookie-import', { platform: cookiePlatform.value, cookies: JSON.parse(cookieJsonInput.value) })
      message.success('Cookie 已导入')
      showingCookieImport.value = false
      cookieJsonInput.value = ''
      return true
    } catch (e: any) {
      message.error('导入失败: ' + (e.response?.data?.detail || 'JSON 格式错误'))
      return false
    }
  }

  return { showTombstone, showingCookieImport, cookiePlatform, cookieJsonInput, importCookie }
}
