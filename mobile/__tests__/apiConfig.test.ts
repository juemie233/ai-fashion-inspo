/** 移动端 API 配置层（后端地址 / API Key / 连通性测试）的测试。
 *
 * 这部分是「装错之后整个 App 都用不了」的入口，原覆盖率约 70%：
 * - 用户手输地址常不带协议 → 必须自动补 `http://`，并去掉末尾斜杠；
 * - AsyncStorage 读写失败要静默降级（本次运行仍生效），不能让设置页崩；
 * - API Key 为空要清除存储而不是存空串；回显只用于「是否已设置」判断；
 * - 测试连接要区分「成功 / 带 Key 成功 / HTTP 非 200 / 鉴权失败 / 连不上」五种提示。
 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import axios from 'axios'

import {
  getApiBaseUrl,
  getApiKey,
  hasApiKey,
  loadApiBaseUrl,
  loadApiKey,
  saveApiBaseUrl,
  saveApiKey,
  testApiConnection,
} from '../services/api'

const URL_KEY = 'fashion-inspo:api-base-url'
const KEY_KEY = 'fashion-inspo:api-key'

beforeEach(async () => {
  await AsyncStorage.clear()
  jest.restoreAllMocks()
})

describe('后端地址', () => {
  it('保存时去空白/末尾斜杠，并自动补 http://', async () => {
    await saveApiBaseUrl('  192.168.31.142:18888/  ')
    expect(getApiBaseUrl()).toBe('http://192.168.31.142:18888')
    expect(await AsyncStorage.getItem(URL_KEY)).toBe('http://192.168.31.142:18888')

    await saveApiBaseUrl('https://example.com/')
    expect(getApiBaseUrl()).toBe('https://example.com')
  })

  it('保存空串回退默认地址（本机 IP:端口）', async () => {
    await saveApiBaseUrl('   ')
    expect(getApiBaseUrl()).toMatch(/^http:\/\//)
    expect(getApiBaseUrl()).not.toBe('')
  })

  it('持久化失败静默：本次运行仍然生效', async () => {
    jest.spyOn(AsyncStorage, 'setItem').mockRejectedValueOnce(new Error('disk full'))

    await expect(saveApiBaseUrl('10.0.0.2:18888')).resolves.toBeUndefined()
    expect(getApiBaseUrl()).toBe('http://10.0.0.2:18888')
  })

  it('启动时从存储恢复并规范化；无值/读取失败保持默认', async () => {
    await AsyncStorage.setItem(URL_KEY, '192.168.1.5:18888')
    expect(await loadApiBaseUrl()).toBe('http://192.168.1.5:18888')

    await AsyncStorage.removeItem(URL_KEY)
    await saveApiBaseUrl('') // 回到默认
    const fallback = await loadApiBaseUrl()
    expect(fallback).toMatch(/^http:\/\//)

    jest.spyOn(AsyncStorage, 'getItem').mockRejectedValueOnce(new Error('storage error'))
    await expect(loadApiBaseUrl()).resolves.toBe(fallback) // 静默使用默认
  })
})

describe('API Key', () => {
  it('保存非空 Key 会持久化；hasApiKey 反映当前状态', async () => {
    expect(hasApiKey()).toBe(false)

    await saveApiKey('  secret-key  ')
    expect(getApiKey()).toBe('secret-key') // trim
    expect(hasApiKey()).toBe(true)
    expect(await AsyncStorage.getItem(KEY_KEY)).toBe('secret-key')
  })

  it('保存空串 = 清除持久化', async () => {
    await saveApiKey('secret-key')
    await saveApiKey('   ')

    expect(getApiKey()).toBe('')
    expect(hasApiKey()).toBe(false)
    expect(await AsyncStorage.getItem(KEY_KEY)).toBeNull()
  })

  it('持久化失败静默；启动恢复 trim 空值视为未设置', async () => {
    jest.spyOn(AsyncStorage, 'setItem').mockRejectedValueOnce(new Error('disk full'))
    await expect(saveApiKey('k1')).resolves.toBeUndefined()
    expect(getApiKey()).toBe('k1') // 本次运行仍生效

    await AsyncStorage.setItem(KEY_KEY, '  restored  ')
    expect(await loadApiKey()).toBe('restored')

    await saveApiKey('') // 先清空内存
    await AsyncStorage.setItem(KEY_KEY, '   ') // 存储里只有空白
    await loadApiKey()
    expect(hasApiKey()).toBe(false) // 空白值视为未设置，不覆盖内存

    jest.spyOn(AsyncStorage, 'getItem').mockRejectedValueOnce(new Error('storage error'))
    await expect(loadApiKey()).resolves.toBe('') // 静默按未设置处理
  })
})

describe('testApiConnection', () => {
  it('成功返回；带 Key 时提示「已带 API Key」并附请求头', async () => {
    const spy = jest
      .spyOn(axios, 'get')
      .mockResolvedValue({ status: 200, data: { status: 'ok' } })

    const noKey = await testApiConnection('10.0.0.3:18888')
    expect(noKey).toEqual({ ok: true, message: '连接成功' })
    expect(spy).toHaveBeenLastCalledWith('http://10.0.0.3:18888/api/health', {
      timeout: 8000,
      headers: undefined,
    })

    const withKey = await testApiConnection('10.0.0.3:18888', '  k1 ')
    expect(withKey).toEqual({ ok: true, message: '连接成功（已带 API Key）' })
    expect(spy).toHaveBeenLastCalledWith('http://10.0.0.3:18888/api/health', {
      timeout: 8000,
      headers: { 'X-API-Key': 'k1' },
    })
  })

  it('HTTP 非 200 返回状态码提示', async () => {
    jest.spyOn(axios, 'get').mockResolvedValue({ status: 503, data: null })

    const result = await testApiConnection('example.com')

    expect(result).toEqual({ ok: false, message: '后端返回 HTTP 503' })
  })

  it('401/403 提示鉴权问题', async () => {
    jest.spyOn(axios, 'get').mockRejectedValue({ response: { status: 401 } })

    const result = await testApiConnection('example.com', 'bad-key')

    expect(result).toEqual({
      ok: false,
      message: 'API Key 无效或缺失（后端已开启鉴权）',
    })
  })

  it('网络错误提示检查地址与网络', async () => {
    jest.spyOn(axios, 'get').mockRejectedValue(new Error('Network Error'))

    const result = await testApiConnection('example.com')

    expect(result).toEqual({ ok: false, message: '无法连接，请检查地址与网络' })
  })
})
