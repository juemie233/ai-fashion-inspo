/** 后台 Service Worker：管理 API 通信和存储。 */

// 默认后端地址
const DEFAULT_API_URL = 'http://localhost:18888';

/** 获取保存的设置 */
async function getSettings() {
  const result = await chrome.storage.local.get(['apiUrl', 'autoAnalyze']);
  return {
    apiUrl: result.apiUrl || DEFAULT_API_URL,
    autoAnalyze: result.autoAnalyze !== false,
  };
}

/** 上传图片到后端：服务端从图片 URL 直接下载入库。

   不在插件内 fetch 平台图片（小红书/抖音 CDN 域名不在扩展授权内，
   浏览器会拦截跨域请求导致全部失败），改由后端下载，规避 CORS 限制。
 */
async function uploadImage(imageUrl, metadata, taskId) {
  const settings = await getSettings();

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
    return { success: false, error: error.message || String(error) };
  }
}

/** 为一次采集会话创建任务记录，返回 task id（失败返回 null，不影响上传主流程） */
async function createExtensionTask(metadata) {
  const settings = await getSettings();
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
  } catch {
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
});

/** 批量上传图片：先创建采集会话任务，逐张上传后汇总计数 */
async function handleUploadImages(images, metadata, sendResponse) {
  const taskId = await createExtensionTask(metadata);
  const results = [];
  for (const img of images) {
    const result = await uploadImage(img, metadata, taskId);
    results.push(result);
  }
  const successCount = results.filter((r) => r.success).length;
  if (taskId) await completeExtensionTask(taskId, images.length, successCount);
  sendResponse({ results, taskId });
}
