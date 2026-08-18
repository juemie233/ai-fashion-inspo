/** 人物状态管理：博主/模特共用实现，按 kind 生成独立 store 实例。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { bloggersApi, modelsApi, type PersonForm } from '@/api/persons'
import type { Person } from '@shared/types/person'

/** 人物种类：blogger（穿搭博主）/ model（职业模特） */
export type PersonKind = 'blogger' | 'model'

/**
 * 按 kind 定义并获取独立 store 实例（Pinia 动态 id：`persons-blogger` / `persons-model`）。
 * 同一 kind 复用同一实例；博主与模特互不共享状态。
 */
export function usePersonsStore(kind: PersonKind) {
  /** 对应 API（博主 / 模特各自独立端点） */
  const api = kind === 'blogger' ? bloggersApi : modelsApi
  /** 人物列表 */
  const persons = ref<Person[]>([])
  /** 是否正在加载 */
  const loading = ref(false)
  /** 加载错误信息（非空时展示错误空态，避免被误读为「无数据」） */
  const error = ref<string | null>(null)
  /** 总数（分页用） */
  const total = ref(0)
  /** 当前页码 */
  const page = ref(1)
  /** 每页数量 */
  const size = ref(20)
  /** 当前搜索关键字 */
  const search = ref('')
  /** 平台筛选（'' 表示全部） */
  const platform = ref('')
  /** 排序方式：newest | name | count */
  const sort = ref<'newest' | 'name' | 'count'>('newest')

  /** 请求序号：筛选快速切换时丢弃过期响应，防止旧数据覆盖新列表 */
  let loadSeq = 0

  /** 加载人物列表（force=true 忽略已有数据强制刷新） */
  async function load(force: boolean = false) {
    if (!force && loading.value) return
    const seq = ++loadSeq
    loading.value = true
    error.value = null
    try {
      const data = await api.fetchList({
        page: page.value,
        size: size.value,
        search: search.value || undefined,
        platform: platform.value || undefined,
        sort: sort.value,
      })
      if (seq !== loadSeq) return // 已有更新的请求，丢弃过期响应
      persons.value = data.items
      total.value = data.total
    } catch (e) {
      if (seq !== loadSeq) return
      error.value = '加载人物列表失败，请检查服务后重试'
      console.error('加载人物列表失败', e)
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  /** 重置到第一页并重新加载（搜索/筛选变化时调用） */
  async function reload() {
    page.value = 1
    await load(true)
  }

  /** 翻页 */
  async function setPage(p: number) {
    page.value = p
    await load(true)
  }

  /** 创建/更新（表单提交统一入口） */
  async function save(id: number | null, body: PersonForm) {
    return id ? api.update(id, body) : api.create(body)
  }

  return defineStore(`persons-${kind}`, () => ({
    kind,
    persons,
    loading,
    error,
    total,
    page,
    size,
    search,
    platform,
    sort,
    load,
    reload,
    setPage,
    save,
  }))()
}

export type PersonsStore = ReturnType<typeof usePersonsStore>
