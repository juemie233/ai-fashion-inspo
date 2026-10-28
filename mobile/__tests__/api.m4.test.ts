/** M4 API 契约：人物（博主/模特归一）、作品墙、标签改名。 */

import { apiClient } from '../services/api'
import { fetchPerson, fetchPersonInspirations, fetchPersons, renameTag } from '../services/api'

describe('人物列表', () => {
  it('博主：打 /bloggers，并把原始结构归一成 Person', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({
      data: {
        items: [
          {
            id: 3,
            name: '一朵芝士',
            platform: 'douyin',
            avatar_path: 'avatars/a.jpg',
            inspiration_count: 2022,
            follower_count: 12000,
            face_registered: true,
            last_inspiration_at: '2026-10-20T00:00:00Z',
          },
        ],
        total: 38,
      },
    })

    const page = await fetchPersons('blogger', 1, 20, '芝士')

    expect(spy).toHaveBeenCalledWith('/bloggers', {
      params: { page: 1, size: 20, sort: 'count', search: '芝士' },
    })
    expect(page.total).toBe(38)
    expect(page.items[0]).toMatchObject({
      id: 3,
      kind: 'blogger',
      name: '一朵芝士',
      inspiration_count: 2022,
      follower_count: 12000,
      face_registered: true,
    })
    spy.mockRestore()
  })

  it('模特：打 /models，缺字段时给安全默认值', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({
      data: { items: [{ id: 7 }], total: 1 },
    })

    const page = await fetchPersons('model')

    expect(spy).toHaveBeenCalledWith('/models', { params: { page: 1, size: 20, sort: 'count' } })
    expect(page.items[0]).toMatchObject({
      id: 7,
      kind: 'model',
      name: '未命名',
      platform: 'other',
      inspiration_count: 0,
      face_registered: false,
    })
    spy.mockRestore()
  })

  it('空搜索词不作为参数发送', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({ data: { items: [], total: 0 } })
    await fetchPersons('blogger', 2, 20, '   ')

    expect(spy).toHaveBeenCalledWith('/bloggers', { params: { page: 2, size: 20, sort: 'count' } })
    spy.mockRestore()
  })
})

describe('人物详情', () => {
  it('按 id 直接打 /bloggers|models/{id}，不再拉列表前 200 条', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({
      data: { id: 9, name: '小A', platform: 'douyin', inspiration_count: 3 },
    })

    const person = await fetchPerson('blogger', 9)
    expect(spy).toHaveBeenCalledWith('/bloggers/9')
    expect(person).toMatchObject({ id: 9, kind: 'blogger', name: '小A' })

    await fetchPerson('model', 4)
    expect(spy).toHaveBeenLastCalledWith('/models/4')
    spy.mockRestore()
  })
})

describe('人物作品墙', () => {
  it('博主作品打 /bloggers/{id}/inspirations', async () => {
    const spy = jest
      .spyOn(apiClient, 'get')
      .mockResolvedValue({ data: { items: [{ id: 'a' }], total: 5 } })

    const page = await fetchPersonInspirations('blogger', 3, 1, 30)

    expect(spy).toHaveBeenCalledWith('/bloggers/3/inspirations', {
      params: { page: 1, size: 30 },
    })
    expect(page.total).toBe(5)
    spy.mockRestore()
  })

  it('模特作品打 /models/{id}/inspirations', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({ data: {} })

    const page = await fetchPersonInspirations('model', 7, 2, 15)

    expect(spy).toHaveBeenCalledWith('/models/7/inspirations', { params: { page: 2, size: 15 } })
    expect(page.items).toEqual([])
    expect(page.total).toBe(0)
    spy.mockRestore()
  })
})

describe('标签库缓存（/api/tags 有 4.3MB / 8 秒，必须缓存）', () => {
  it('第二次调用不再请求，force=true 才刷新，invalidate 后重新请求', async () => {
    const mod = require('../services/api') as {
      fetchTagGroups: (force?: boolean) => Promise<unknown>
      invalidateTagCache: () => void
    }
    const spy = jest
      .spyOn(apiClient, 'get')
      .mockResolvedValue({ data: [{ category: 'style', tags: [] }] })

    mod.invalidateTagCache()
    await mod.fetchTagGroups()
    await mod.fetchTagGroups()
    expect(spy).toHaveBeenCalledTimes(1)

    await mod.fetchTagGroups(true)
    expect(spy).toHaveBeenCalledTimes(2)

    mod.invalidateTagCache()
    await mod.fetchTagGroups()
    expect(spy).toHaveBeenCalledTimes(3)

    spy.mockRestore()
    mod.invalidateTagCache()
  })
})

describe('标签改名', () => {
  it('打 PATCH /tags/{id}，只提交 name', async () => {
    const spy = jest.spyOn(apiClient, 'patch').mockResolvedValue({ data: { id: 5, name: '新名' } })

    const tag = await renameTag(5, '新名')

    expect(spy).toHaveBeenCalledWith('/tags/5', { name: '新名' })
    expect(tag.name).toBe('新名')
    spy.mockRestore()
  })

  it('改名失败时把错误抛给调用方处理', async () => {
    const spy = jest.spyOn(apiClient, 'patch').mockRejectedValue(new Error('duplicate'))

    await expect(renameTag(5, '重复名')).rejects.toThrow('duplicate')
    spy.mockRestore()
  })
})
