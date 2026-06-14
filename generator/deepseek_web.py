"""
DeepSeek 网页端自动化模块

用途：自动操作 DeepSeek 网页端，在已有的提示词工作流对话中发送消息并获取回复

完整流程：
  1. 连接到用户已打开的 Chrome
  2. 打开 DeepSeek 网页端
  3. 点击指定的历史对话（使用你预设好的提示词工作流）
  4. 输入话题关键词
  5. 点击发送
  6. 等待 AI 回复完成
  7. 提取回复文本

⚠️  前置条件：
  - Chrome 需要已启动带远程调试端口（python3 start_chrome.py）
  - 用户需要已登录 chat.deepseek.com

⚠️  风险提示：
  - DeepSeek 网页端可能会更新界面，导致选择器失效
  - 频繁操作可能触发反爬机制
  - 建议每次操作间隔 ≥ 10 秒
"""

import time
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from loguru import logger

from config import settings

# Chrome 远程调试地址
CDP_URL = "http://localhost:9222"

# DeepSeek 网址
DEEPSEEK_URL = "https://chat.deepseek.com/"

# 你常用的对话工作流名称 → 对话链接映射（首次运行时自动发现）
WORKFLOW_MAP = {}


class DeepSeekWeb:
    """
    DeepSeek 网页端自动化

    用法：
        ds = DeepSeekWeb()
        ds.connect()
        # 打开你已有的工作流对话
        ds.open_conversation("热点话题吐槽文章生成")
        # 发送话题并获取生成的文章
        reply = ds.send_and_wait("腾讯收购喜马拉雅")
        print(reply)
        ds.disconnect()
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def connect(self) -> bool:
        """连接到 Chrome"""
        logger.info(f"连接到 Chrome（{CDP_URL}）...")
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(CDP_URL)
            self._context = self._browser.contexts[0] if self._browser.contexts else None
            if not self._context:
                logger.error("❌ 未找到浏览器上下文")
                return False
            logger.success("✓ 已连接到 Chrome")
            return True
        except Exception as e:
            logger.error(f"❌ 连接失败: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        if self._playwright:
            self._playwright.stop()
        logger.info("已断开 Chrome 连接")

    def open_deepseek(self) -> Page:
        """打开或切换到 DeepSeek 页面"""
        logger.info("打开 DeepSeek...")

        # 先找已打开的
        for pg in self._context.pages:
            if "chat.deepseek.com" in pg.url:
                self._page = pg
                # 刷新到首页（对话列表）
                if "/a/chat/" in pg.url:
                    pg.goto(DEEPSEEK_URL, wait_until="domcontentloaded", timeout=10000)
                    time.sleep(2)
                logger.success("✓ DeepSeek 已打开")
                return pg

        # 没有就新建
        page = self._context.new_page()
        page.goto(DEEPSEEK_URL, wait_until="domcontentloaded", timeout=15000)
        time.sleep(3)
        self._page = page
        logger.success("✓ DeepSeek 已打开")
        return page

    def new_conversation(self) -> bool:
        """
        新建一个空白对话（避免历史上下文干扰）

        返回:
            True 成功
        """
        logger.info("新建对话...")

        # 点击「开启新对话」或「新对话」按钮
        clicked = self._page.evaluate('''() => {
            // 找"开启新对话"或"新对话"链接/按钮
            const all = document.querySelectorAll('a, button, span, div');
            for (const el of all) {
                const text = (el.textContent || '').trim();
                if ((text === '开启新对话' || text === '新对话') && el.getBoundingClientRect().width > 0) {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')

        if clicked:
            time.sleep(3)
            logger.success("✓ 已新建对话")
        else:
            logger.warning("未找到新建对话按钮，尝试直接导航...")
            self._page.goto(DEEPSEEK_URL, wait_until="domcontentloaded", timeout=10000)
            time.sleep(2)

        return True

    def list_conversations(self) -> list[dict]:
        """
        列出所有可用的对话

        返回:
            [{ "name": "对话名", "href": "链接" }, ...]
        """
        logger.info("获取对话列表...")

        conversations = self._page.evaluate('''() => {
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const text = (a.textContent || '').trim();
                const href = a.href || '';
                // 只取对话链接（包含 /a/chat/s/ 的）
                if (href.includes('/a/chat/s/') && text.length > 0 && text.length < 50) {
                    if (!seen.has(href)) {
                        seen.add(href);
                        results.push({ name: text, href: href });
                    }
                }
            }
            return results;
        }''')

        logger.info(f"找到 {len(conversations)} 个对话")
        for c in conversations:
            logger.debug(f"  • {c['name']}")

        return conversations

    def open_conversation(self, name: str) -> bool:
        """
        打开指定名称的对话

        参数:
            name: 对话名称（模糊匹配）

        返回:
            True 成功，False 失败
        """
        logger.info(f"打开对话: {name}")

        # 查找匹配的对话链接
        clicked = self._page.evaluate('''(targetName) => {
            const links = document.querySelectorAll('a');
            // 先精确匹配
            for (const a of links) {
                if (a.textContent.trim() === targetName && a.href.includes('/a/chat/s/')) {
                    a.click();
                    return { found: true, name: a.textContent.trim(), href: a.href, match: 'exact' };
                }
            }
            // 再模糊匹配
            for (const a of links) {
                const text = a.textContent.trim();
                if (text.includes(targetName) && a.href.includes('/a/chat/s/') && text.length < 50) {
                    a.click();
                    return { found: true, name: text, href: a.href, match: 'fuzzy' };
                }
            }
            return { found: false };
        }''', name)

        if not clicked.get('found'):
            logger.error(f"❌ 未找到对话: {name}")
            logger.info("可用对话列表:")
            convs = self.list_conversations()
            for c in convs:
                logger.info(f"  • {c['name']}")
            return False

        match_type = "精确匹配" if clicked.get('match') == 'exact' else "模糊匹配"
        logger.info(f"  {match_type}: {clicked['name']}")
        time.sleep(3)

        # 等待对话加载
        logger.success(f"✓ 已打开对话: {clicked['name']}")
        return True

    def send_and_wait(
        self,
        message: str,
        max_wait: int = 120,
        use_deep_think: bool = False,
        use_search: bool = False,
    ) -> Optional[str]:
        """
        发送消息并等待回复

        参数:
            message:        要发送的消息（如热点关键词）
            max_wait:       最长等待时间（秒），默认120秒
            use_deep_think: 是否开启「深度思考」模式
            use_search:     是否开启「智能搜索」模式（默认关闭，避免超慢）

        返回:
            AI 回复的文本内容，失败返回 None
        """
        logger.info(f"发送消息: {message[:50]}...")

        # 关闭「智能搜索」（默认关闭，搜索模式太慢且容易跑题）
        if not use_search:
            self._disable_search()

        # 可选：开启深度思考
        if use_deep_think:
            self._toggle_deep_think()

        # 输入消息
        textarea = self._page.locator('textarea')
        if textarea.count() == 0:
            logger.error("❌ 未找到输入框")
            return None

        textarea.click()
        time.sleep(0.3)

        # 逐字输入（模拟真实打字，避免被检测）
        self._page.keyboard.type(message, delay=20)
        time.sleep(0.5)

        # 点击发送按钮
        self._click_send()

        # 等待回复完成
        logger.info("等待 DeepSeek 回复...")
        reply = self._wait_for_reply(max_wait)

        if reply:
            logger.success(f"✓ 收到回复 ({len(reply)} 字)")
        else:
            logger.error("❌ 未收到回复")

        return reply

    def get_last_reply(self) -> Optional[str]:
        """
        获取当前对话中最后一条 AI 回复
        用于手动发送消息后获取回复
        """
        return self._extract_reply()

    def _toggle_deep_think(self):
        """开启「深度思考」模式"""
        logger.info("开启深度思考模式...")
        self._page.evaluate('''() => {
            const spans = document.querySelectorAll('span');
            for (const span of spans) {
                if (span.textContent.trim() === '深度思考') {
                    span.click();
                    return true;
                }
            }
            return false;
        }''')
        time.sleep(1)

    def _disable_search(self):
        """关闭「智能搜索」模式（避免生成太慢和跑题）"""
        logger.debug("关闭智能搜索模式...")
        self._page.evaluate('''() => {
            const spans = document.querySelectorAll('span');
            for (const span of spans) {
                if (span.textContent.trim() === '智能搜索') {
                    // 检查是否已经激活（有特定样式类）
                    const isActive = span.classList.contains('active') ||
                                     span.getAttribute('data-state') === 'active' ||
                                     span.style.color === 'rgb(0, 111, 238)';
                    if (isActive) {
                        span.click();
                        return true;
                    }
                    return false;  // 已经关闭
                }
            }
            return false;
        }''')
        time.sleep(0.5)

    def _click_send(self):
        """点击发送按钮"""
        # 找蓝色主按钮（发送按钮）
        clicked = self._page.evaluate('''() => {
            const btn = document.querySelector('div.ds-button--primary[role="button"]');
            if (btn) {
                btn.click();
                return true;
            }
            return false;
        }''')

        if not clicked:
            # 备用：按 Enter
            logger.debug("未找到发送按钮，尝试 Enter 键")
            self._page.keyboard.press('Enter')

        time.sleep(1)

    def _wait_for_reply(self, max_wait: int = 120) -> Optional[str]:
        """等待回复完成并提取文本"""
        check_interval = 5  # 每5秒检查一次（避免过于频繁）
        elapsed = 0
        no_generating_count = 0  # 连续"不在生成"的次数

        while elapsed < max_wait:
            time.sleep(check_interval)
            elapsed += check_interval

            # 检查是否还在生成
            is_generating = self._page.evaluate('''() => {
                // 方法1：查找停止按钮
                const stopBtn = document.querySelector(
                    'div[role="button"][aria-label*="stop"], ' +
                    'div[role="button"][aria-label*="停止"], ' +
                    'div.ds-button--danger[role="button"]'
                );
                if (stopBtn) return true;

                // 方法2：检查输入框是否可用（生成中输入框通常被禁用或隐藏）
                const textarea = document.querySelector('textarea');
                if (textarea && textarea.disabled) return true;

                // 方法3：查找"思考中"或"搜索中"的指示器
                const thinking = document.querySelector(
                    '[class*="thinking"], [class*="loading"], [class*="generating"]'
                );
                if (thinking) return true;

                return false;
            }''')

            if is_generating:
                no_generating_count = 0
                logger.debug(f"  ...生成中 ({elapsed}s)")
                continue
            else:
                no_generating_count += 1
                # 连续2次确认不在生成，才认为真的完成了
                if no_generating_count < 2:
                    logger.debug(f"  ...确认中 ({elapsed}s)")
                    continue

                # 等待最后渲染
                time.sleep(3)
                reply = self._extract_reply()
                if reply and len(reply) > 50:
                    logger.info(f"  生成完成 ({elapsed}s)")
                    return reply

        logger.warning(f"⏰ 等待超时 ({max_wait}s)")
        return self._extract_reply()

    def _extract_reply(self) -> Optional[str]:
        """提取最后一条 AI 回复文本（过滤掉思考过程）"""
        reply = self._page.evaluate('''() => {
            // 找到最后一条助手消息的整个容器
            // DeepSeek 的消息结构：每个消息块是一个独立容器
            // 我们需要找到最后一个助手消息的所有内容
            const markdownBlocks = document.querySelectorAll('[class*="ds-markdown--block"]');
            if (markdownBlocks.length > 0) {
                // 取最后一个块（即最后一条回复）
                const lastBlock = markdownBlocks[markdownBlocks.length - 1];
                const text = lastBlock.textContent.trim();
                if (text.length > 10) return text;
            }

            // 备用方法：取最后20个段落
            const paragraphs = document.querySelectorAll(
                'p.ds-markdown-paragraph, [class*="markdown-paragraph"]'
            );
            if (paragraphs.length > 0) {
                const start = Math.max(0, paragraphs.length - 30);
                const parts = [];
                for (let i = start; i < paragraphs.length; i++) {
                    const text = paragraphs[i].textContent.trim();
                    if (text) parts.push(text);
                }
                if (parts.length > 0) {
                    return parts.join('\\n\\n');
                }
            }

            return null;
        }''')

        if not reply:
            return None

        # 过滤 DeepSeek 思考过程（搜索时生成的中间文字）
        reply = self._clean_reply(reply)

        return reply

    def _clean_reply(self, text: str) -> str:
        """
        清理 DeepSeek 回复中的思考过程和无关内容

        DeepSeek 搜索模式下会输出思考过程，如：
        - "用户希望我..." / "所有搜索都已完成" / "现在需要撰写..."
        - 这些需要被过滤掉
        """
        lines = text.split('\n')
        cleaned = []
        skip = False

        for line in lines:
            stripped = line.strip()

            # 跳过典型的思考过程行
            think_patterns = [
                '用户希望我', '用户要求', '我需要',
                '所有搜索都已完成', '现在需要撰写',
                '为了全面获取信息', '我需要同时',
                '为了获取更详细的信息',
                '基于这些信息撰写', '搜索结果都已返回',
                '我将围绕', '文章将涵盖',
                '我将采用', '我将从',
            ]

            # 如果这一行匹配思考模式，跳过
            is_think_line = any(p in stripped for p in think_patterns)

            if is_think_line:
                skip = True
                continue

            # 如果遇到了正常文章内容（较长的行），停止跳过
            if skip and len(stripped) > 20:
                skip = False

            if not skip and stripped:
                cleaned.append(stripped)

        result = '\n'.join(cleaned)

        # 如果清理后内容太短，返回原始文本
        if len(result) < 50:
            return text

        return result
