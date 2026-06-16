"""
脉脉发帖模块 —— 自动在脉脉社区发布帖子

完整流程：
  1. 连接到用户已打开的 Chrome（带远程调试端口 9222）
  2. 打开脉脉社区发帖页（整个批量只打开一次）
  3. 切换身份为"职场领域创作者"
  4. 循环每篇帖子：填入标题/正文 → 添加话题 → 上传图片 → 点击"发动态" → 等待间隔

⚠️  前置条件：
  - Chrome 带调试端口(9222)启动
  - 已登录脉脉

⚠️  风险提示：
  - 发布是真实操作，会创建真实内容
  - 批量发帖需控制频率，建议每篇间隔 3 分钟
  - 不要短时间内大量发布，可能触发平台风控
"""

import random
import time
from typing import Optional, List

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from loguru import logger

from config import settings, PROJECT_ROOT


# ========== 常量 ==========

CDP_URL = "http://localhost:9222"
MAIMAI_HOME_URL = "https://maimai.cn/community/home/recommended"
DEFAULT_TOPIC = "我来爆个料"


class MaimaiPoster:
    """
    脉脉发帖器

    用法：
        poster = MaimaiPoster()
        poster.connect()
        poster.batch_post(posts=[...], interval=180)
        poster.disconnect()
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def connect(self) -> bool:
        """连接到用户已启动的 Chrome"""
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
            logger.error(f"❌ 连接 Chrome 失败: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        if self._playwright:
            self._playwright.stop()
        logger.info("已断开 Chrome 连接")

    def post(
        self,
        content: str,
        title: str = "",
        image_paths: List[str] = None,
        topic: str = DEFAULT_TOPIC,
        dry_run: bool = False,
    ) -> bool:
        """单篇发帖（会先打开页面）"""
        page = self._open_post_page()
        self._switch_identity(page)
        return self._fill_and_publish(page, content, title, image_paths, topic, dry_run)

    def batch_post(
        self,
        posts: List[dict],
        interval: int = 180,
        dry_run: bool = False,
    ) -> dict:
        """
        批量发帖 —— 只打开一次页面，循环填内容+发布

        参数:
            posts:    帖子列表，每项 {"content": str, "title": str, "image_paths": list, "topic": str}
            interval: 发帖间隔秒数，默认180秒(3分钟)
            dry_run:  干跑模式
        """
        total = len(posts)
        success = 0
        failed = 0
        results = []

        logger.info(f"📋 批量发帖开始: 共 {total} 篇，间隔 {interval} 秒")

        # 只打开一次页面
        page = self._open_post_page()
        self._switch_identity(page)

        for i, post_data in enumerate(posts, 1):
            logger.info(f"\n{'='*40}")
            logger.info(f"📝 第 {i}/{total} 篇")
            logger.info(f"{'='*40}")

            try:
                result = self._fill_and_publish(
                    page,
                    content=post_data.get("content", ""),
                    title=post_data.get("title", ""),
                    image_paths=post_data.get("image_paths"),
                    topic=post_data.get("topic", DEFAULT_TOPIC),
                    dry_run=dry_run,
                )

                if result:
                    success += 1
                    results.append({"index": i, "status": "success"})
                else:
                    failed += 1
                    results.append({"index": i, "status": "failed"})

            except Exception as e:
                logger.error(f"❌ 第 {i} 篇发帖失败: {e}")
                failed += 1
                results.append({"index": i, "status": "failed", "error": str(e)})
                # 出错后尝试重新打开页面并切换身份
                try:
                    page = self._open_post_page()
                    self._switch_identity(page)
                except Exception:
                    pass

            # 不是最后一篇时等待（随机抖动防检测）
            if i < total and not dry_run:
                jitter = random.randint(-30, 30)  # ±30秒抖动
                actual_wait = max(60, interval + jitter)  # 最少等1分钟
                logger.info(f"⏳ 等待 {actual_wait} 秒后发布下一篇...")
                time.sleep(actual_wait)

        logger.info(f"\n{'='*40}")
        logger.info(f"🏁 批量发帖完成: 成功 {success}, 失败 {failed}")
        logger.info(f"{'='*40}")

        return {"success": success, "failed": failed, "results": results}

    # ========== 核心发帖流程 ==========

    def _fill_and_publish(
        self,
        page: Page,
        content: str,
        title: str = "",
        image_paths: List[str] = None,
        topic: str = DEFAULT_TOPIC,
        dry_run: bool = False,
    ) -> bool:
        """在同一页面上填入内容并发布"""

        # 干跑模式下先清空上一篇残留内容
        if dry_run:
            self._clear_form(page)

        # 填入标题
        if title:
            self._fill_title(page, title)

        # 填入正文
        self._fill_content(page, content)

        # 添加话题
        if topic:
            self._add_topic(page, topic)

        # 上传图片
        if image_paths:
            self._upload_images(page, image_paths)

        # 截图预览
        self._save_screenshot(page, f"maimai_before_post_{int(time.time())}")

        if dry_run:
            logger.info("🔍 干跑模式：内容已填入，但不点击发布")
            return True

        # 点击"发动态"
        result = self._click_publish(page)

        # 发布后导航回首页（发布后页面会跳转到帖子详情，需要回到发帖页）
        time.sleep(2)
        logger.info("  导航回社区首页，准备下一篇...")
        try:
            page.goto(MAIMAI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
        except Exception:
            logger.warning("  ⚠️ 导航回首页失败，下一篇会自动重试")

        return result

    # ========== 内部方法 ==========

    def _open_post_page(self) -> Page:
        """打开发帖页 —— 确保编辑器存在，不存在则导航到社区首页"""
        logger.info("打开脉脉社区发帖页...")

        # 找已有的脉脉标签页
        for pg in self._context.pages:
            try:
                if "maimai.cn" in pg.url and not pg.is_closed():
                    self._page = pg
                    # 验证编辑器是否存在（标题输入框 + 正文 contenteditable）
                    editor_ok = pg.evaluate('''() => {
                        const titleInput = document.querySelector('input[placeholder*="标题"]');
                        const contentEditor = document.querySelector('[contenteditable="true"]');
                        const rect1 = titleInput ? titleInput.getBoundingClientRect() : null;
                        const rect2 = contentEditor ? contentEditor.getBoundingClientRect() : null;
                        return titleInput && contentEditor
                            && rect1 && rect1.width > 50
                            && rect2 && rect2.width > 50;
                    }''')
                    if editor_ok:
                        logger.success("✓ 发帖页已打开（复用现有标签页）")
                        return pg
                    else:
                        logger.info("  现有标签页无编辑器，导航到社区首页...")
                        pg.goto(MAIMAI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
                        time.sleep(3)
                        # 再次检查
                        editor_ok2 = pg.evaluate('''() => {
                            const titleInput = document.querySelector('input[placeholder*="标题"]');
                            const contentEditor = document.querySelector('[contenteditable="true"]');
                            return titleInput && contentEditor
                                && titleInput.getBoundingClientRect().width > 50
                                && contentEditor.getBoundingClientRect().width > 50;
                        }''')
                        if editor_ok2:
                            logger.success("✓ 发帖页已打开（导航后编辑器就绪）")
                            return pg
            except Exception:
                continue

        # 新建页面
        page = self._context.new_page()
        page.goto(MAIMAI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
        time.sleep(5)
        self._page = page

        if "signin" in page.url:
            raise RuntimeError("未登录脉脉，请先登录")

        logger.success("✓ 发帖页已打开")
        return page

    def _switch_identity(self, page: Page):
        """确保身份为'职场领域创作者'"""
        logger.info("检查发帖身份...")

        # 检查当前身份文本是否包含"职场领域创作者"
        current = page.evaluate('''() => {
            const all = document.querySelectorAll('span, div');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                // 找包含"职场领域创作者"且不太大的文本元素（精确匹配身份标签）
                if (t.includes('职场领域创作者') && t.length < 30
                    && rect.width > 50 && rect.width < 300
                    && rect.y > 80 && rect.y < 200) {
                    return t.substring(0, 30);
                }
            }
            return '';
        }''')

        if '职场领域创作者' in current:
            logger.info("  ✓ 身份已是职场领域创作者")
            return

        logger.info("  切换身份为职场领域创作者...")

        # 点击"切换"文字（精确匹配span text==="切换"，class含text-primary）
        clicked_switch = page.evaluate('''() => {
            const all = document.querySelectorAll('span, a, div');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                const cls = (el.className || '').toString();
                if (t === '切换' && rect.y > 80 && rect.y < 200
                    && rect.width > 10 && rect.width < 80) {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')

        if not clicked_switch:
            logger.warning("  ⚠️ 未找到切换按钮")
            return

        time.sleep(2)

        # 选择"职场领域创作者"
        selected = page.evaluate('''() => {
            const all = document.querySelectorAll('span, div, li, p');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                if (t === '职场领域创作者' && el.children.length === 0
                    && rect.width > 50 && rect.width < 300) {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')

        if selected:
            logger.success("  ✓ 已切换为职场领域创作者")
        else:
            logger.warning("  ⚠️ 未找到职场领域创作者选项")
        time.sleep(1)

    def _fill_title(self, page: Page, title: str):
        """填入标题"""
        logger.info(f"填入标题: {title[:20]}...")
        title = title[:20]

        # 先清空已有内容
        title_input = page.locator('input[placeholder*="标题"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill("")  # Playwright fill 会自动清空
            title_input.first.fill(title)
            logger.success(f"  ✓ 标题已填入: {title}")
        else:
            # 用 JS 方式
            filled = page.evaluate('''(title) => {
                const inputs = document.querySelectorAll('input');
                for (const input of inputs) {
                    const ph = (input.placeholder || '') + (input.getAttribute('aria-label') || '');
                    if (ph.includes('标题')) {
                        // 用 nativeInputValueSetter 确保 React 感知
                        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        nativeInputValueSetter.call(input, title);
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }''', title)
            if filled:
                logger.success(f"  ✓ 标题已填入: {title}")
            else:
                logger.warning("  ⚠️ 未找到标题输入框（标题为选填，继续）")

        time.sleep(0.5)

    def _clear_form(self, page: Page):
        """清空发帖表单（干跑模式下防止残留内容影响下一篇）"""
        logger.debug("清空表单残留内容...")

        # 清空标题
        title_input = page.locator('input[placeholder*="标题"]')
        if title_input.count() > 0:
            title_input.first.fill("")

        # 清空正文 contenteditable
        editor = page.locator('[contenteditable="true"]')
        if editor.count() > 0:
            editor.first.click()
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            time.sleep(0.3)

        # 清空已添加的话题标签（找到话题标签旁边的 × 按钮并点击）
        page.evaluate('''() => {
            // 话题标签通常有 "×" 关闭按钮，或者直接删除话题容器
            const closeButtons = document.querySelectorAll('svg, button, div');
            for (const btn of closeButtons) {
                const rect = btn.getBoundingClientRect();
                const parent = btn.closest('[class*="cursor-pointer"]');
                // 找话题标签区的小×按钮
                if (rect.width > 0 && rect.width < 25 && rect.height > 0 && rect.height < 25
                    && rect.y > 250 && rect.y < 320) {
                    const svg = btn.querySelector('svg');
                    if (svg && (btn.getAttribute('aria-label')?.includes('关闭')
                        || btn.getAttribute('aria-label')?.includes('close')
                        || (btn.textContent || '').trim() === '×')) {
                        btn.click();
                    }
                }
            }
        }''')

        time.sleep(0.3)

    def _fill_content(self, page: Page, content: str):
        """填入正文"""
        logger.info(f"填入正文: {len(content)} 字")
        content = content[:1000]

        # 先清空已有内容
        # 策略1：textarea
        textarea = page.locator('textarea[placeholder*="想法"], textarea[placeholder*="分享"]')
        if textarea.count() > 0:
            textarea.first.click()
            # 全选并删除已有内容
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            time.sleep(0.2)
            textarea.first.fill(content)
            logger.success(f"  ✓ 正文已填入 (textarea)")
            time.sleep(0.5)
            return

        # 策略2：contenteditable
        editor = page.locator('[contenteditable="true"]')
        if editor.count() > 0:
            editor.first.click()
            # 全选删除已有内容
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            time.sleep(0.2)
            page.keyboard.type(content, delay=10)
            logger.success(f"  ✓ 正文已填入 (contenteditable)")
            time.sleep(0.5)
            return

        raise RuntimeError("未找到正文输入框")

    def _add_topic(self, page: Page, topic: str):
        """
        添加话题 —— 正确流程：
          1. 点击工具栏中的「添加话题」按钮（class 含 cursor-pointer 的那个，不是容器）
          2. 等待弹出面板出现，面板内有搜索输入框
          3. 在搜索框中输入话题名称
          4. 点击搜索结果中的第一个匹配项
        """
        logger.info(f"添加话题: {topic}")

        # 1. 点击「添加话题」按钮
        #    关键：要点击 class 含 cursor-pointer 的小按钮，不是整个工具栏容器
        clicked = page.evaluate('''() => {
            const all = document.querySelectorAll('div, span, label');
            let best = null;
            let bestArea = Infinity;

            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                const cls = (el.className || '').toString();
                const area = rect.width * rect.height;

                // 匹配条件：文字含"添加话题"，在工具栏区域(y>250)，有 cursor-pointer
                if (t.includes('添加话题') && rect.y > 250 && rect.width > 0
                    && cls.includes('cursor-pointer')) {
                    // 选最小的匹配元素（最精确的按钮）
                    if (area < bestArea) {
                        bestArea = area;
                        best = el;
                    }
                }
            }

            if (best) {
                best.click();
                return best.textContent.trim();
            }

            // 备用：如果没有 cursor-pointer，选最小且文字精确匹配的
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                if ((t === '添加话题' || t === '# 添加话题') && rect.y > 250 && rect.width < 150) {
                    el.click();
                    return t;
                }
            }

            return false;
        }''')

        if not clicked:
            logger.warning("  ⚠️ 未找到'添加话题'按钮")
            return

        logger.info(f"  已点击添加话题按钮: {clicked}")
        time.sleep(2)

        # 2. 在弹出面板的搜索框中输入话题名称
        #    弹出面板的搜索框：y > 250 的 input[type=search]（排除顶部导航栏 y < 100）
        popup_search = None
        for inp in page.locator('input[type="search"], input[type="text"]').all():
            try:
                box = inp.bounding_box()
                if box and box['y'] > 250 and box['width'] > 50:
                    popup_search = inp
                    break
            except Exception:
                continue

        if popup_search:
            popup_search.click()
            time.sleep(0.3)
            popup_search.fill(topic)
            logger.info(f"  已在弹出搜索框输入: {topic}")
        else:
            logger.warning("  ⚠️ 未找到弹出面板的搜索框")
            return

        time.sleep(2)

        # 3. 点击搜索结果
        #    优先选择话题名在开头、无前缀的行（最精确）
        selected = page.evaluate('''(topic) => {
            const all = document.querySelectorAll('div');
            let exactRow = null;       // 话题名在文本开头的行
            let exactLen = Infinity;
            let prefixRow = null;      // 话题名在文本中间的行（有前缀如"互联网新鲜事，"）
            let prefixLen = Infinity;

            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                const cls = (el.className || '').toString();

                if (!cls.includes('cursor-pointer') || rect.y < 300 || rect.height < 30 || rect.height > 60) continue;
                if (!t.includes(topic)) continue;

                if (t.startsWith(topic)) {
                    if (t.length < exactLen) { exactLen = t.length; exactRow = el; }
                } else {
                    if (t.length < prefixLen) { prefixLen = t.length; prefixRow = el; }
                }
            }

            const target = exactRow || prefixRow;
            if (target) {
                target.click();
                return { match: exactRow ? 'exact' : 'prefix', text: target.textContent.trim().substring(0, 30) };
            }
            return null;
        }''', topic)

        if selected:
            logger.success(f"  ✓ 话题已点击: {selected.get('text', topic)}")
        else:
            logger.warning(f"  ⚠️ 未找到话题搜索结果: {topic}，可能需要手动选择")

        # 等待话题添加生效，弹窗关闭
        time.sleep(2)

        # 确保弹窗关闭（按 Escape 关闭可能残留的搜索面板）
        page.keyboard.press("Escape")
        time.sleep(1)

    def _upload_images(self, page: Page, image_paths: List[str]):
        """
        上传图片 —— 直接通过 #picture file input 上传（无需先点图标）
        """
        logger.info(f"上传图片: {len(image_paths)} 张")

        try:
            # 直接用 #picture file input 上传
            picture_input = page.locator('#picture')
            if picture_input.count() > 0:
                picture_input.set_input_files(image_paths)
                logger.info(f"  ✓ 上传 {len(image_paths)} 张图片成功 (#picture)")
                time.sleep(3)
            else:
                # 备用：找其他 file input
                image_input = page.locator('input[type="file"][accept*="image"]')
                if image_input.count() > 0:
                    image_input.first.set_input_files(image_paths)
                    logger.info(f"  ✓ 上传 {len(image_paths)} 张图片成功 (file input)")
                    time.sleep(3)
                else:
                    logger.warning("  ⚠️ 未找到图片上传 file input")
        except Exception as e:
            logger.warning(f"  ⚠️ 图片上传异常: {e}")

        logger.success("✓ 图片上传完成")

    def _click_publish(self, page: Page) -> bool:
        """点击'发动态'按钮"""
        logger.info("⚠️  点击'发动态'按钮...")

        # 先确保没有弹窗挡着（按 Escape 关闭任何残留面板）
        page.keyboard.press("Escape")
        time.sleep(1)

        # 点击"发动态"按钮
        # 优先点击 <button> 元素（最可靠），确保按钮可见且可点击
        clicked = page.evaluate('''() => {
            // 优先 button
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const t = (btn.textContent || '').trim();
                const rect = btn.getBoundingClientRect();
                if ((t === '发动态' || t === '发布') && rect.width > 0 && !btn.disabled) {
                    btn.click();
                    return { tag: 'button', text: t };
                }
            }
            // 备用
            const all = document.querySelectorAll('div, span');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                if ((t === '发动态' || t === '发布') && rect.width > 50 && rect.y > 200) {
                    el.click();
                    return { tag: el.tagName, text: t };
                }
            }
            return null;
        }''')

        if not clicked:
            raise RuntimeError("未找到'发动态'按钮")

        logger.info(f"  ✓ 已点击: {clicked.get('tag')}.{clicked.get('text')}")

        # 等待发布完成 — 页面可能跳转到帖子详情页
        time.sleep(5)

        # 截图验证发布结果
        self._save_screenshot(page, f"maimai_after_post_{int(time.time())}")
        logger.success("✓ 发帖完成")
        return True

    # ========== 截图工具 ==========

    def _save_screenshot(self, page: Page, name: str):
        """保存调试截图"""
        try:
            debug_dir = PROJECT_ROOT / "debug_screenshots"
            debug_dir.mkdir(exist_ok=True)
            page.screenshot(path=str(debug_dir / f"{name}.png"), full_page=True)
            logger.debug(f"  截图已保存: {name}.png")
        except Exception:
            pass
