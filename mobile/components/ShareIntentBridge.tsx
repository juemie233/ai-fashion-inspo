/** 分享 Intent 处理：别的 App（小红书/抖音/相册）分享进来 → 直接入库。
 *
 * 链路：
 * - 分享**图片** → `uploadInspirationImage`（走已有的 multipart 上传）
 * - 分享**文本/链接** → 提取 URL → `importInspirationFromUrl`（后端下载并入库）
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

export default function ShareIntentBridge() {
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntent()
  /** 防止同一份分享被处理两次（effect 可能重跑） */
  const handledRef = useRef<string | null>(null)

  useEffect(() => {
    if (!hasShareIntent) return

    const file = shareIntent?.files?.[0]
    const text = shareIntent?.text ?? ''
    const url = extractUrl(text)
    // 同一份内容只处理一次
    const key = `${file?.path ?? ''}|${url ?? text}`
    if (handledRef.current === key) return
    handledRef.current = key

    const handle = async () => {
      try {
        if (file?.path) {
          const item = await uploadInspirationImage(file.path)
          useInspirationStore.getState().prependItem(item)
          Alert.alert('已入库', '分享的图片已存进素材库，AI 将自动分析标签')
          return
        }

        if (url && !looksLikeImageUrl(url)) {
          const item = await importInspirationFromUrl(url)
          useInspirationStore.getState().prependItem(item)
          Alert.alert('已入库', '分享的链接已保存到素材库')
          return
        }

        if (url) {
          const item = await importInspirationFromUrl(url)
          useInspirationStore.getState().prependItem(item)
          Alert.alert('已入库', '分享的图片链接已保存到素材库')
          return
        }

        Alert.alert('暂不支持', '目前只能分享图片或包含链接的文本')
      } catch {
        Alert.alert('入库失败', '请检查后端地址与网络后重试')
      } finally {
        resetShareIntent()
      }
    }

    void handle()
  }, [hasShareIntent, shareIntent, resetShareIntent])

  return null
}
