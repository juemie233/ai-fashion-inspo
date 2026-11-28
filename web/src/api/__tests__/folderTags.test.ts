/**
 * 抖音收藏夹 → 穿搭大标签映射 API（`folderTags.ts`，2026-11-24 新功能）单测。
 *
 * 这是「收藏夹标签映射」面板的唯一接口层，0% 覆盖。重点钉住：
 * - 路径里的收藏夹名必须 `encodeURIComponent`（中文/空格/斜杠不能裸拼进 URL）；
 * - upsert / delete / apply / apply-all / tag-impact 的方法与路径一一对应；
 * - 返回值是后端 data 原样透传，错误原样抛出（由面板统一提示）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, delete: mocks.delete },
}))

import {
  applyAllFolderTags,
  applyFolderTagMapping,
  deleteFolderTagMapping,
  fetchFolderTagImpact,
  fetchFolderTagMappings,
  upsertFolderTagMapping,
} from '../folderTags'

const FOLDER = '我的 收藏/夹'

function mapping(over: Record<string, unknown> = {}) {
  return {
    folder_name: FOLDER,
    mode: 'tag',
    needs_confirm: false,
    tag_id: 3,
    tag_name: '法式',
    tag_category: 'outfit',
    local_collection_id: 9,
    local_item_count: 12,
    ...over,
  }
}

describe('folderTags API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue({ data: [] })
    mocks.post.mockResolvedValue({ data: {} })
    mocks.delete.mockResolvedValue({ data: {} })
  })

  it('获取映射列表：GET /admin/douyin-folder-tags', async () => {
    const list = [mapping()]
    mocks.get.mockResolvedValueOnce({ data: list })

    await expect(fetchFolderTagMappings()).resolves.toEqual(list)
    expect(mocks.get).toHaveBeenCalledWith('/admin/douyin-folder-tags')
  })

  it('新增/更新映射：POST body 原样透传可空字段', async () => {
    const payload = {
      folder_name: FOLDER,
      mode: 'tag' as const,
      tag_id: null,
      new_tag_name: '新标签',
      reuse_existing_category: true,
    }
    mocks.post.mockResolvedValueOnce({ data: mapping() })

    await expect(upsertFolderTagMapping(payload)).resolves.toEqual(mapping())
    expect(mocks.post).toHaveBeenCalledWith('/admin/douyin-folder-tags', payload)
  })

  it('删除映射：DELETE 路径中的收藏夹名必须 URL 编码', async () => {
    await deleteFolderTagMapping(FOLDER)

    expect(mocks.delete).toHaveBeenCalledWith(
      '/admin/douyin-folder-tags/%E6%88%91%E7%9A%84%20%E6%94%B6%E8%97%8F%2F%E5%A4%B9',
    )
  })

  it('应用单个映射：POST {encoded}/apply 并返回统计', async () => {
    const result = { applied: 7, folders: [{ folder_name: FOLDER }] }
    mocks.post.mockResolvedValueOnce({ data: result })

    await expect(applyFolderTagMapping(FOLDER)).resolves.toEqual(result)
    expect(mocks.post).toHaveBeenCalledWith(
      '/admin/douyin-folder-tags/%E6%88%91%E7%9A%84%20%E6%94%B6%E8%97%8F%2F%E5%A4%B9/apply',
    )
  })

  it('应用全部映射：POST /admin/douyin-folder-tags/apply-all', async () => {
    mocks.post.mockResolvedValueOnce({ data: { applied: 3, folders: [] } })

    await expect(applyAllFolderTags()).resolves.toEqual({ applied: 3, folders: [] })
    expect(mocks.post).toHaveBeenCalledWith('/admin/douyin-folder-tags/apply-all')
  })

  it('查询标签影响面：GET /admin/douyin-folder-tags/tag-impact/{tagId}', async () => {
    const impact = {
      tag_id: 3,
      name: '法式',
      category: 'outfit',
      usage_count: 20,
      mapped_folders: [FOLDER],
    }
    mocks.get.mockResolvedValueOnce({ data: impact })

    await expect(fetchFolderTagImpact(3)).resolves.toEqual(impact)
    expect(mocks.get).toHaveBeenCalledWith('/admin/douyin-folder-tags/tag-impact/3')
  })

  it('接口错误原样抛出（面板统一提示）', async () => {
    const err = new Error('500')
    mocks.post.mockRejectedValueOnce(err)

    await expect(upsertFolderTagMapping({ folder_name: FOLDER, mode: 'skip' })).rejects.toBe(err)
  })
})
