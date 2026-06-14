"""
脉脉帖子抓取模块

功能：
  1. 打开指定话题页
  2. 找到「小渔学姐」的最新帖子
  3. 抓取完整文字 + 全部图片（原图）
  4. 用内容哈希做去重判断

前置条件：
  - Chrome 已启动（python3 start_chrome.py）
  - 已登录脉脉

用法：
    scraper = MaimaiScraper()
    scraper.connect()
    post = scraper.fetch_post(target_username="小渔学姐")
    if post and not scraper.is_duplicate(post["post_id"]):
        # 处理新帖子...
"""

import re
import hashlib
import time
from typing import Optional, List
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from loguru import logger

from config import settings, PROJECT_ROOT
from db.database import get_connection


# Chrome 远程调试地址
CDP_URL = "http://localhost:9222"

# 话题页 URL
TOPIC_URL = "https://maimai.cn/community/topic-detail/SxAXPZZ2/hot"

# 图片下载目录
IMAGE_DIR = PROJECT_ROOT / "downloads"


class MaimaiScraper:
    """
    脉脉帖子抓取器

    用法：
        scraper = MaimaiScraper()
        scraper.connect()
        post = scraper.fetch_post("小渔学姐")
    """

    def __init__(self, topic_url: str = TOPIC_URL):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self.topic_url = topic_url

    def connect(self) -> bool:
        """连接 Chrome"""
        logger.info(f"连接 Chrome...")
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(CDP_URL)
            self._context = self._browser.contexts[0]
            logger.success("✓ Chrome 已连接")
            return True
        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        if self._playwright:
            self._playwright.stop()

    def fetch_post(self, target_username: str = "小渔学姐") -> Optional[dict]:
        """
        抓取指定用户的第一条帖子

        流程：
          1. 刷新话题页
          2. 点击帖子标题/正文打开全文
          3. 抓取完整文字 + 全部图片

        参数:
            target_username: 目标用户名

        返回:
            {
                "post_id": "abc123",           # 去重ID
                "username": "小渔学姐",
                "title": "帖子标题",
                "content": "完整正文",
                "images": [{"original": "url"}], # 图片列表
                "fetched_at": "2024-01-01T...",  # 抓取时间
            }
            如果没找到帖子返回 None
        """
        logger.info(f"抓取帖子: {target_username}")

        # 1. 刷新话题页
        page = self._get_maimai_page()
        logger.debug("刷新话题页...")
        page.goto(self.topic_url, wait_until="domcontentloaded", timeout=15000)
        time.sleep(5)

        # 2. 点击帖子标题打开全文
        self._click_to_expand(page, target_username)
        time.sleep(5)

        # 验证是否已展开（检查「发布于」文字）
        is_expanded = page.evaluate('''() => {
            const text = document.body.innerText;
            return text.includes('发布于');
        }''')
        if not is_expanded:
            logger.warning("帖子可能未完全展开，重试点击...")
            self._click_to_expand(page, target_username)
            time.sleep(3)

        # 3. 抓取帖子
        raw = page.evaluate('''(username) => {
            // 找用户名
            const allEls = document.querySelectorAll('span, div, a');
            let usernameEl = null;
            for (const el of allEls) {
                if (el.textContent.trim() === username && el.children.length === 0) {
                    usernameEl = el;
                    break;
                }
            }
            if (!usernameEl) return { found: false, reason: '用户名未找到' };

            // 向上找帖子容器（展开后的包含「发布于」）
            let container = null;
            let el = usernameEl;
            for (let i = 0; i < 15; i++) {
                el = el.parentElement;
                if (!el) break;
                const text = (el.textContent || '').trim();
                if (text.includes('发布于') && text.length > 50 && text.length < 1500) {
                    container = el;
                    break;
                }
            }
            if (!container) return { found: false, reason: '帖子容器未找到' };

            // 提取文字
            const fullText = container.textContent.trim();

            // 提取图片（过滤掉头像：小正方形图片 < 50px）
            const imgs = container.querySelectorAll('img');
            const images = [];
            for (const img of imgs) {
                const src = img.src || '';
                const rect = img.getBoundingClientRect();
                const w = Math.round(rect.width);
                const h = Math.round(rect.height);
                // 头像特征：小正方形（38x38），内容图片通常 126px+ 且为长方形
                const isSmallSquare = w < 50 && h < 50 && Math.max(w, h) / Math.min(w, h) < 1.3;
                if (src && !isSmallSquare && !src.includes('avatar')) {
                    const original = src.replace(/-t\\d+/, '').split('?')[0];
                    images.push({
                        thumbnail: src,
                        original: original,
                        width: w,
                        height: h,
                    });
                }
            }

            return { found: true, fullText: fullText, images: images };
        }''', target_username)

        if not raw.get('found'):
            logger.error(f"抓取失败: {raw.get('reason')}")
            return None

        # 处理抓取结果
        full_text = raw['fullText']
        images = raw['images']

        # 分离标题和正文
        title, content = self._parse_text(full_text, target_username)

        # 生成帖子唯一 ID（内容哈希）
        post_id = hashlib.md5(content.encode()).hexdigest()[:12]

        from datetime import datetime
        result = {
            "post_id": post_id,
            "username": target_username,
            "title": title,
            "content": content,
            "images": images,
            "fetched_at": datetime.now().isoformat(),
        }

        logger.success(f"抓取成功 ✓ ID={post_id} 标题={title[:30]} 图片={len(images)}张")
        return result

    def fetch_post_by_url(self, post_url: str) -> Optional[dict]:
        """
        从帖子详情页 URL 直接抓取帖子

        与 fetch_post() 不同，这个方法直接打开帖子详情页，
        帖子内容已经展开，不需要搜索用户名或点击展开。

        支持多种 URL 格式：
          - https://maimai.cn/community/gossip-detail/37006232
          - https://maimai.cn/n/content/gossip-detail/37007088 (移动端分享链接)
          - https://maimai.cn/community/topic-detail/SxAXPZZ2/hot

        参数:
            post_url: 帖子详情页 URL

        返回:
            与 fetch_post() 相同格式的 dict，失败返回 None
        """
        logger.info(f"从 URL 抓取帖子: {post_url}")

        # 1. 规范化 URL：将移动端分享链接转为桌面端链接
        #    /n/content/gossip-detail/123 → /community/gossip-detail/123
        import re
        normalized_url = re.sub(
            r'https?://maimai\.cn/n/content/',
            'https://maimai.cn/community/',
            post_url,
        )
        # 注意：不要去掉查询参数！脉脉需要 egid/gid 等参数才能正确显示帖子

        # 2. 打开详情页
        page = self._get_maimai_page()
        logger.debug("导航到帖子详情页...")
        page.goto(normalized_url, wait_until="domcontentloaded", timeout=15000)
        time.sleep(5)

        # 2. 从详情页抓取内容
        #    脉脉详情页结构：导航栏 → 帖子正文区（含用户名、时间、正文、图片）→ 评论区
        #    策略：先尝试精确定位帖子正文区，再回退到评分启发式
        raw = page.evaluate('''() => {
            let postContainer = null;

            // 策略1：精确定位 — 找包含「发布于」文字的最小容器
            // 帖子正文区一定包含「发布于」，且不会包含评论区的大量评论
            const allEls = document.querySelectorAll('span, div, p, section, article');
            let bestEl = null;
            let bestLen = Infinity;
            for (const el of allEls) {
                const text = (el.textContent || '').trim();
                if (!text.includes('发布于')) continue;
                // 排除太大的容器（包含整个页面）和太小的
                if (text.length < 50 || text.length > 3000) continue;
                // 找包含「发布于」且文字最少的最小容器 = 帖子正文
                if (text.length < bestLen) {
                    bestLen = text.length;
                    bestEl = el;
                }
            }
            if (bestEl) {
                postContainer = bestEl;
            }

            // 策略2：如果策略1失败，找包含用户名+「我来爆个料」的区域
            if (!postContainer) {
                for (const el of allEls) {
                    const text = (el.textContent || '').trim();
                    if (text.includes('我来爆个料') && text.length > 50 && text.length < 3000) {
                        postContainer = el;
                        break;
                    }
                }
            }

            // 策略3：评分启发式（兜底）
            if (!postContainer) {
                const candidates = document.querySelectorAll('div');
                let bestScore = 0;
                for (const div of candidates) {
                    const text = (div.textContent || '').trim();
                    const hasPublishTime = text.includes('发布于') || text.includes('小时前') || text.includes('天前');
                    let score = 0;
                    if (hasPublishTime) score += 10;
                    if (text.length > 50 && text.length < 3000) score += 5;
                    if (text.length > 10000) score -= 20;
                    if (score > bestScore) {
                        bestScore = score;
                        postContainer = div;
                    }
                }
            }

            if (!postContainer) return { found: false, reason: '帖子容器未找到' };

            // 提取用户名
            let username = '';
            const nameEls = postContainer.querySelectorAll('span, a, div');
            for (const el of nameEls) {
                const t = el.textContent.trim();
                if (t.length >= 2 && t.length <= 15
                    && !t.includes('发布') && !t.includes('评论')
                    && !t.includes('删除') && !t.includes('回复')
                    && !t.includes('小时') && !t.includes('天前')
                    && !t.includes('领域') && !t.includes('创作者')
                    && !t.includes('推荐') && !t.includes('职言')
                    && !t.includes('招聘') && !t.includes('企业号')
                    && el.children.length === 0) {
                    const rect = el.getBoundingClientRect();
                    const containerRect = postContainer.getBoundingClientRect();
                    if (rect.top < containerRect.top + 80) {
                        username = t;
                        break;
                    }
                }
            }

            // 提取文字
            const fullText = postContainer.textContent.trim();

            // 提取图片（严格过滤：排除头像、导航图标等小图）
            const imgs = postContainer.querySelectorAll('img');
            const images = [];
            for (const img of imgs) {
                const src = img.src || '';
                const rect = img.getBoundingClientRect();
                const w = Math.round(rect.width);
                const h = Math.round(rect.height);
                // 排除：头像(<50px正方形)、导航图标(宽度<50)、SVG图标
                const isSmallSquare = w < 50 && h < 50 && Math.max(w, h) / Math.min(w, h) < 1.3;
                const isTiny = w < 50 || h < 50;
                const isSvg = src.includes('svg') || src.includes('icon');
                if (src && !isSmallSquare && !isTiny && !isSvg && !src.includes('avatar')
                    && !src.includes('logo') && !src.includes('tab')) {
                    const original = src.replace(/-t\\d+/, '').split('?')[0];
                    images.push({
                        thumbnail: src,
                        original: original,
                        width: w,
                        height: h,
                    });
                }
            }

            return { found: true, fullText: fullText, images: images, username: username };
        }''')

        if not raw.get('found'):
            logger.error(f"抓取失败: {raw.get('reason')}")
            return None

        full_text = raw['fullText']
        images = raw['images']
        username = raw.get('username', 'unknown')

        # 分离标题和正文
        title, content = self._parse_text(full_text, username)

        # 生成帖子唯一 ID
        post_id = hashlib.md5(content.encode()).hexdigest()[:12]

        from datetime import datetime
        result = {
            "post_id": post_id,
            "username": username,
            "title": title,
            "content": content,
            "images": images,
            "fetched_at": datetime.now().isoformat(),
        }

        logger.success(f"抓取成功 ✓ ID={post_id} 标题={title[:30]} 图片={len(images)}张")
        return result

    def is_duplicate(self, post_id: str) -> bool:
        """检查帖子是否已抓取过"""
        conn = get_connection()
        try:
            # 确保表存在
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scraped_posts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id     TEXT NOT NULL UNIQUE,
                    username    TEXT NOT NULL,
                    title       TEXT DEFAULT '',
                    content     TEXT NOT NULL,
                    images      TEXT DEFAULT '[]',
                    status      TEXT DEFAULT 'scraped',
                    scraped_at  TEXT NOT NULL
                )
            """)
            row = conn.execute(
                "SELECT id FROM scraped_posts WHERE post_id = ?",
                (post_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def save_post(self, post: dict):
        """保存帖子记录（用于去重）"""
        conn = get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scraped_posts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id     TEXT NOT NULL UNIQUE,
                    username    TEXT NOT NULL,
                    title       TEXT DEFAULT '',
                    content     TEXT NOT NULL,
                    images      TEXT DEFAULT '[]',
                    status      TEXT DEFAULT 'scraped',
                    scraped_at  TEXT NOT NULL
                )
            """)
            conn.execute(
                """INSERT OR IGNORE INTO scraped_posts
                    (post_id, username, title, content, images, status, scraped_at)
                VALUES (?, ?, ?, ?, ?, 'scraped', ?)""",
                (
                    post["post_id"],
                    post["username"],
                    post["title"],
                    post["content"],
                    str(post["images"]),
                    post["fetched_at"],
                ),
            )
            conn.commit()
            logger.info(f"帖子已保存 (id={post['post_id']}) ✓")
        finally:
            conn.close()

    def download_images(self, post: dict) -> List[str]:
        """
        下载帖子图片到本地

        返回:
            本地文件路径列表
        """
        import urllib.request

        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        post_dir = IMAGE_DIR / post["post_id"]
        post_dir.mkdir(exist_ok=True)

        local_paths = []
        for i, img in enumerate(post["images"], 1):
            url = img["original"]
            if not url:
                continue

            ext = ".jpg"  # 脉脉图片默认 jpg
            filename = f"{i}{ext}"
            filepath = post_dir / filename

            if filepath.exists():
                logger.debug(f"  图片 {i} 已存在，跳过")
                local_paths.append(str(filepath))
                continue

            try:
                logger.debug(f"  下载图片 {i}: {url[:60]}...")
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Referer": "https://maimai.cn/",
                })
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                    # 过滤太小的文件（<5KB 通常是图标/损坏图片，不是内容图片）
                    if len(data) < 5000:
                        logger.debug(f"  图片 {i} 太小({len(data)}字节)，跳过")
                        continue
                    with open(filepath, "wb") as f:
                        f.write(data)
                local_paths.append(str(filepath))
                logger.debug(f"  ✓ 图片 {i} 已下载")
            except Exception as e:
                logger.warning(f"  图片 {i} 下载失败: {e}")

        logger.info(f"图片下载完成: {len(local_paths)}/{len(post['images'])} 张")
        return local_paths

    def _get_maimai_page(self) -> Page:
        """获取或创建脉脉页面"""
        for pg in self._context.pages:
            if "maimai.cn" in pg.url:
                self._page = pg
                return pg
        page = self._context.new_page()
        self._page = page
        return page

    def _click_to_expand(self, page: Page, username: str):
        """点击帖子标题/正文区域打开全文"""
        logger.debug("点击帖子打开全文...")
        page.evaluate('''(username) => {
            // 策略：找到用户名下方的帖子标题文字，点击其父容器
            const all = document.querySelectorAll('*');
            let foundTitle = null;

            // 先找用户名位置
            let usernameY = 0;
            for (const el of all) {
                if (el.textContent.trim() === username && el.children.length === 0) {
                    usernameY = el.getBoundingClientRect().y;
                    break;
                }
            }

            // 找帖子标题（在用户名下方、短文字、不含「查看更多」等）
            for (const el of all) {
                const text = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                // 标题文字：在用户名下方、纯文本、长度适中
                if (el.children.length === 0
                    && text.length > 5 && text.length < 80
                    && !text.includes(username)
                    && !text.includes('查看更多')
                    && !text.includes('评论')
                    && !text.includes('删除')
                    && !text.includes('我来爆个料')
                    && !text.includes('条帖子')
                    && !text.includes('小时前')
                    && !text.includes('天前')
                    && !text.includes('领域创作者')
                    && rect.y > usernameY
                    && rect.y < usernameY + 300
                    && rect.width > 50) {
                    foundTitle = el;
                    break;
                }
            }

            if (foundTitle) {
                // 点击标题的父容器（更大点击区域）
                const parent = foundTitle.parentElement;
                if (parent && parent.getBoundingClientRect().width > 100) {
                    parent.click();
                    return true;
                }
                // 直接点标题也行
                foundTitle.click();
                return true;
            }

            // 备选：点击任何在用户名下方的正文文字
            for (const el of all) {
                const text = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                if (el.children.length === 0
                    && text.length > 20 && text.length < 200
                    && rect.y > usernameY && rect.y < usernameY + 400
                    && !text.includes(username) && !text.includes('查看')
                    && !text.includes('评论') && !text.includes('删除')
                    && rect.width > 50) {
                    el.parentElement?.click();
                    return true;
                }
            }
            return false;
        }''', username)

    def _parse_text(self, full_text: str, username: str) -> tuple:
        """
        从原始文本中分离标题和正文

        脉脉帖子文本格式：
        "小渔学姐23小时前·职场领域创作者我来爆个料标题正文...发布于 四川5评论1删除"
        """
        # 去掉用户名前缀
        text = full_text
        if text.startswith(username):
            text = text[len(username):]

        # 去掉时间前缀（如 "23小时前·职场领域创作者"）
        text = re.sub(r'^[\d小时天分钟前秒刚刚]+·.*?创作者', '', text)
        text = re.sub(r'^我来爆个料', '', text)

        # 去掉尾部信息（"发布于 xxx N评论 N删除"）
        text = re.sub(r'发布于.*$', '', text)

        text = text.strip()

        if not text:
            return ("无标题", full_text)

        # 第一行作为标题
        lines = text.split('\n')
        title = lines[0].strip() if lines else "无标题"

        # 如果标题太长（>50字），截取前50字
        if len(title) > 50:
            title = title[:50] + "..."

        return (title, text)
