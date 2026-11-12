/** 分享 Intent 处理：别的 App（小红书/抖音/相册）分享进来 → 直接入库。
 *
 * 链路：
 * - 分享**图片**（一张或多张）→ `uploadInspirationImage`（走已有的 multipart 上传）
 * - 分享**文本/链接** → 提取 URL（或 `webUrl`）→ `importInspirationFromUrl`
 *   （只支持图片/视频直链；小红书/抖音的笔记页面链接会被后端拒绝，这里把原因原样提示）
 * - 其它（纯文字、视频）→ 提示不支持，不做半成品
 *
 * P1 扩展：
 * - **多图**一次分享：按顺序逐张入库，最多 `MAX_SHARE_FILES` 张，最后给一条汇总
 *   （成功 N 张 / 已在库中 M 张 / 失败 K 张，附第一条失败原因），不再一张一个弹窗；
 * - **重复命中明确提示**：后端内容去重返回 409「该素材已存在（内容重复）」，
 *   统计成「已在库中」而不是伪装成失败；
 * - **链接标题提取**：把分享文案里的标题带进成功提示，用户能确认入库的是哪一条。
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
import {
  buildShareSummary,
  describeShareError,
  extractShareTitle,
  isDuplicateError,
  MAX_SHARE_FILES,
  type ShareOutcome,
} from '../utils/shareIntent'

export default function ShareIntentBridge() {
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntent()
  /** 防止同一次 Intent 被 effect 重跑处理两次（按对象身份去重，同一内容再次分享仍会处理） */
  const handledIntentRef = useRef<unknown>(null)

  useEffect(() => {
    if (!hasShareIntent) return
    if (handledIntentRef.current === shareIntent) return
    handledIntentRef.current = shareIntent

    /** 分享的图片文件（过滤掉没有本地路径的脏数据） */
    const files = (Array.isArray(shareIntent?.files) ? shareIntent.files : [])
      .map((file) => file?.path)
      .filter((path): path is string => typeof path === 'string' && path.length > 0)
    const text = shareIntent?.text ?? ''
    // Android 的 ACTION_SEND 只给 text；iOS 可能给 webUrl。两者都认
    const url = extractUrl(text) ?? shareIntent?.webUrl ?? null
    const title = extractShareTitle(text, url)

    /** 逐张上传，统计三类结果 */
    const uploadFiles = async (): Promise<ShareOutcome> => {
      const targets = files.slice(0, MAX_SHARE_FILES)
      const outcome: ShareOutcome = {
        created: 0,
        duplicate: 0,
        failed: 0,
        skipped: files.length - targets.length,
      }
      for (const path of targets) {
        try {
          const item = await uploadInspirationImage(path)
          useInspirationStore.getState().prependItem(item)
          outcome.created += 1
        } catch (error) {
          if (isDuplicateError(error)) {
            outcome.duplicate += 1
            continue
          }
          outcome.failed += 1
          if (!outcome.firstError) outcome.firstError = describeShareError(error)
        }
      }
      return outcome
    }

    /** 导入一个链接 */
    const importUrl = async (target: string): Promise<ShareOutcome> => {
      try {
        const item = await importInspirationFromUrl(target)
        useInspirationStore.getState().prependItem(item)
        return { created: 1, duplicate: 0, failed: 0 }
      } catch (error) {
        if (isDuplicateError(error)) return { created: 0, duplicate: 1, failed: 0 }
        return { created: 0, duplicate: 0, failed: 1, firstError: describeShareError(error) }
      }
    }

    const handle = async () => {
      try {
        if (files.length > 0) {
          const outcome = await uploadFiles()
          const summary = buildShareSummary('image', outcome)
          Alert.alert(summary.title, summary.message)
          return
        }

        if (url) {
          const outcome = await importUrl(url)
          const summary = buildShareSummary('url', outcome)
          // 成功时把标题/链接类型说清楚（笔记页链接会被后端拒绝，提示里已带原因）
          if (outcome.created > 0) {
            const what = looksLikeImageUrl(url) ? '图片链接' : '链接'
            const subject = title || url
            Alert.alert('已导入', `${subject}（${what}）已保存到素材库`)
            return
          }
          Alert.alert(summary.title, summary.message)
          return
        }

        Alert.alert('暂不支持', '目前只能分享图片或包含链接的文本')
      } catch (error) {
        // 兜底：任何未预期的异常都不能让分享流程卡住（否则下次冷启动重复处理）
        Alert.alert('入库失败', describeShareError(error))
      } finally {
        resetShareIntent()
      }
    }

    void handle()
  }, [hasShareIntent, shareIntent, resetShareIntent])

  return null
}
