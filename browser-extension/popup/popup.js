/** Popup 主逻辑：图片选择、上传管理、设置面板。 */

// DOM 元素
const imageGrid = document.getElementById('imageGrid');
const selectedCountEl = document.getElementById('selectedCount');
const totalCountEl = document.getElementById('totalCount');
const uploadBtn = document.getElementById('uploadBtn');
const selectAllBtn = document.getElementById('selectAllBtn');
const statusBar = document.getElementById('statusBar');
const statusText = document.getElementById('statusText');
const settingsBtn = document.getElementById('settingsBtn');
const settingsPanel = document.getElementById('settingsPanel');
const saveSettingsBtn = document.getElementById('saveSettingsBtn');
const closeSettingsBtn = document.getElementById('closeSettingsBtn');
const uploadOverlay = document.getElementById('uploadOverlay');
const uploadProgressText = document.getElementById('uploadProgressText');

// 状态
let imageList = [];
let selectedUrls = new Set();
let metadata = {};
// 上传前查重：已入库图片默认跳过（与 service-worker 中的设置同步）
let skipDuplicates = true;

// 初始化
document.addEventListener('DOMContentLoaded', () => {
  checkConnection();
  loadImages();
  setupEventListeners();
  loadSettings();
});

/** 检查后端连接状态 */
async function checkConnection() {
  try {
    const response = await chrome.runtime.sendMessage({ type: 'CHECK_API' });
    // 防御：response.data 可能为 null / 缺 version 字段（后端 health 响应结构变化时
    // 不能抛 TypeError，否则 popup 打开即异常）
    const version = (response && response.data && response.data.version) || '';
    if (response && response.connected) {
      statusBar.className = 'status-bar connected';
      statusText.textContent = version ? `已连接 (v${version})` : '已连接';
    } else {
      statusBar.className = 'status-bar disconnected';
      statusText.textContent = '未连接 — 请确认后端已启动';
    }
  } catch (err) {
    // sendMessage 失败（如 service worker 未就绪）：按未连接处理，不抛异常
    console.warn('[穿搭采集器] 检查后端连接失败:', err);
    statusBar.className = 'status-bar disconnected';
    statusText.textContent = '未连接 — 请确认后端已启动';
  }
}

/** 加载设置 */
async function loadSettings() {
  try {
    const settings = await chrome.runtime.sendMessage({ type: 'GET_SETTINGS' });
    if (settings) {
      document.getElementById('apiUrl').value =
        settings.apiUrl || 'http://localhost:18888';
      document.getElementById('autoAnalyze').checked = settings.autoAnalyze !== false;
      skipDuplicates = settings.skipDuplicates !== false;
      document.getElementById('skipDuplicates').checked = skipDuplicates;
    }
  } catch (err) {
    // service worker 冷启动 / 未就绪时 sendMessage 可能失败：用默认值，不抛异常
    console.warn('[穿搭采集器] 加载设置失败（使用默认值）:', err);
  }
}

/** 从当前页面提取图片 */
async function loadImages() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      // 防御：无当前标签页（罕见）时给出提示而非静默失败
      imageGrid.innerHTML = '<p class="hint">无法获取当前标签页，请刷新后重试</p>';
      totalCountEl.textContent = '0';
      updateFooter();
      return;
    }

    // 仅支持小红书 / 抖音页面；tab.url 可能为空（无 tabs 权限时），防御性判断
    const tabUrl = tab.url || '';
    if (!/xiaohongshu\.com|douyin\.com/.test(tabUrl)) {
      imageGrid.innerHTML =
        '<p class="hint">请先打开小红书或抖音的穿搭页面，<br/>再点击本插件采集。</p>';
      totalCountEl.textContent = '0';
      updateFooter();
      return;
    }

    // 注入内容脚本提取图片（每次注入都会重新提取，兼容 SPA 页面导航）
    let results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content-scripts/extract-images.js'],
    });
    let result = results && results[0] && results[0].result;

    // 若首次提取为空，稍等片刻重试一次（懒加载 / SPA 异步渲染未完成）
    if (!result || (result.images && result.images.length === 0)) {
      await new Promise((resolve) => setTimeout(resolve, 600));
      results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ['content-scripts/extract-images.js'],
      });
      result = results && results[0] && results[0].result;
    }

    imageList = (result && result.images) || [];
    metadata = (result && result.metadata) || {};
    selectedUrls.clear();
    await markCollectedImages();
    renderImages();
    console.log(`[穿搭采集器] 提取到 ${imageList.length} 张候选图片`);
  } catch (err) {
    // executeScript 失败（页面不可注入 / 权限未授予等）：记录日志便于排查
    console.error('[穿搭采集器] 加载图片失败:', err);
    imageGrid.innerHTML =
      '<p class="hint">无法读取当前页面。<br/>请确保在小红书或抖音页面使用此插件。</p>';
  }
}

/** 上传前查重：按平台 ID 查询后端，把已入库的图片标记为「已采集」。
 *
 *  平台 ID 是页面级笔记 ID，同一页提取的所有图片共享同一个 ID，
 *  因此一次查询即可覆盖整页；查重失败（后端未启动等）时静默降级，
 *  不影响正常采集流程。
 */
async function markCollectedImages() {
  const platformId = metadata && metadata.platformId;
  if (!platformId || imageList.length === 0) return;
  try {
    const res = await chrome.runtime.sendMessage({
      type: 'CHECK_PLATFORM_ID',
      platformId,
    });
    if (res && res.exists) {
      imageList.forEach((img) => {
        img.collected = true;
      });
    }
  } catch (err) {
    console.warn('[穿搭采集器] 上传前查重失败（忽略，继续正常采集）:', err);
  }
}

/** 渲染图片网格 */
function renderImages() {
  if (imageList.length === 0) {
    imageGrid.innerHTML =
      '<p class="hint">当前页面未检测到穿搭图片<br/>请打开小红书或抖音的穿搭笔记</p>';
    totalCountEl.textContent = '0';
    updateFooter();
    return;
  }

  imageGrid.innerHTML = imageList
    .map(
      (img, i) => `
    <div class="image-item ${selectedUrls.has(img.url) ? 'selected' : ''} ${img.collected ? 'collected' : ''}"
         data-index="${i}"
         data-url="${escapeHtml(img.url)}">
      <!-- referrerpolicy="no-referrer"：小红书/抖音 CDN 图片通常按 Referer 防盗链，
           扩展页面 Referer 为 chrome-extension:// 会被拒绝（裂图），去掉 Referer 可正常预览 -->
      <img src="${escapeHtml(img.url)}" alt="${escapeHtml(img.alt)}" loading="lazy"
           referrerpolicy="no-referrer" />
      <div class="checkbox">${selectedUrls.has(img.url) ? '✓' : ''}</div>
      ${img.collected ? '<div class="collected-badge">已采集</div>' : ''}
    </div>`
    )
    .join('');

  totalCountEl.textContent = imageList.length;
  updateFooter();

  // 绑定点击事件
  imageGrid.querySelectorAll('.image-item').forEach((item) => {
    item.addEventListener('click', () => toggleSelect(item.dataset.url, item));
  });
}

/** 切换选中状态（开启查重跳过时，已采集图片不可选中） */
function toggleSelect(url, element) {
  const img = imageList.find((x) => x.url === url);
  if (skipDuplicates && img && img.collected) return;

  if (selectedUrls.has(url)) {
    selectedUrls.delete(url);
    if (element) {
      element.classList.remove('selected');
      element.querySelector('.checkbox').textContent = '';
    }
  } else {
    selectedUrls.add(url);
    if (element) {
      element.classList.add('selected');
      element.querySelector('.checkbox').textContent = '✓';
    }
  }
  updateFooter();
}

/** 更新底部操作栏 */
function updateFooter() {
  selectedCountEl.textContent = selectedUrls.size;
  uploadBtn.disabled = selectedUrls.size === 0;
  uploadBtn.textContent = selectedUrls.size > 0 ? `采集入库 (${selectedUrls.size})` : '采集入库';
}

/** 全选/取消全选（开启查重跳过时，已采集图片不参与全选） */
function toggleSelectAll() {
  const selectable = imageList.filter((img) => !(skipDuplicates && img.collected));
  if (selectable.length > 0 && selectedUrls.size === selectable.length) {
    // 取消全选
    selectedUrls.clear();
  } else {
    // 全选（仅可选图片）
    selectable.forEach((img) => selectedUrls.add(img.url));
  }
  renderImages();
}

/** 上传选中的图片 */
async function uploadImages() {
  if (selectedUrls.size === 0) return;

  showUploadOverlay(true);
  const images = Array.from(selectedUrls);
  let done = 0;

  uploadProgressText.textContent = `正在采集... (0/${images.length})`;

  try {
    const response = await chrome.runtime.sendMessage({
      type: 'UPLOAD_IMAGES',
      images,
      metadata: {
        ...metadata,
        platform: metadata.platform || 'browser_extension',
        sourceUrl: metadata.sourceUrl,
        author: metadata.author,
        platformId: metadata.platformId,
      },
    });

    if (response && response.results) {
      const successCount = response.results.filter((r) => r.success).length;
      const failCount = response.results.filter((r) => !r.success).length;
      const firstError = response.results.find((r) => !r.success);

      if (successCount > 0 && failCount === 0) {
        uploadProgressText.textContent = `✅ 全部采集成功 (${successCount} 张)`;
      } else if (successCount > 0) {
        uploadProgressText.textContent = `⚠️ 部分成功: ${successCount} 张, ${failCount} 张失败${firstError ? `（${firstError.error}）` : ''}`;
      } else {
        uploadProgressText.textContent = firstError
          ? `❌ 采集失败：${firstError.error}`
          : '❌ 采集失败，请确认后端已启动';
      }
    }
  } catch (err) {
    uploadProgressText.textContent = `❌ 通信失败: ${err.message}`;
  }

  setTimeout(() => {
    showUploadOverlay(false);
    selectedUrls.clear();
    renderImages();
  }, 1500);
}

function showUploadOverlay(show) {
  uploadOverlay.classList.toggle('hidden', !show);
}

/** HTML 转义：同时转义引号，防止页面图片 URL 经 innerHTML 渲染时注入属性
 * （如 data-url/src 属性逃逸） */
function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 绑定事件 */
function setupEventListeners() {
  selectAllBtn.addEventListener('click', toggleSelectAll);
  uploadBtn.addEventListener('click', uploadImages);
  settingsBtn.addEventListener('click', () =>
    settingsPanel.classList.toggle('hidden')
  );
  closeSettingsBtn.addEventListener('click', () =>
    settingsPanel.classList.add('hidden')
  );
  saveSettingsBtn.addEventListener('click', async () => {
    const apiUrl = document.getElementById('apiUrl').value;
    const autoAnalyze = document.getElementById('autoAnalyze').checked;
    skipDuplicates = document.getElementById('skipDuplicates').checked;
    await chrome.runtime.sendMessage({
      type: 'SAVE_SETTINGS',
      settings: { apiUrl, autoAnalyze, skipDuplicates },
    });
    settingsPanel.classList.add('hidden');
    checkConnection();
    // 跳过设置变更后立即重渲染：重新开启跳过时，取消已采集图片的选中状态
    if (skipDuplicates) {
      imageList.forEach((img) => {
        if (img.collected) selectedUrls.delete(img.url);
      });
    }
    renderImages();
  });
}
