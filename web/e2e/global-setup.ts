/**
 * Playwright 全局预检：确认后端在跑，否则给出可执行的启动指引。
 *
 * 为什么需要：e2e 用例全是读操作，但**后端不由 Playwright 启动**（Playwright 只会
 * 起前端 dev server）。后端没起时，症状是一堆「接口超时 / 表格空」的用例失败，
 * 看起来像选择器坏了，实际是环境没准备好——排障方向从一开始就错了。
 *
 * 这里只在开跑前做一次健康检查，失败时抛出带命令的错误信息。
 *
 * 后端地址优先级：
 *   1. 环境变量 `E2E_API_BASE`（临时指向别的环境时用）
 *   2. `web/.env` 的 `VITE_BACKEND_URL`（与 vite proxy 同源，避免两处配置不一致）
 *   3. 默认 `http://localhost:18888`
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/** 从 web/.env 读 VITE_BACKEND_URL（读不到就用默认端口）。 */
function resolveBackendBase(): string {
  if (process.env.E2E_API_BASE) return process.env.E2E_API_BASE.replace(/\/$/, '')
  try {
    const envPath = resolve(process.cwd(), '.env')
    const text = readFileSync(envPath, 'utf-8')
    const line = text
      .split('\n')
      .map((l) => l.trim())
      .find((l) => l.startsWith('VITE_BACKEND_URL='))
    if (line) {
      const value = line
        .slice('VITE_BACKEND_URL='.length)
        .trim()
        .replace(/^["']|["']$/g, '')
      if (value) return value.replace(/\/$/, '')
    }
  } catch {
    // 没有 .env 时走默认值
  }
  return 'http://localhost:18888'
}

export default async function globalSetup(): Promise<void> {
  const backend = resolveBackendBase()
  const url = `${backend}/api/health`
  const hint = [
    '',
    `[e2e] 后端未就绪：${url} 连接失败或返回非 200。`,
    'e2e 用例都是读操作，但需要后端在跑；Playwright 只会自动起前端 dev server。',
    '请先在另一个终端启动后端：',
    '',
    '  cd backend && py -3.12 -m uvicorn app.main:app --host 127.0.0.1 --port 18888',
    '',
    '若后端端口不同：改 web/.env 的 VITE_BACKEND_URL，或临时设 E2E_API_BASE=<后端地址>。',
    '（前端 dev server 会由 Playwright 按 playwright.config.ts 的 webServer 自动拉起）',
    '',
  ].join('\n')

  let res: Response
  try {
    res = await fetch(url, { signal: AbortSignal.timeout(5000) })
  } catch (e) {
    throw new Error(`${hint}\n底层错误：${String(e)}`)
  }
  if (!res.ok) throw new Error(`${hint}\nHTTP ${res.status}`)
}
