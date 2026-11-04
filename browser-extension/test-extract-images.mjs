/**
 * 浏览器插件图片提取纯函数测试（2026-11-04 测试体系评测 P1-4）。
 *
 * 插件是 MV3 content script（IIFE，无模块导出），这里用 node:vm 执行真实源码，
 * 在返回前把内部纯函数挂到 globalThis；不复制实现，改坏源码测试会跟着红。
 *
 * 运行：node --test browser-extension/test-extract-images.mjs
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const sourcePath = path.join(__dirname, 'content-scripts/extract-images.js')
let source = fs.readFileSync(sourcePath, 'utf8')

// 在 IIFE 返回前暴露内部函数（只改测试加载副本，不改源码）
const marker = '  // 返回值供 chrome.scripting.executeScript'
if (!source.includes(marker)) throw new Error('extract-images.js 结构变化：找不到注入点')
source = source.replace(
  marker,
  `  globalThis.__extractFns = { pickLargestSrcset, normalizeUrl, extractPlatformId };\n\n${marker}`,
)

const context = {
  window: {
    location: { hostname: 'www.xiaohongshu.com', href: 'https://www.xiaohongshu.com/explore/abc123' },
    __fashionInspoData: null,
  },
  document: {
    querySelectorAll: () => [],
    querySelector: () => null,
  },
  console: { log: () => {} },
  Date,
}
context.globalThis = context
vm.createContext(context)
vm.runInContext(source, context, { filename: sourcePath })

const fns = context.__extractFns

test('pickLargestSrcset 选择分辨率最高的 URL', () => {
  const srcset = 'https://a.example/1.jpg 320w, https://a.example/2.jpg 1080w, https://a.example/3.jpg 640w'
  assert.equal(fns.pickLargestSrcset(srcset), 'https://a.example/2.jpg')
})

test('pickLargestSrcset 忽略非 http/空值', () => {
  assert.equal(fns.pickLargestSrcset(''), null)
  assert.equal(fns.pickLargestSrcset('data:image/png;base64,xx 100w'), null)
})

test('normalizeUrl 去掉尺寸后缀与 query，用于跨尺寸去重', () => {
  assert.equal(
    fns.normalizeUrl('https://sns-img.example/a_1080x1440.jpg?imageView2/2/w/1080'),
    'https://sns-img.example/a.jpg',
  )
})

test('extractPlatformId 识别小红书笔记与抖音作品', () => {
  assert.equal(fns.extractPlatformId(), 'abc123')

  context.window.location.hostname = 'www.douyin.com'
  context.window.location.href = 'https://www.douyin.com/video/7654321098765432100'
  assert.equal(fns.extractPlatformId(), '7654321098765432100')

  context.window.location.href = 'https://www.douyin.com/note/7654321098765432101'
  assert.equal(fns.extractPlatformId(), '7654321098765432101')
})
