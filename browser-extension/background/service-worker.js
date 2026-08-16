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

/** 上传图片到后端 */
async function uploadImage(imageUrl, metadata, taskId) {
  const settings = await getSettings();

  try {
    const response = await fetch(imageUrl);
    if (!response.ok) throw new Error(`下载图片失败: ${response.status}`);
    const blob = await response.blob();

    const formData = new FormData();
    formData.append('file', blob, metadata.filename || 'image.jpg');
    formData.append('source_type', metadata.platform || 'browser_extension');
    if (metadata.sourceUrl) formData.append('source_url', metadata.sourceUrl);
    if (metadata.author) formData.append('source_author', metadata.author);
    if (metadata.platformId) formData.append('source_platform_id', metadata.platformId);
    // 关联采集任务记录：素材归属本次插件采集会话，可在采集管理页按任务查看
    if (taskId) formData.append('scraper_task_id', taskId);

    const apiUrl = settings.apiUrl;
    const result = await fetch(`${apiUrl}/api/inspirations`, {
      method: 'POST',
      body: formData,
    });

    if (!result.ok) {
      const err = await result.json();
      throw new Error(err.detail || '上传失败');
    }

    const data = await result.json();
    return { success: true, data };
  } catch (error) {
    return { success: false, error: error.message };
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
