/** 分享 Intent 处理：别的 App（小红书/抖音/相册）分享进来 → 直接入库。
 *
 * 链路：
 * - 分享**图片** → `uploadInspirationImage`（走已有的 multipart 上传）
 * - 分享**文本/链接** → 提取 URL → `importInspirationFromUrl`（只支持图片/视频直链；
 *   小红书/抖音的笔记页面链接会被后端拒绝，这里把原因原样提示给用户）
 * - 其它（纯文字、视频）→ 提示不支持，不做半成品
 *
 * 原生侧由 `expo-share-intent` 的 config plugin 提供（app.json 里配 intent-filter），
 * 这里只负责消费结果。处理完必须 `resetShareIntent()`，否则下次冷启动会重复入库。
 */

import { useEffect, useRef } from 'react'
import { Alert } from 'react-native'
import { useShareIntent } from 'expo-share-intent'
import { importInspirationFromUrl, uploadInspirationImage } from '../services/api'
import { useInspirationStore } from '../hooks/useInspirations'
import { extractUrl, looksLikeImageUrl } from '../utils/extractUrl'

/** 从 axios 错误里取后端 detail：页面链接/内容重复是业务 4xx，不能一律报成网络问题 */
function apiErrorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail.trim() : ''
}

export default function ShareIntentBridge() {
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntent()
  /** 防止同一次 Intent 被 effect 重跑处理两次（按对象身份去重，同一内容再次分享仍会处理） */
  const handledIntentRef = useRef<unknown>(null)

  useEffect(() => {
    if (!hasShareIntent) return
    if (handledIntentRef.current === shareIntent) return
    handledIntentRef.current = shareIntent

    const file = shareIntent?.files?.[0]
    const text = shareIntent?.text ?? ''
    const url = extractUrl(text)

    const handle = async () => {
      try {
        if (file?.path) {
          const item = await uploadInspirationImage(file.path)
          useInspirationStore.getState().prependItem(item)
          Alert.alert('已入库', '分享的图片已存进素材库，AI 将自动分析标签')
          return
        }

        if (url) {
          const item = await importInspirationFromUrl(url)
          useInspirationStore.getState().prependItem(item)
          Alert.alert(
            '已入库',
            looksLikeImageUrl(url) ? '分享的图片链接已保存到素材库' : '分享的链接已保存到素材库',
          )
          return
        }

        Alert.alert('暂不支持', '目前只能分享图片或包含链接的文本')
      } catch (error) {
        const detail = apiErrorMessage(error)
        if (detail) {
          // 后端明确拒绝（如链接是 HTML 页面）：给出可操作提示，不要误导成网络故障
          const hint = url && !looksLikeImageUrl(url) ? '。笔记链接暂不支持，请直接分享图片' : ''
          Alert.alert('入库失败', `${detail}${hint}`)
        } else {
          Alert.alert('入库失败', '请检查后端地址与网络后重试')
        }
      } finally {
        resetShareIntent()
      }
    }

    void handle()
  }, [hasShareIntent, shareIntent, resetShareIntent])

  return null
}
