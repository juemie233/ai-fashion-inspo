/** M2 新增 API 的契约测试：URL / 请求体 / 返回值兼容性。
 *
 * 这些用例的价值在于「钉住后端契约」：改了路径或字段名，测试会先红，
 * 而不是等到真机上点按钮才发现 404。
 */

import { apiClient } from '../services/api'
import {
  addTagsToInspiration,
  cancelTask,
  createTag,
  fetchSimilarInspirations,
  fetchTagGroups,
  fetchTasks,
  removeTagFromInspiration,
  restoreInspiration,
  trashInspiration,
  uploadInspirationImage,
} from '../services/api'

describe('标签相关', () => {
  it('addTagsToInspiration 打 POST /inspirations/{id}/tags 并带上 names/category/source', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { added: ['蕾丝'] } })
    const result = await addTagsToInspiration('i1', ['蕾丝'], 'free')

    expect(spy).toHaveBeenCalledWith('/inspirations/i1/tags', {
      names: ['蕾丝'],
      category: 'free',
      source: 'manual',
    })
    expect(result).toEqual({ added: ['蕾丝'] })
    spy.mockRestore()
  })

  it('removeTagFromInspiration 打 DELETE /inspirations/{id}/tags/{tagId}', async () => {
    const spy = jest.spyOn(apiClient, 'delete').mockResolvedValue({ data: {} })
    await removeTagFromInspiration('i1', 42)

    expect(spy).toHaveBeenCalledWith('/inspirations/i1/tags/42')
    spy.mockRestore()
  })

  it('fetchTagGroups 返回分组数组；后端异常返回时退化为空数组', async () => {
    // 注意：fetchTagGroups 现在有会话级缓存（4.3MB 的接口不能每次重拉），
    // 想拿到新结果必须 invalidateTagCache() 或传 force
    const { invalidateTagCache } = require('../services/api') as {
      invalidateTagCache: () => void
    }
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({ data: [{ category: 'style', tags: [] }] })

    invalidateTagCache()
    expect(await fetchTagGroups()).toHaveLength(1)

    spy.mockResolvedValue({ data: null })
    expect(await fetchTagGroups(true)).toEqual([])
    spy.mockRestore()
    invalidateTagCache()
  })

  it('createTag 打 POST /tags', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 1, name: '新标签' } })
    const tag = await createTag('新标签', 'free')

    expect(spy).toHaveBeenCalledWith('/tags', { name: '新标签', category: 'free' })
    expect(tag.name).toBe('新标签')
    spy.mockRestore()
  })
})

describe('垃圾桶', () => {
  it('trashInspiration 带原因时把 reason 放进请求体', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'i1' } })
    await trashInspiration('i1', '重复')

    expect(spy).toHaveBeenCalledWith('/inspirations/i1/trash', {
      reason: '重复',
      source: 'manual',
    })
    spy.mockRestore()
  })

  it('trashInspiration 不传原因时请求体只有 source', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'i1' } })
    await trashInspiration('i1')

    expect(spy).toHaveBeenCalledWith('/inspirations/i1/trash', { source: 'manual' })
    spy.mockRestore()
  })

  it('restoreInspiration 打 POST .../restore', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'i1' } })
    await restoreInspiration('i1')

    expect(spy).toHaveBeenCalledWith('/inspirations/i1/restore')
    spy.mockRestore()
  })
})

describe('相似推荐', () => {
  it('兼容后端返回 {items} 与 {results} 两种结构', async () => {
    const spy = jest.spyOn(apiClient, 'get')
    spy.mockResolvedValueOnce({ data: { items: [{ id: 'a' }, { id: 'b' }] } })
    expect(await fetchSimilarInspirations('i1')).toHaveLength(2)

    spy.mockResolvedValueOnce({ data: { results: [{ id: 'c' }] } })
    expect(await fetchSimilarInspirations('i1')).toHaveLength(1)

    spy.mockRestore()
  })

  it('返回结构异常时不抛错，退化为空数组', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({ data: {} })
    expect(await fetchSimilarInspirations('i1')).toEqual([])
    spy.mockRestore()
  })
})

describe('任务中心', () => {
  it('fetchTasks 带分页参数', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({
      data: { items: [{ id: 1 }], total: 1, page: 1, size: 20 },
    })
    await fetchTasks(2, 10)

    expect(spy).toHaveBeenCalledWith('/tasks', { params: { page: 2, size: 10 } })
    spy.mockRestore()
  })

  it('cancelTask 打 POST /tasks/{id}/cancel', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: {} })
    await cancelTask(7)

    expect(spy).toHaveBeenCalledWith('/tasks/7/cancel')
    spy.mockRestore()
  })
})

describe('多图上传', () => {
  it('uploadInspirationImage 以 multipart 打 POST /inspirations，并透传进度回调', async () => {
    const post = jest
      .spyOn(apiClient, 'post')
      .mockResolvedValue({ data: { id: 'new-1', file_path: 'images/a.jpg' } })

    const onProgress = jest.fn()
    const created = await uploadInspirationImage('file:///tmp/photo.png', onProgress)

    expect(post).toHaveBeenCalledTimes(1)
    const [url, _body, config] = post.mock.calls[0]
    expect(url).toBe('/inspirations')
    expect((config as { headers: Record<string, string> }).headers['Content-Type']).toBe(
      'multipart/form-data',
    )
    // 进度回调存在且能安全调用（RN 下由 XHR 触发，测试里手动模拟一次）
    expect(typeof (config as { onUploadProgress?: unknown }).onUploadProgress).toBe('function')
    ;(config as { onUploadProgress: (e: unknown) => void }).onUploadProgress({
      loaded: 50,
      total: 100,
    })
    expect(onProgress).toHaveBeenCalledWith(0.5)
    expect(created.id).toBe('new-1')

    post.mockRestore()
  })

  it('没有 total 时进度回调不报错（部分环境不提供总大小）', async () => {
    const post = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'x' } })
    const onProgress = jest.fn()
    await uploadInspirationImage('file:///tmp/a.jpg', onProgress)

    const config = post.mock.calls[0][2] as { onUploadProgress: (e: unknown) => void }
    expect(() => config.onUploadProgress({ loaded: 10 })).not.toThrow()
    expect(onProgress).not.toHaveBeenCalled()

    post.mockRestore()
  })
})
