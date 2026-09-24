/** 人脸库扫描页媒体地址工具的单元测试。
 *
 * 重点回归：`media_type` 缺失时不能把视频的 mp4 当 <img> 加载。
 * 背景：聚合分组明细接口曾漏回传 media_type，导致视频素材的悬停大图取到 mp4，
 * 浮层空白（用户反馈「悬停功能失效」）。此处兜底按后缀判视频，接口缺字段也不再破图。
 */

import { describe, expect, it } from 'vitest'
import { groupThumbUrl, isVideoItem, largePreviewUrl, thumbUrl } from '../faceMedia'
import type { DetectionItem } from '@/api/faceScan'

function item(over: Partial<DetectionItem> = {}): DetectionItem {
  return {
    detection_id: 1,
    inspiration_id: 'insp-1',
    confidence: 0.9,
    file_path: 'images/2026/09/a.jpg',
    thumbnail_path: 'thumbnails/2026/09/a.webp',
    media_type: 'image',
    ...over,
  }
}

describe('isVideoItem', () => {
  it('以 media_type 为准', () => {
    expect(isVideoItem(item({ media_type: 'video' }))).toBe(true)
    expect(isVideoItem(item({ media_type: 'image' }))).toBe(false)
  })

  it('media_type 缺失时按路径后缀兜底（接口漏字段的防线）', () => {
    expect(isVideoItem(item({ media_type: null, file_path: 'videos/a.mp4' }))).toBe(true)
    expect(isVideoItem(item({ media_type: undefined, file_path: 'videos/a.MOV' }))).toBe(true)
    expect(isVideoItem(item({ media_type: null, file_path: 'videos/a.webm' }))).toBe(true)
    expect(isVideoItem(item({ media_type: null, file_path: 'images/a.jpg' }))).toBe(false)
    expect(isVideoItem(item({ media_type: null, file_path: null as unknown as string }))).toBe(
      false,
    )
  })
})

describe('largePreviewUrl —— 悬停大图', () => {
  it('图片素材用原图', () => {
    expect(largePreviewUrl(item())).toBe('/api/files/images/2026/09/a.jpg')
  })

  it('视频素材（media_type=video）改走缩略图，绝不返回 mp4', () => {
    const url = largePreviewUrl(item({ media_type: 'video', file_path: 'videos/a.mp4' }))
    expect(url).toBe('/api/files/thumbnails/2026/09/a.webp')
    expect(url).not.toContain('.mp4')
  })

  it('media_type 缺失的视频素材也不返回 mp4（回归：悬停空白）', () => {
    const url = largePreviewUrl(
      item({ media_type: null, file_path: 'videos/a.mp4', thumbnail_path: 'thumbnails/a.webp' }),
    )
    expect(url).toBe('/api/files/thumbnails/a.webp')
    expect(url).not.toContain('.mp4')
  })

  it('视频素材没有缩略图时返回空串（宁可不出浮层也不破图）', () => {
    expect(
      largePreviewUrl(
        item({ media_type: 'video', file_path: 'videos/a.mp4', thumbnail_path: null }),
      ),
    ).toBe('')
  })
})

describe('thumbUrl —— 网格缩略图', () => {
  it('图片素材无缩略图时回退原图', () => {
    expect(thumbUrl(item({ thumbnail_path: null }))).toBe('/api/files/images/2026/09/a.jpg')
  })

  it('视频素材无缩略图时不回退到 mp4', () => {
    expect(
      thumbUrl(item({ media_type: 'video', file_path: 'videos/a.mp4', thumbnail_path: null })),
    ).toBe('')
    expect(
      thumbUrl(item({ media_type: null, file_path: 'videos/a.mp4', thumbnail_path: null })),
    ).toBe('')
  })
})

describe('groupThumbUrl —— 聚合分组代表图', () => {
  const group = {
    group_id: 0,
    size: 2,
    detection_ids: [1, 2],
    rep_detection_id: 1,
    rep_inspiration_id: 'insp-1',
    rep_file_path: 'videos/a.mp4',
    rep_thumbnail_path: 'thumbnails/a.webp',
    rep_media_type: null,
  }

  it('rep_media_type 缺失但路径是 mp4 时走缩略图', () => {
    expect(groupThumbUrl(group)).toBe('/api/files/thumbnails/a.webp')
  })

  it('代表图无缩略图且是视频时返回空串', () => {
    expect(groupThumbUrl({ ...group, rep_thumbnail_path: null })).toBe('')
  })
})
