/** 测试用的 api 模块取用器：给 `jest.mock('../services/api')` 之后的模块**完整类型**。
 *
 * 为什么需要：各测试文件原先都写
 * `const api = require('../services/api') as { apiClient: { get: jest.Mock } }`——
 * 手写的窄类型只能覆盖「当时用到的那几个函数」。一旦测试里 mock 了新函数却忘了同步
 * 这个类型，运行时正常、`tsc --noEmit` 直接报 TS2339（2026-10-28 CI 就是这样红的：
 * `video.test.tsx` 用了 `fetchSimilarInspirations` / `fetchTagGroups`，类型里没有）。
 *
 * 改用模块自身的类型后，`jest.mock` 里新增任何导出都不用再回来改类型。
 *
 * 注意 `jest.Mocked<T>` 只把**顶层函数**变成 mock：`apiClient` 是对象属性，它的
 * `get/post/...` 不会被转，所以这里单独把 apiClient 的四个方法标成 jest.Mock。
 */

import type * as ApiModule from '../services/api'

/** 被 mock 过的 axios 客户端（四个方法都是 jest.Mock）。 */
type MockedApiClient = {
  get: jest.Mock
  post: jest.Mock
  patch: jest.Mock
  delete: jest.Mock
}

/** 被 mock 过的 api 模块：顶层导出全是 mock，apiClient 的方法也是 mock。 */
export type MockedApi = Omit<jest.Mocked<typeof ApiModule>, 'apiClient'> & {
  apiClient: MockedApiClient
}

/** 取被 mock 过的 api 模块（含所有导出的 mock 方法）。 */
export function mockedApi(): MockedApi {
  // jest.mock 的工厂已在各测试文件里声明；这里只负责把运行时模块按完整类型取出来
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  return require('../services/api') as MockedApi
}
