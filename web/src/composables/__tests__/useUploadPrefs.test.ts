/**
 * 上传偏好（`useUploadPrefs`）的测试。
 *
 * 偏好全部落 localStorage，是「刷新后用户设置不能丢」这条承诺的唯一实现：
 * - 首次访问必须给出后端默认口径（自动分析开、停留在本页、跳过重复开）；
 * - 只有字符串 `"false"` 才算关闭——脏值（`"0"` / `"no"` / 任意文本）按开启处理，
 *   不能因为手动改过 localStorage 就变成「静默关掉自动分析」；
 * - `savePrefs` 三个键都要写成字符串，漏一个刷新后就丢一个设置。
 */

import { beforeEach, describe, expect, it } from 'vitest'

import { useUploadPrefs } from '../useUploadPrefs'

const KEY_AUTO = 'upload-auto-analyze'
const KEY_AFTER = 'upload-after'
const KEY_SKIP = 'upload-skip-duplicates'

describe('useUploadPrefs', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('无存储时使用默认值：自动分析开、上传后留本页、跳过重复开', () => {
    const prefs = useUploadPrefs()

    expect(prefs.autoAnalyze.value).toBe(true)
    expect(prefs.afterUpload.value).toBe('stay')
    expect(prefs.skipDuplicates.value).toBe(true)
  })

  it('读取已持久化的偏好（关闭项为字符串 "false"）', () => {
    localStorage.setItem(KEY_AUTO, 'false')
    localStorage.setItem(KEY_AFTER, 'detail')
    localStorage.setItem(KEY_SKIP, 'false')

    const prefs = useUploadPrefs()

    expect(prefs.autoAnalyze.value).toBe(false)
    expect(prefs.afterUpload.value).toBe('detail')
    expect(prefs.skipDuplicates.value).toBe(false)
  })

  it('只有严格等于 "false" 才算关闭，脏值一律按开启处理', () => {
    localStorage.setItem(KEY_AUTO, '0')
    localStorage.setItem(KEY_SKIP, 'no')

    const prefs = useUploadPrefs()

    // 避免「手改 localStorage 或旧版本写入非布尔值」导致自动分析被静默关闭
    expect(prefs.autoAnalyze.value).toBe(true)
    expect(prefs.skipDuplicates.value).toBe(true)

    localStorage.setItem(KEY_AUTO, 'true')
    const again = useUploadPrefs()
    expect(again.autoAnalyze.value).toBe(true)
  })

  it('savePrefs 把三个偏好全部写成字符串', () => {
    const prefs = useUploadPrefs()
    prefs.autoAnalyze.value = false
    prefs.afterUpload.value = 'models?tab=queue'
    prefs.skipDuplicates.value = false

    prefs.savePrefs()

    expect(localStorage.getItem(KEY_AUTO)).toBe('false')
    expect(localStorage.getItem(KEY_AFTER)).toBe('models?tab=queue')
    expect(localStorage.getItem(KEY_SKIP)).toBe('false')
  })

  it('保存后可被新实例读回（刷新页面场景）', () => {
    const prefs = useUploadPrefs()
    prefs.autoAnalyze.value = false
    prefs.afterUpload.value = 'detail'
    prefs.skipDuplicates.value = true
    prefs.savePrefs()

    const reloaded = useUploadPrefs()

    expect(reloaded.autoAnalyze.value).toBe(false)
    expect(reloaded.afterUpload.value).toBe('detail')
    expect(reloaded.skipDuplicates.value).toBe(true)
  })
})
