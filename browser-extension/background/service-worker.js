/** 后台 Service Worker：管理 API 通信、存储、右键菜单采集。 */

// 默认后端地址
const DEFAULT_API_URL = 'http://localhost:18888';

// 右键菜单 ID（固定，onInstalled 中重复创建时靠 lastError 静默忽略）
const CONTEXT_MENU_ID = 'fashion-inspo-capture-image';

/** 获取保存的设置 */
async function getSettings() {
  const result = await chrome.storage.local.get(['apiUrl', 'autoAnalyze', 'skipDuplicates']);
  return {
    apiUrl: result.apiUrl || DEFAULT_API_URL,
    autoAnalyze: result.autoAnalyze !== false,
    // 上传前查重：已入库图片默认跳过（可在 popup 设置面板关闭）
    skipDuplicates: result.skipDuplicates !== false,
  };
}

/** 从页面 URL 识别平台（与 content-scripts/extract-images.js 的 detectPlatform 保持一致；
 *  service worker 无法访问页面 DOM，只能从 URL 解析） */
function detectPlatformFromUrl(pageUrl) {
  if (/xiaohongshu\.com/.test(pageUrl)) return 'xiaohongshu';
  if (/douyin\.com/.test(pageUrl)) return 'douyin';
  return 'browser_extension';
}

/** 从页面 URL 提取平台笔记 ID（与 content-scripts/extract-images.js 的
 *  extractPlatformId 保持一致，兼容小红书 explore/discovery/note 与抖音 video/note 形态） */
function extractPlatformIdFromUrl(pageUrl) {
  const match =
    pageUrl.match(/\/(?:explore|discovery\/item|note)\/([a-zA-Z0-9]+)/) ||
    pageUrl.match(/\/video\/(\d+)/) ||
    pageUrl.match(/\/note\/(\d+)/);
  return match ? match[1] : null;
}

/** 上传图片到后端：服务端从图片 URL 直接下载入库。

   不在插件内 fetch 平台图片（小红书/抖音 CDN 域名不在扩展授权内，
   浏览器会拦截跨域请求导致全部失败），改由后端下载，规避 CORS 限制。

   settings 可选：批量上传时由调用方传入，避免每张图重复读取存储。
 */
async function uploadImage(imageUrl, metadata, taskId, settings) {
  if (!settings) settings = await getSettings();

  try {
    const response = await fetch(`${settings.apiUrl}/api/inspirations/from-url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: imageUrl,
        source_type: metadata.platform || 'browser_extension',
        source_url: metadata.sourceUrl || null,
        source_author: metadata.author || null,
        source_platform_id: metadata.platformId || null,
        // 关联采集任务记录：素材归属本次插件采集会话，可在采集管理页按任务查看
        scraper_task_id: taskId || null,
      }),
    });

    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error((data && data.detail) || `后端返回 HTTP ${response.status}`);
    }
    return { success: true, data };
  } catch (error) {
    // 记录失败原因（扩展 Service Worker 的控制台可见，便于排查上传失败）
    console.warn(`[穿搭采集器] 图片上传失败: ${imageUrl}`, error.message || error);
    return { success: false, error: error.message || String(error) };
  }
}

/** 上传成功后触发后端 AI 分析（autoAnalyze 开启时，仅图片素材）。

   后端上传链路不会自动触发 AI 分析，需显式调用 POST /api/ai/analyze/{id}
   （分析在服务端后台异步执行）。此处仅触发、不等待结果；
   触发失败不影响上传结果，只记日志。
 */
async function triggerAnalysis(inspiration, settings) {
  if (!settings.autoAnalyze) return false;
  if (!inspiration || !inspiration.id) return false;
  // 后端暂不支持视频分析（POST /api/ai/analyze/{id} 对非图片返回 400）
  if (inspiration.media_type && inspiration.media_type !== 'image') return false;
  try {
    const resp = await fetch(`${settings.apiUrl}/api/ai/analyze/${inspiration.id}`, {
      method: 'POST',
    });
    if (!resp.ok) {
      console.warn(
        `[穿搭采集器] 触发 AI 分析失败 (HTTP ${resp.status}): ${inspiration.id}`
      );
      return false;
    }
    return true;
  } catch (err) {
    console.warn('[穿搭采集器] 触发 AI 分析异常:', err);
    return false;
  }
}

/** 查询平台 ID 是否已入库（上传前查重，结果供 popup 标记「已采集」）。
 *  与后端入库查重语义一致：垃圾桶素材释放平台 ID，不视为已入库。 */
async function checkPlatformIdExists(platformId) {
  if (!platformId) return { exists: false, inspirationId: null };
  const settings = await getSettings();
  try {
    const url =
      `${settings.apiUrl}/api/inspirations/check-platform-id` +
      `?platform_id=${encodeURIComponent(platformId)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      return { exists: false, inspirationId: null, error: `后端返回 HTTP ${resp.status}` };
    }
    const data = await resp.json();
    return { exists: !!data.exists, inspirationId: data.inspiration_id || null };
  } catch (err) {
    console.warn('[穿搭采集器] 平台 ID 查重失败:', err);
    return { exists: false, inspirationId: null, error: err.message || String(err) };
  }
}

/** 为一次采集会话创建任务记录，返回 task id（失败返回 null，不影响上传主流程） */
async function createExtensionTask(metadata, settings) {
  if (!settings) settings = await getSettings();
  try {
    const resp = await fetch(`${settings.apiUrl}/api/scraper/extension-tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        platform: metadata.platform || 'browser_extension',
        source_url: metadata.sourceUrl || null,
      }),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.id || null;
  } catch (err) {
    // 任务创建失败不影响上传主流程，但记录日志便于排查
    console.warn('[穿搭采集器] 创建采集会话任务失败:', err);
    return null;
  }
}

/** 汇总采集会话结果并标记任务完成（上报失败不影响主流程） */
async function completeExtensionTask(taskId, found, added) {
  const settings = await getSettings();
  try {
    await fetch(`${settings.apiUrl}/api/scraper/extension-tasks/${taskId}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items_found: found, items_added: added }),
    });
  } catch {
    // 静默失败：任务记录保持 running，下次上传会创建新会话
  }
}

// ── 右键保存单张图片 ──

/** 确保右键菜单存在（幂等）：removeAll 后重建。
 *
 * 旧实现只在 onInstalled 注册且静默吞掉创建错误——一旦某次注册失败
 * （如重复 ID 冲突、profile 异常），菜单就永久消失且无任何提示，
 * 用户「找不到右键采集入口」。改为 Service Worker 每次唤醒都在顶层
 * 执行本函数：contextMenus 注册本身持久化，removeAll+create 幂等安全。
 */
async function ensureContextMenu() {
  await chrome.contextMenus.removeAll();
  chrome.contextMenus.create({
    id: CONTEXT_MENU_ID,
    title: '保存此图片到素材库',
    contexts: ['image'],
    documentUrlPatterns: ['http://*/*', 'https://*/*'],
  });
}

// Service Worker 每次唤醒都会执行顶层代码：无论是否触发过 onInstalled，
// 菜单一定被重建，右键入口始终可用
ensureContextMenu();

chrome.runtime.onInstalled.addListener(() => {
  ensureContextMenu();
});

chrome.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId !== CONTEXT_MENU_ID) return;
  handleContextCapture(info);
});

/** 右键保存单张图片：提取图片 URL、识别平台，走现有上传流程，
 *  结果用系统通知 + 工具栏角标提示（此路径没有 popup，需要独立反馈通道） */
async function handleContextCapture(info) {
  const imageUrl = info.srcUrl || '';
  const pageUrl = info.pageUrl || '';
  if (!imageUrl.startsWith('http')) {
    // data:/blob: 图片无法由服务端按 URL 下载，需明确提示而非笼统失败
    const hint = imageUrl.startsWith('blob:')
      ? '该图片是网页动态生成的（blob 地址），暂不支持保存'
      : '无法获取图片地址（图片可能未加载完成）';
    await notifyCaptureResult(false, '保存失败', hint);
    return;
  }

  const metadata = {
    platform: detectPlatformFromUrl(pageUrl),
    platformId: extractPlatformIdFromUrl(pageUrl),
    sourceUrl: pageUrl,
    author: '',
  };

  const settings = await getSettings();
  const taskId = await createExtensionTask(metadata, settings);
  const result = await uploadImage(imageUrl, metadata, taskId, settings);
  if (taskId) await completeExtensionTask(taskId, 1, result.success ? 1 : 0);

  if (result.success) {
    const analyzed = await triggerAnalysis(result.data, settings);
    await notifyCaptureResult(
      true,
      '保存成功',
      analyzed ? '图片已入库，AI 分析已启动' : '图片已保存到素材库',
    );
  } else {
    await notifyCaptureResult(false, '保存失败', result.error || '请确认后端已启动');
  }
}

/** 用系统通知 + 角标提示右键采集结果（角标 5 秒后自动清除） */
async function notifyCaptureResult(success, title, message) {
  try {
    await chrome.notifications.create({
      type: 'basic',
      iconUrl: chrome.runtime.getURL('icons/icon128.png'),
      title,
      message,
    });
  } catch (err) {
    console.warn('[穿搭采集器] 通知创建失败:', err);
  }
  try {
    await chrome.action.setBadgeBackgroundColor({ color: success ? '#16a34a' : '#dc2626' });
    await chrome.action.setBadgeText({ text: success ? '✓' : '!' });
    setTimeout(() => {
      chrome.action.setBadgeText({ text: '' }).catch(() => {});
    }, 5000);
  } catch {
    // 角标设置失败不影响通知
  }
}

/** 监听来自 popup 的消息 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'UPLOAD_IMAGES') {
    handleUploadImages(message.images, message.metadata, sendResponse);
    return true; // 异步响应
  }

  if (message.type === 'GET_SETTINGS') {
    getSettings().then(sendResponse);
    return true;
  }

  if (message.type === 'SAVE_SETTINGS') {
    chrome.storage.local.set(message.settings).then(() => {
      sendResponse({ success: true });
    });
    return true;
  }

  if (message.type === 'CHECK_API') {
    getSettings().then(async (settings) => {
      try {
        const resp = await fetch(`${settings.apiUrl}/api/health`);
        const data = await resp.json();
        sendResponse({ connected: true, data });
      } catch {
        sendResponse({ connected: false });
      }
    });
    return true;
  }

  if (message.type === 'CHECK_PLATFORM_ID') {
    checkPlatformIdExists(message.platformId).then(sendResponse);
    return true;
  }
});

/** 批量上传图片：先创建采集会话任务，逐张上传后汇总计数。
 *
 *  平台 ID（笔记 ID）在库内受部分唯一索引约束、只能对应一条素材，
 *  因此仅首图携带 source_platform_id，其余图片不传，
 *  避免同笔记多图批量采集时第 2 张起全部 409 冲突。
 */
async function handleUploadImages(images, metadata, sendResponse) {
  try {
    const settings = await getSettings();
    const taskId = await createExtensionTask(metadata, settings);
    const results = [];
    for (let i = 0; i < images.length; i += 1) {
      const imgMetadata = i === 0 ? metadata : { ...metadata, platformId: null };
      const result = await uploadImage(images[i], imgMetadata, taskId, settings);
      if (result.success) {
        // autoAnalyze 开启时对每个新素材触发 AI 分析（结果不影响上传状态）
        result.analyzed = await triggerAnalysis(result.data, settings);
      }
      results.push(result);
    }
    const successCount = results.filter((r) => r.success).length;
    if (taskId) await completeExtensionTask(taskId, images.length, successCount);
    // popup 可能在等待期间被关闭：sendResponse 抛错时静默（结果已无法送达）
    sendResponse({ results, taskId });
  } catch (err) {
    console.error('[穿搭采集器] 批量上传异常:', err);
    try {
      sendResponse({ results: [], taskId: null, error: String(err) });
    } catch {
      // popup 已关闭，无法送达响应，忽略
    }
  }
}
