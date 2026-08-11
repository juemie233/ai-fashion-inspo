/** 内容脚本：从当前页面提取可采集的图片列表。

  自动识别平台（小红书/抖音），过滤掉头像、图标等无效图片，
  只保留穿搭相关的图片供用户选择。
*/

(function () {
  // 标记是否已经提取过，避免重复
  if (window.__fashionInspoExtracted) return;
  window.__fashionInspoExtracted = true;

  /** 检测当前平台 */
  function detectPlatform() {
    const host = window.location.hostname;
    if (host.includes('xiaohongshu.com')) return 'xiaohongshu';
    if (host.includes('douyin.com')) return 'douyin';
    return 'unknown';
  }

  /** 从图片 URL 或属性中提取可能的平台 ID */
  function extractPlatformId(img) {
    const platform = detectPlatform();
    const pageUrl = window.location.href;

    if (platform === 'xiaohongshu') {
      // 小红书：从 URL 提取笔记 ID
      const match = pageUrl.match(/\/explore\/([a-zA-Z0-9]+)/) ||
                    pageUrl.match(/\/note\/([a-zA-Z0-9]+)/);
      return match ? match[1] : null;
    }

    if (platform === 'douyin') {
      // 抖音：从 URL 提取视频 ID
      const match = pageUrl.match(/\/video\/(\d+)/) ||
                    pageUrl.match(/\/note\/(\d+)/);
      return match ? match[1] : null;
    }

    return null;
  }

  /** 判断图片是否可能是穿搭图片（过滤头像、图标等小图） */
  function isLikelyFashionImage(img) {
    const { naturalWidth, naturalHeight, width, height } = img;

    // 使用 naturalWidth/Height（原始尺寸），fallback 到 CSS 尺寸
    const w = naturalWidth || width || 0;
    const h = naturalHeight || height || 0;

    // 过滤太小的图（头像、图标等）
    if (w < 200 || h < 200) return false;

    // 过滤极端宽高比（横幅广告、超长图）
    const ratio = w / h;
    if (ratio > 4 || ratio < 0.25) return false;

    // 过滤明显的 UI 元素（1:1 小方图可能是图标）
    if (w < 300 && h < 300 && Math.abs(ratio - 1) < 0.1) return false;

    return true;
  }

  /** 提取所有符合条件的穿搭图片 */
  function extractImages() {
    const allImages = document.querySelectorAll('img');
    const fashionImages = [];

    for (const img of allImages) {
      if (!img.src || !img.src.startsWith('http')) continue;
      if (!isLikelyFashionImage(img)) continue;

      fashionImages.push({
        url: img.src,
        width: img.naturalWidth || img.width,
        height: img.naturalHeight || img.height,
        alt: img.alt || '',
      });
    }

    // 去重（按 URL）
    const seen = new Set();
    const unique = fashionImages.filter((img) => {
      if (seen.has(img.url)) return false;
      seen.add(img.url);
      return true;
    });

    return unique;
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
})();
