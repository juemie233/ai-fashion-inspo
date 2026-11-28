/**
 * 人脸库媒体地址工具（utils/faceMedia）**补齐用例**：既有 faceMedia.test.ts 覆盖了
 * 视频/缺 media_type 的主要防破图分支，本文件补剩余导出与分支：
 *
 * - faceCropUrl / personAvatarUrl（有头像/无头像）；
 * - groupThumbUrl 的图片分支（缩略图优先、缺缩略图回退原图、两者都缺返回空串）；
 * - 视频后缀兜底扩展名（m4v/avi/mkv）与大小写；
 * - personAvatarUrl 的路径编码（中文/空格由 getFileUrl 逐段编码）。
 */

import { describe, expect, it } from 'vitest'
import { faceCropUrl, groupThumbUrl, isVideoItem, personAvatarUrl, thumbUrl } from '../faceMedia'
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

describe('人脸裁剪与人物头像地址', () => {
  it('faceCropUrl 按检测 id 拼后端裁剪路径', () => {
    expect(faceCropUrl(12)).toBe('/api/face-scan/detections/12/face')
  })

  it('personAvatarUrl：有头像返回编码后的文件 URL，无头像返回 undefined', () => {
    expect(personAvatarUrl({ avatar_path: 'avatars/女 模特.png' } as never)).toBe(
      `/api/files/avatars/${encodeURIComponent('女 模特.png')}`,
    )
    expect(personAvatarUrl({ avatar_path: null } as never)).toBeUndefined()
    expect(personAvatarUrl({} as never)).toBeUndefined()
  })
})

describe('聚合分组代表图', () => {
  const group = {
    group_id: 0,
    size: 2,
    detection_ids: [1, 2],
    rep_detection_id: 1,
    rep_inspiration_id: 'insp-1',
    rep_file_path: 'images/a.jpg',
    rep_thumbnail_path: 'thumbnails/a.webp',
    rep_media_type: 'image',
  }

  it('图片分组：缩略图优先', () => {
    expect(groupThumbUrl(group as never)).toBe('/api/files/thumbnails/a.webp')
  })

  it('图片分组缺缩略图：回退代表原图', () => {
    expect(groupThumbUrl({ ...group, rep_thumbnail_path: null } as never)).toBe(
      '/api/files/images/a.jpg',
    )
  })

  it('代表图路径与缩略图都缺：返回空串（不崩）', () => {
    expect(
      groupThumbUrl({
        ...group,
        rep_file_path: null,
        rep_thumbnail_path: null,
      } as never),
    ).toBe('')
  })
})

describe('视频后缀兜底扩展名', () => {
  it('media_type 缺失时 m4v/avi/mkv/大小写后缀都识别为视频', () => {
    for (const path of ['videos/a.m4v', 'videos/a.avi', 'videos/a.mkv', 'videos/a.MP4']) {
      expect(isVideoItem(item({ media_type: null, file_path: path as string }))).toBe(true)
    }
  })

  it('非视频后缀不会被误判（gif/apng 等仍按图片处理）', () => {
    expect(isVideoItem(item({ media_type: null, file_path: 'images/a.gif' }))).toBe(false)
    expect(isVideoItem(item({ media_type: null, file_path: 'images/a.webp' }))).toBe(false)
  })
})

describe('缩略图地址的图片分支', () => {
  it('图片素材有缩略图用缩略图；没有才回退原图', () => {
    expect(thumbUrl(item())).toBe('/api/files/thumbnails/2026/09/a.webp')
    expect(thumbUrl(item({ thumbnail_path: null }))).toBe('/api/files/images/2026/09/a.jpg')
  })
})
