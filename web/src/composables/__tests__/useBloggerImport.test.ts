/**
 * 博主 CSV 导入（`useBloggerImport`）的测试。
 *
 * 导入是「一次性写库」的批量操作，提示必须能区分：
 * - 全部成功 vs 部分失败（`failed > 0` 要 warning 并列出失败数）；
 * - 失败时把后端 detail 落进 `importError` 供页面展示，并回调 Arco 上传的 onError；
 * - 成功后必须 `store.reload()` 刷新列表并回调 onSuccess（否则页面数据是旧的）；
 * - 没拿到文件时只提示、不调接口。
 * `handleImportCsv` 是 Arco custom-request：同步返回 UploadRequest，异步逻辑走内部 IIFE。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RequestOption } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  importBloggersCsv: vi.fn(),
  usePersonsStore: vi.fn(),
  reload: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/persons', () => ({ importBloggersCsv: mocks.importBloggersCsv }))

vi.mock('@/stores/persons', () => ({ usePersonsStore: mocks.usePersonsStore }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useBloggerImport } from '../useBloggerImport'

async function flush() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function result(overrides: Record<string, unknown> = {}) {
  return { imported: 3, updated: 2, skipped: 0, failed: 0, errors: [], ...overrides }
}

function requestOptions(overrides: Record<string, unknown> = {}) {
  const options = {
    fileItem: { file: new File(['name,platform\n小美,xiaohongshu'], 'bloggers.csv') },
    onSuccess: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  }
  return options as unknown as RequestOption
}

describe('useBloggerImport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.usePersonsStore.mockReturnValue({ reload: mocks.reload })
    mocks.reload.mockResolvedValue(undefined)
    mocks.importBloggersCsv.mockResolvedValue(result())
  })

  it('按 kind 取对应 store 实例', () => {
    useBloggerImport('model')

    expect(mocks.usePersonsStore).toHaveBeenCalledWith('model')
  })

  it('同步返回 UploadRequest（空对象），异步完成导入', async () => {
    const importer = useBloggerImport('blogger')
    const options = requestOptions()

    const uploadRequest = importer.handleImportCsv(options)

    expect(uploadRequest).toEqual({})
    await flush()

    expect(mocks.importBloggersCsv).toHaveBeenCalledTimes(1)
    expect(mocks.importBloggersCsv.mock.calls[0][0]).toBeInstanceOf(File)
    expect(options.onSuccess).toHaveBeenCalledTimes(1)
    expect(options.onError).not.toHaveBeenCalled()
  })

  it('未获取到文件：提示且不调接口、不刷新、不回调', async () => {
    const importer = useBloggerImport('blogger')
    const options = requestOptions({ fileItem: { file: undefined } })

    importer.handleImportCsv(options)
    await flush()

    expect(mocks.message.error).toHaveBeenCalledWith('未获取到文件，请重新选择')
    expect(mocks.importBloggersCsv).not.toHaveBeenCalled()
    expect(mocks.reload).not.toHaveBeenCalled()
    expect(options.onSuccess).not.toHaveBeenCalled()
  })

  it('全部成功：回填结果、成功提示、刷新列表、回调 onSuccess', async () => {
    const importer = useBloggerImport('blogger')
    const options = requestOptions()

    importer.handleImportCsv(options)
    await flush()

    expect(importer.importResult.value).toEqual(result())
    expect(importer.importError.value).toBe('')
    expect(mocks.message.success).toHaveBeenCalledWith('导入成功：新增 3，更新 2')
    expect(mocks.reload).toHaveBeenCalledTimes(1)
    expect(options.onSuccess).toHaveBeenCalledTimes(1)
  })

  it('部分失败：warning 提示失败数（仍算导入完成并刷新）', async () => {
    mocks.importBloggersCsv.mockResolvedValue(result({ failed: 2 }))
    const importer = useBloggerImport('blogger')
    const options = requestOptions()

    importer.handleImportCsv(options)
    await flush()

    expect(mocks.message.warning).toHaveBeenCalledWith('导入完成：新增 3，更新 2，失败 2')
    expect(mocks.message.success).not.toHaveBeenCalled()
    expect(mocks.reload).toHaveBeenCalledTimes(1)
    expect(options.onSuccess).toHaveBeenCalledTimes(1)
  })

  it('后端失败：importError 落 detail、错误提示、回调 onError、不刷新列表', async () => {
    const err = { response: { data: { detail: 'CSV 缺少昵称列' } } }
    mocks.importBloggersCsv.mockRejectedValue(err)
    const importer = useBloggerImport('blogger')
    const options = requestOptions()

    importer.handleImportCsv(options)
    await flush()

    expect(importer.importError.value).toBe('CSV 缺少昵称列')
    expect(importer.importResult.value).toBeNull()
    expect(mocks.message.error).toHaveBeenCalledWith('CSV 缺少昵称列')
    expect(options.onError).toHaveBeenCalledWith(err)
    expect(options.onSuccess).not.toHaveBeenCalled()
    expect(mocks.reload).not.toHaveBeenCalled()
  })

  it('失败且无 detail：importError 回退「导入失败」', async () => {
    mocks.importBloggersCsv.mockRejectedValue(new Error('network'))
    const importer = useBloggerImport('blogger')
    const options = requestOptions()

    importer.handleImportCsv(options)
    await flush()

    expect(importer.importError.value).toBe('导入失败')
    expect(mocks.message.error).toHaveBeenCalledWith('导入失败')
  })

  it('再次导入会先清空上一次的结果与错误', async () => {
    const importer = useBloggerImport('blogger')
    importer.handleImportCsv(requestOptions())
    await flush()
    expect(importer.importResult.value).not.toBeNull()

    mocks.importBloggersCsv.mockRejectedValueOnce({ response: { data: { detail: '表头错误' } } })
    importer.handleImportCsv(requestOptions())
    await flush()

    expect(importer.importResult.value).toBeNull()
    expect(importer.importError.value).toBe('表头错误')
  })

  it('dismissImportResult：清空结果与错误', async () => {
    const importer = useBloggerImport('blogger')
    importer.handleImportCsv(requestOptions())
    await flush()

    importer.dismissImportResult()

    expect(importer.importResult.value).toBeNull()
    expect(importer.importError.value).toBe('')
  })
})
