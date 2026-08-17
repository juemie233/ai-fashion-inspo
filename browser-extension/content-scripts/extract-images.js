/** 内容脚本：从当前页面提取可采集的图片列表。
 *
 * 自动识别平台（小红书/抖音），过滤头像、图标等无效图片，
 * 只保留穿搭相关的图片供用户选择。
 *
 * 设计说明：
 * - 每次注入都会重新提取（popup 每次打开都会注入一次本脚本），
 *   避免 SPA 路由切换或数据异步加载后仍停留在旧数据上；
 *   因此不再使用「只提取一次」的全局标记。
 * - 兼容懒加载图片：优先读取 src / currentSrc，其次 data-src、
 *   data-lazy-src、data-original、srcset，未加载完成的图也能进入候选，
 *   由后端下载真实图片。
 * - 头像过滤优先按 URL 与 DOM 容器特征（avatar/icon/logo 等）判断，
 *   因为头像原图尺寸可能较大，纯尺寸过滤会漏网。
 */

(function () {
  /** 检测当前平台 */
  function detectPlatform() {
    const host = window.location.hostname;
    if (host.includes('xiaohongshu.com')) return 'xiaohongshu';
    if (host.includes('douyin.com')) return 'douyin';
    return 'unknown';
  }

  /** 从图片 URL 或属性中提取可能的平台 ID */
  function extractPlatformId() {
    const platform = detectPlatform();
    const pageUrl = window.location.href;

    if (platform === 'xiaohongshu') {
      // 小红书：笔记详情页 URL 形态较多（explore / discovery/item / note）
      const match = pageUrl.match(/\/(?:explore|discovery\/item|note)\/([a-zA-Z0-9]+)/);
      return match ? match[1] : null;
    }

    if (platform === 'douyin') {
      const match = pageUrl.match(/\/video\/(\d+)/) || pageUrl.match(/\/note\/(\d+)/);
      return match ? match[1] : null;
    }

    return null;
  }

  /** 从 srcset 中取出分辨率最高的一张 */
  function pickLargestSrcset(srcset) {
    if (!srcset) return null;
    let best = null;
    let bestSize = -1;
    for (const part of srcset.split(',')) {
      const pieces = part.trim().split(/\s+/);
      const url = pieces[0];
      if (!url || !url.startsWith('http')) continue;
      const sizeStr = pieces[1] || '';
      const size = parseInt(sizeStr.replace(/[wx]/gi, ''), 10) || 0;
      if (size >= bestSize) {
        bestSize = size;
        best = url;
      }
    }
    return best;
  }

  /** 从 img 元素解析出真实图片 URL（兼容懒加载） */
  function resolveImageUrl(img) {
    const candidates = [
      img.currentSrc || img.src,
      img.dataset.src,
      img.getAttribute('data-lazy-src'),
      img.getAttribute('data-original'),
      img.dataset.srcset ? pickLargestSrcset(img.dataset.srcset) : null,
      pickLargestSrcset(img.srcset),
    ];
    for (const url of candidates) {
      if (url && url.startsWith('http')) return url;
    }
    return null;
  }

  /** 判断 img 是否处于明显的非内容区域（头像/图标/logo/表情等） */
  function isInNonContentArea(img, url) {
    // URL 特征：头像/图标/logo/表情/二维码等（url 为解析后的真实地址，兼容懒加载）
    if (url && /(avatar|\.icon[^.]|logo|emoji|badge|qr_?code|default_head)/.test(url.toLowerCase())) {
      return true;
    }

    // DOM 上下文：向上查找祖先节点的 class / id（限 6 层，避免误伤正文容器）
    let el = img.parentElement;
    for (let i = 0; el && i < 6; i += 1, el = el.parentElement) {
      const cls = `${el.className || ''} ${el.id || ''}`.toLowerCase();
      if (/(avatar|icon|logo|emoji|badge|qr_?code|default_head)/.test(cls)) return true;
    }
    return false;
  }

  /** 判断图片是否可能是穿搭图片（过滤头像、图标等小图） */
  function isLikelyFashionImage(img, url) {
    // 先按 URL / 容器特征排除头像、图标等（头像原图可能很大，尺寸过滤挡不住）
    if (isInNonContentArea(img, url)) return false;

    const naturalW = img.naturalWidth || 0;
    const naturalH = img.naturalHeight || 0;

    if (naturalW && naturalH) {
      // 已加载：用原始尺寸过滤
      if (naturalW < 120 || naturalH < 120) return false;

      const ratio = naturalW / naturalH;
      if (ratio > 4 || ratio < 0.25) return false;

      // 过滤明显的 UI 元素（1:1 小方图可能是图标）
      if (naturalW < 240 && naturalH < 240 && Math.abs(ratio - 1) < 0.1) return false;

      return true;
    }

    // 未加载（懒加载）：Chrome 对无 src 的 img 给占位尺寸（约 80x21），
    // 不能当真实尺寸过滤，否则会把 data-src 的懒加载内容图全部误杀
    const hasLazySource = !!(
      img.dataset.src ||
      img.getAttribute('data-lazy-src') ||
      img.getAttribute('data-original') ||
      img.srcset ||
      img.dataset.srcset
    );
    if (hasLazySource) return true;

    // 无真实来源的占位图/装饰图：按布局尺寸过滤
    const rect = img.getBoundingClientRect();
    const w = rect.width || img.width || 0;
    const h = rect.height || img.height || 0;
    if (w < 120 || h < 120) return false;

    // 过滤极端宽高比（横幅广告、超长图）
    const ratio = w / h;
    if (ratio > 4 || ratio < 0.25) return false;

    return true;
  }

  /** 去掉 URL 上的尺寸后缀/裁剪参数，用于跨尺寸去重 */
  function normalizeUrl(url) {
    return url.replace(/_[0-9]+x[0-9]+/i, '').replace(/\?[^#]*/, '');
  }

  /** 提取所有符合条件的穿搭图片 */
  function extractImages() {
    const allImages = document.querySelectorAll('img');
    // 按去尺寸后缀后的 URL 分组，同一张图多尺寸时保留原始尺寸更大的一张
    const byKey = new Map();

    for (const img of allImages) {
      const url = resolveImageUrl(img);
      if (!url) continue;
      if (!isLikelyFashionImage(img, url)) continue;

      const key = normalizeUrl(url);
      // 未加载完成的图（懒加载）naturalWidth 为 0，尺寸记 0（未知），展示时不误导
      const width = img.naturalWidth || 0;
      const height = img.naturalHeight || 0;
      const prev = byKey.get(key);

      if (!prev || width > prev.width) {
        byKey.set(key, { url, width, height, alt: img.alt || '' });
      }
    }

    return Array.from(byKey.values());
  }

  /** 提取页面元数据 */
  function extractMetadata() {
    const platform = detectPlatform();

    let author = '';
    if (platform === 'xiaohongshu') {
      const authorEl = document.querySelector('.username, .author .name, [class*="nickname"]');
      author = authorEl ? authorEl.textContent.trim() : '';
    } else if (platform === 'douyin') {
      const authorEl = document.querySelector('[data-e2e="user-info"] .name, [class*="author-name"]');
      author = authorEl ? authorEl.textContent.trim() : '';
    }

    return {
      platform,
      platformId: extractPlatformId(),
      sourceUrl: window.location.href,
      author,
    };
  }

  // 将结果暴露到全局，供 popup 读取
  window.__fashionInspoData = {
    images: extractImages(),
    metadata: extractMetadata(),
    timestamp: Date.now(),
  };

  console.log(
    `[穿搭采集器] 从 ${detectPlatform()} 提取了 ${window.__fashionInspoData.images.length} 张候选图片`
  );

  // 返回值供 chrome.scripting.executeScript 直接取用（注入文件的完成值）
  return window.__fashionInspoData;
})();
