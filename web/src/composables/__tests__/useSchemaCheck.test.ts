/**
 * 前后端 schema 握手（`useSchemaCheck`）的测试。
 *
 * 版本不一致会让前端按旧契约解析新后端（字段缺失/含义变化），因此这里的口径必须明确：
 * - 后端版本与期望一致 → 不提示；
 * - 不一致、或旧后端根本没返回 `schema_version` → 都算不一致（不能静默降级）；
 * - 构建时算不出期望版本（空串）→ 跳过校验，避免全部用户看到误报；
 * - 后端不可达 → 给出固定可操作文案，`mismatch` 保持 false（不是版本问题）。
 *
 * `EXPECTED_SCHEMA_VERSION` 由 vite 在构建期注入；测试里用 getter 动态 mock，
 * 以便在同一个文件里切换「有期望版本 / 空期望版本」两种构建结果。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  state: { expected: 'schema-2026-11-19' },
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get } }))

vi.mock('@/utils/schemaVersion', () => ({
  get EXPECTED_SCHEMA_VERSION() {
    return mocks.state.expected
  },
}))

import { useSchemaCheck } from '../useSchemaCheck'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('useSchemaCheck', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.state.expected = 'schema-2026-11-19'
  })

  it('版本一致：serverVersion 回填、mismatch=false、error=null', async () => {
    mocks.get.mockResolvedValue({ data: { schema_version: 'schema-2026-11-19' } })
    const check = useSchemaCheck()

    await check.check()

    expect(mocks.get).toHaveBeenCalledWith('/health')
    expect(check.serverVersion.value).toBe('schema-2026-11-19')
    expect(check.mismatch.value).toBe(false)
    expect(check.error.value).toBeNull()
    expect(check.checking.value).toBe(false)
  })

  it('版本不一致：mismatch=true，并保留后端实际版本供提示', async () => {
    mocks.get.mockResolvedValue({ data: { schema_version: 'schema-2026-10-01' } })
    const check = useSchemaCheck()

    await check.check()

    expect(check.mismatch.value).toBe(true)
    expect(check.serverVersion.value).toBe('schema-2026-10-01')
    expect(check.error.value).toBeNull()
  })

  it('旧后端未返回 schema_version 时视为不一致（不能静默降级）', async () => {
    mocks.get.mockResolvedValue({ data: {} })
    const check = useSchemaCheck()

    await check.check()

    expect(check.serverVersion.value).toBeNull()
    expect(check.mismatch.value).toBe(true)
  })

  it('构建时算不出期望版本（空串）时跳过校验，不误报', async () => {
    mocks.state.expected = ''
    mocks.get.mockResolvedValue({ data: {} })
    const check = useSchemaCheck()

    await check.check()

    expect(check.mismatch.value).toBe(false)
    expect(check.serverVersion.value).toBeNull()
  })

  it('请求失败：清零版本、mismatch=false，并给出可操作错误文案', async () => {
    mocks.get.mockRejectedValue(new Error('connect ECONNREFUSED'))
    const check = useSchemaCheck()

    await check.check()

    expect(check.error.value).toBe('无法连接后端服务，未能校验版本一致性')
    expect(check.serverVersion.value).toBeNull()
    expect(check.mismatch.value).toBe(false)
    expect(check.checking.value).toBe(false)
  })

  it('checking 在请求在途时为 true，结束后复位', async () => {
    const pending = deferred<{ data: { schema_version: string } }>()
    mocks.get.mockReturnValue(pending.promise)
    const check = useSchemaCheck()

    const promise = check.check()
    expect(check.checking.value).toBe(true)

    pending.resolve({ data: { schema_version: 'schema-2026-11-19' } })
    await promise

    expect(check.checking.value).toBe(false)
  })

  it('重试成功会清掉上一次的失败文案', async () => {
    mocks.get.mockRejectedValueOnce(new Error('boom'))
    mocks.get.mockResolvedValueOnce({ data: { schema_version: 'schema-2026-11-19' } })
    const check = useSchemaCheck()

    await check.check()
    expect(check.error.value).not.toBeNull()

    await check.check()

    expect(check.error.value).toBeNull()
    expect(check.mismatch.value).toBe(false)
    expect(check.serverVersion.value).toBe('schema-2026-11-19')
  })
})
