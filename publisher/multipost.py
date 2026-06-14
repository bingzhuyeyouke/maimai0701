"""
MultiPost 发布模块 —— 自动操作 MultiPost 网页端发布内容到多平台

完整流程：
  1. 连接到用户已打开的 Chrome（带远程调试端口 9222）
  2. 打开 MultiPost 编辑器（multipost.app）
  3. 上传图片
  4. 填入标题和正文
  5. 点击「下一步」（蓝色箭头按钮）
  6. 取消全选，勾选目标平台（头条/公众号）
  7. 点击发布按钮
  8. 检测新打开的平台标签页
  9. 在各平台标签页中填入标题、正文、分类，点击各平台发布按钮

⚠️  前置条件：
  - 用户需要先启动 Chrome 并打开远程调试端口：
    /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
      --remote-debugging-port=9222 \
      --user-data-dir=/tmp/chrome-automation-profile
  - 用户需要已登录 MultiPost（multipost.app）
  - 用户需要已登录各目标平台（头条/公众号）

⚠️  风险提示：
  - 发布是真实操作，会在平台上创建真实内容
  - 建议先用测试内容验证流程，确认无误后再用正式内容
  - 不要短时间内大量发布，可能触发平台风控
"""

import time
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from loguru import logger

from config import settings, PROJECT_ROOT


# ========== 常量 ==========

# Chrome 远程调试地址
CDP_URL = "http://localhost:9222"

# MultiPost 编辑器地址
MULTIPOST_URL = "https://multipost.app/"

# 默认要发布的平台
DEFAULT_PLATFORMS = ["微信公众号", "今日头条"]

# MultiPost 扩展 ID
MULTIPOST_EXT_ID = "dhohkaclnjgcikfoaacfgijgjgceofih"


class MultiPostPublisher:
    """
    MultiPost 发布器

    用法：
        publisher = MultiPostPublisher()
        publisher.connect()
        publisher.publish(title="标题", body="正文", platforms=["今日头条", "微信公众号"])
        publisher.disconnect()
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def connect(self) -> bool:
        """
        连接到用户已启动的 Chrome 浏览器

        返回:
            True 连接成功，False 连接失败
        """
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
            logger.info("请先启动 Chrome（带调试端口）：")
            logger.info(
                "  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome "
                "--remote-debugging-port=9222 "
                "--user-data-dir=/tmp/chrome-automation-profile"
            )
            return False

    def disconnect(self):
        """断开连接（不关闭用户的 Chrome）"""
        # 不关闭 browser，因为是用户的 Chrome
        if self._playwright:
            self._playwright.stop()
        logger.info("已断开 Chrome 连接")

    def publish(
        self,
        title: str,
        body: str,
        platforms: list[str] = None,
        image_paths: list[str] = None,
        dry_run: bool = False,
    ) -> bool:
        """
        发布内容到 MultiPost

        参数:
            title:       文章标题
            body:        文章正文
            platforms:   目标平台列表，默认 ["微信公众号", "今日头条"]
            image_paths: 要上传的本地图片路径列表
            dry_run:     干跑模式——只填内容选平台，不点最终发布按钮

        返回:
            True 发布成功，False 失败
        """
        if platforms is None:
            platforms = DEFAULT_PLATFORMS

        try:
            # 第1步：打开 MultiPost 编辑器
            page = self._open_editor()

            # 第2步：上传图片（在填文字之前，因为上传后光标位置更可控）
            if image_paths:
                self._upload_images(page, image_paths)

            # 第3步：填入标题和正文
            self._fill_content(page, title, body)

            # 第4步：点击「下一步」
            self._click_next(page)

            # 第5步：先取消所有已勾选平台，再勾选目标平台
            self._deselect_all_platforms(page)
            self._select_platforms(page, platforms)

            if dry_run:
                logger.info("🔍 干跑模式：内容已填入，平台已选择，但不点击发布")
                page.screenshot(path="debug_screenshots/dry_run_preview.png", full_page=True)
                return True

            # 第6步：点击发布 + 处理平台标签页
            result = self._click_publish(page, title, body)

            return result

        except Exception as e:
            logger.error(f"❌ 发布失败: {e}")
            return False

    def _open_editor(self) -> Page:
        """第1步：打开或切换到 MultiPost 编辑器（确保在编辑状态，不是平台选择页）"""
        logger.info("打开 MultiPost 编辑器...")

        # 先看看是否已经打开了 multipost 页面
        found_page = None
        for pg in self._context.pages:
            if "multipost.app" in pg.url and "signin" not in pg.url:
                found_page = pg
                break

        if found_page:
            # 已有页面，刷新回到编辑器初始状态
            logger.info("  刷新页面回到编辑器...")
            found_page.goto(MULTIPOST_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            self._page = found_page
        else:
            # 没有就新建
            page = self._context.new_page()
            page.goto(MULTIPOST_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            self._page = page
            found_page = page

        # 检查是否被重定向到登录页
        if "signin" in found_page.url:
            raise RuntimeError("未登录 MultiPost，请先在 Chrome 中登录")

        # 验证编辑器是否就绪（检查是否有正文输入框）
        textarea = found_page.locator('textarea[placeholder*="内容"]')
        if textarea.count() == 0:
            logger.warning("  编辑器未就绪，再次刷新...")
            found_page.reload(wait_until="domcontentloaded", timeout=10000)
            time.sleep(3)

        logger.success("✓ 编辑器已打开")
        return found_page

    def _fill_content(self, page: Page, title: str, body: str):
        """第2步：填入标题和正文"""
        logger.info("填入内容...")

        # 填标题
        title_input = page.locator('input[placeholder*="标题"]')
        if title_input.count() > 0:
            title_input.click()
            title_input.fill(title)
            logger.info(f"  标题: {title}")
        else:
            logger.warning("  未找到标题输入框")

        time.sleep(0.5)

        # 填正文
        textarea = page.locator('textarea[placeholder*="内容"]')
        if textarea.count() > 0:
            textarea.click()
            textarea.fill(body)
            logger.info(f"  正文: {body[:50]}...")
        else:
            raise RuntimeError("未找到正文输入框")

        time.sleep(1)
        logger.success("✓ 内容已填入")

    def _click_next(self, page: Page):
        """第3步：点击蓝色「下一步」按钮"""
        logger.info("点击「下一步」...")

        # 找蓝色按钮（background-color: rgb(0, 111, 238)）
        clicked = page.evaluate('''() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const style = getComputedStyle(btn);
                if (style.backgroundColor.includes('0, 111, 238')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 100) {  // 确保是主按钮，不是小图标
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        }''')

        if not clicked:
            raise RuntimeError("未找到「下一步」按钮")

        time.sleep(3)  # 等待平台选择页加载
        logger.success("✓ 已进入平台选择页")

    def _select_platforms(self, page: Page, platforms: list[str]):
        """第5步：选择目标平台（先取消全选再勾选目标）"""
        logger.info(f"选择平台: {platforms}")

        # 先取消所有已勾选的平台
        self._deselect_all_platforms(page)

        for platform_name in platforms:
            result = self._select_single_platform(page, platform_name)
            if result:
                logger.info(f"  ✓ 已选择: {platform_name}")
            else:
                logger.warning(f"  ⚠️ 未找到或已选择: {platform_name}")
            time.sleep(0.5)

        logger.success(f"✓ 平台选择完成")

    def _deselect_all_platforms(self, page: Page):
        """取消所有已勾选的平台"""
        logger.info("取消所有已勾选的平台...")

        # 取消热门列表中已勾选的
        page.evaluate('''() => {
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            let unchecked = 0;
            for (const cb of checkboxes) {
                if (cb.checked) {
                    cb.click();
                    unchecked++;
                }
            }
            return unchecked;
        }''')

        # 也检查「其他」分类下是否有已勾选的
        page.evaluate('''() => {
            const all = document.querySelectorAll('button, span, a, div');
            for (const el of all) {
                if (el.textContent.trim() === '其他') {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')
        time.sleep(1)

        page.evaluate('''() => {
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of checkboxes) {
                if (cb.checked) {
                    cb.click();
                }
            }
        }''')

        logger.info("  ✓ 已取消所有平台勾选")

    def _upload_images(self, page: Page, image_paths: list[str]):
        """上传图片到 MultiPost 编辑器

        交互流程：
          1. 点击编辑器下方的「上传图片」卡片按钮
          2. 按钮点击后会激活隐藏的 <input type="file" accept="image/*">
          3. 通过该 input 上传本地图片文件
        """
        logger.info(f"上传图片: {len(image_paths)} 张")

        # 先点击「上传图片」按钮，激活 file input
        logger.info("  点击「上传图片」按钮...")
        clicked = page.evaluate('''() => {
            // 找包含「上传图片」文字的卡片/按钮
            const all = document.querySelectorAll('div, button, span');
            for (const el of all) {
                const text = (el.textContent || '').trim();
                // 精确匹配「上传图片」文字的卡片
                if (text === '上传图片' || text.startsWith('上传图片')) {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')

        if not clicked:
            logger.warning("  ⚠️ 未找到「上传图片」按钮")
        else:
            logger.info("  ✓ 已点击「上传图片」按钮")

        time.sleep(1)

        # 通过激活的 file input 上传图片
        image_input = page.locator('input[type="file"][accept*="image"]')

        if image_input.count() > 0:
            # 一次性上传所有图片（input 支持 multiple）
            image_input.set_input_files(image_paths)
            logger.info(f"  ✓ 已上传 {len(image_paths)} 张图片")
            time.sleep(3)  # 等待上传完成
        else:
            logger.warning("  ⚠️ 未找到图片 file input，尝试逐张上传...")
            for i, img_path in enumerate(image_paths, 1):
                # 再次点击上传按钮
                page.evaluate('''() => {
                    const all = document.querySelectorAll('div, button, span');
                    for (const el of all) {
                        if ((el.textContent || '').trim().startsWith('上传图片')) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }''')
                time.sleep(1)

                image_input = page.locator('input[type="file"][accept*="image"]')
                if image_input.count() > 0:
                    image_input.set_input_files(img_path)
                    logger.info(f"    ✓ 图片 {i}/{len(image_paths)} 上传成功")
                    time.sleep(2)
                else:
                    logger.warning(f"    ⚠️ 图片 {i} 上传失败：未找到 file input")

        logger.success(f"✓ 图片上传完成")

    def _select_single_platform(self, page: Page, platform_name: str) -> bool:
        """
        选择单个平台

        策略：
          1. 先在热门列表里找
          2. 找不到就点「其他」展开，再找
          3. 找到后勾选 checkbox
        """
        result = page.evaluate('''(platformName) => {
            // 找到包含目标平台名的行
            const rows = document.querySelectorAll('div.flex.items-center.rounded-lg.p-2');
            for (const row of rows) {
                const text = (row.textContent || '').trim();
                if (text.includes(platformName) && text.length < 30) {
                    const checkbox = row.querySelector('input[type="checkbox"]');
                    if (checkbox && !checkbox.checked) {
                        checkbox.click();
                        return { found: true, clicked: true };
                    } else if (checkbox && checkbox.checked) {
                        return { found: true, clicked: false, reason: 'already checked' };
                    }
                }
            }
            return { found: false };
        }''', platform_name)

        if result.get('found'):
            return True

        # 热门列表里没找到，尝试展开「其他」
        logger.info(f"  {platform_name} 不在热门列表，尝试展开「其他」...")
        page.evaluate('''() => {
            const all = document.querySelectorAll('button, span, a, div');
            for (const el of all) {
                if (el.textContent.trim() === '其他') {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')
        time.sleep(2)

        # 再试一次
        result = page.evaluate('''(platformName) => {
            const rows = document.querySelectorAll('div.flex.items-center.rounded-lg.p-2');
            for (const row of rows) {
                const text = (row.textContent || '').trim();
                if (text.includes(platformName) && text.length < 30) {
                    const checkbox = row.querySelector('input[type="checkbox"]');
                    if (checkbox && !checkbox.checked) {
                        checkbox.click();
                        return { found: true, clicked: true };
                    } else if (checkbox && checkbox.checked) {
                        return { found: true, clicked: false, reason: 'already checked' };
                    }
                }
            }
            return { found: false };
        }''', platform_name)

        return result.get('found', False)

    def _click_publish(self, page: Page, title: str, body: str) -> bool:
        """第6步：点击 MultiPost 发布按钮，然后处理各平台标签页"""
        logger.info("⚠️  即将点击发布按钮，这是真实发布操作！")

        # 记录当前页面数，用于检测新标签页
        pages_before = len(self._context.pages)

        # 点击蓝色发布按钮
        clicked = page.evaluate('''() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const style = getComputedStyle(btn);
                if (style.backgroundColor.includes('0, 111, 238')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 100) {
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        }''')

        if not clicked:
            raise RuntimeError("未找到发布按钮")

        logger.info("  ✓ MultiPost 发布按钮已点击")
        time.sleep(5)

        # 检测新打开的平台标签页
        platform_tabs = self._wait_for_platform_tabs(pages_before)

        if not platform_tabs:
            # 没有新标签页，检查 MultiPost 页面状态
            result = page.evaluate('''() => {
                const text = document.body.textContent || '';
                if (text.includes('publish.success') || text.includes('success')) {
                    return 'success';
                } else if (text.includes('error') || text.includes('失败')) {
                    return 'error';
                }
                return 'unknown';
            }''')

            if result == 'success':
                logger.success("🎉 MultiPost 发布成功（未打开平台标签页）")
                return True
            elif result == 'error':
                logger.error("❌ MultiPost 发布失败")
                return False
            else:
                logger.warning("⚠️  发布结果未知，未检测到平台标签页")
                self._save_screenshot(page, "multipost_after_publish")
                return True

        # 逐个处理平台标签页
        results = {}
        for tab in platform_tabs:
            platform = self._identify_platform(tab)
            logger.info(f"  处理平台: {platform}")

            if platform == 'toutiao':
                results['toutiao'] = self._publish_toutiao(tab, title, body)
            elif platform == 'wechat':
                results['wechat'] = self._publish_wechat(tab, title, body)
            else:
                logger.warning(f"  未知平台，跳过: {tab.url}")
                results['unknown'] = False

        # 汇总结果
        for platform, success in results.items():
            status = "✅ 成功" if success else "❌ 失败"
            logger.info(f"  {platform}: {status}")

        known_results = {k: v for k, v in results.items() if k != 'unknown'}
        if not known_results:
            return False

        all_success = all(known_results.values())
        if all_success:
            logger.success("🎉 所有平台发布成功！")
        return all_success

    # ========== 平台标签页检测与交互 ==========

    def _wait_for_platform_tabs(self, existing_count: int, timeout: int = 15) -> list:
        """
        等待 MultiPost 打开平台标签页

        参数:
            existing_count: 点击发布前的页面数
            timeout: 最长等待秒数

        返回:
            新打开的 Page 列表
        """
        logger.info("等待平台标签页打开...")
        start = time.time()
        while time.time() - start < timeout:
            current_pages = self._context.pages
            if len(current_pages) > existing_count:
                new_pages = current_pages[existing_count:]
                logger.info(f"  ✓ 检测到 {len(new_pages)} 个新标签页")
                return new_pages
            time.sleep(1)

        logger.warning("  ⚠️ 未检测到新标签页")
        return []

    def _identify_platform(self, page: Page) -> str:
        """
        根据 URL 识别平台

        返回:
            'toutiao' / 'wechat' / 'unknown'
        """
        url = page.url
        logger.debug(f"  标签页 URL: {url}")

        if 'mp.toutiao.com' in url or 'toutiao.com' in url:
            return 'toutiao'
        elif 'mp.weixin.qq.com' in url or 'weixin.qq.com' in url:
            return 'wechat'

        # 等待页面跳转完成后再次检查
        time.sleep(3)
        url = page.url
        logger.debug(f"  标签页 URL (等待后): {url}")

        if 'mp.toutiao.com' in url or 'toutiao.com' in url:
            return 'toutiao'
        elif 'mp.weixin.qq.com' in url or 'weixin.qq.com' in url:
            return 'wechat'

        # 未知平台，保存截图用于调试
        self._save_screenshot(page, "unknown_platform_tab")
        logger.warning(f"  未知平台: {url}")
        return 'unknown'

    def _publish_toutiao(self, page: Page, title: str, content: str) -> bool:
        """
        在今日头条发布页面填写标题、正文并发布

        返回:
            True 发布成功
        """
        logger.info("📝 今日头条：填写内容并发布")

        # 等待页面加载
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            logger.warning("  今日头条页面加载超时")
        time.sleep(3)

        self._save_screenshot(page, "toutiao_before_fill")

        # 填标题
        title_filled = self._fill_platform_title(page, title, "toutiao")
        if not title_filled:
            logger.error("  ❌ 今日头条：标题填写失败")
            self._save_screenshot(page, "toutiao_title_fail")
            return False

        time.sleep(1)

        # 填正文
        content_filled = self._fill_platform_content(page, content, "toutiao")
        if not content_filled:
            logger.error("  ❌ 今日头条：正文填写失败")
            self._save_screenshot(page, "toutiao_content_fail")
            return False

        time.sleep(1)

        # TODO: 分类标签选择（需要根据实际页面元素确定选择器）
        logger.info("  分类标签：暂未实现，跳过")

        self._save_screenshot(page, "toutiao_before_publish")

        # 点击发布按钮
        publish_clicked = page.evaluate('''() => {
            const btns = document.querySelectorAll('button, a, span');
            for (const btn of btns) {
                const text = (btn.textContent || '').trim();
                if (text === '发布' || text === '发表' || text === '提交') {
                    btn.click();
                    return text;
                }
            }
            return false;
        }''')

        if not publish_clicked:
            logger.warning("  ⚠️ 今日头条：未找到发布按钮")
            self._save_screenshot(page, "toutiao_publish_btn_fail")
            return False

        logger.info(f"  ✓ 今日头条发布按钮已点击: {publish_clicked}")
        time.sleep(3)

        self._save_screenshot(page, "toutiao_after_publish")
        logger.success("✓ 今日头条发布完成")
        return True

    def _publish_wechat(self, page: Page, title: str, content: str) -> bool:
        """
        在微信公众号发布页面填写标题、正文并发布

        返回:
            True 发布成功
        """
        logger.info("📝 微信公众号：填写内容并发布")

        # 等待页面加载
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            logger.warning("  微信公众号页面加载超时")
        time.sleep(3)

        self._save_screenshot(page, "wechat_before_fill")

        # 填标题
        title_filled = self._fill_platform_title(page, title, "wechat")
        if not title_filled:
            logger.error("  ❌ 微信公众号：标题填写失败")
            self._save_screenshot(page, "wechat_title_fail")
            return False

        time.sleep(1)

        # 填正文（微信公众号编辑器可能在 iframe 中）
        content_filled = self._fill_wechat_content(page, content)
        if not content_filled:
            logger.error("  ❌ 微信公众号：正文填写失败")
            self._save_screenshot(page, "wechat_content_fail")
            return False

        time.sleep(1)

        # TODO: 分类标签选择
        logger.info("  分类标签：暂未实现，跳过")

        self._save_screenshot(page, "wechat_before_publish")

        # 点击发布按钮（微信公众号通常是「群发」或「发布」）
        publish_clicked = page.evaluate('''() => {
            const btns = document.querySelectorAll('button, a, span');
            for (const btn of btns) {
                const text = (btn.textContent || '').trim();
                if (text === '群发' || text === '发布' || text === '保存并群发') {
                    btn.click();
                    return text;
                }
            }
            return false;
        }''')

        if not publish_clicked:
            logger.warning("  ⚠️ 微信公众号：未找到发布按钮")
            self._save_screenshot(page, "wechat_publish_btn_fail")
            return False

        logger.info(f"  ✓ 微信公众号按钮已点击: {publish_clicked}")
        time.sleep(2)

        # 处理确认弹窗
        confirm_clicked = page.evaluate('''() => {
            const btns = document.querySelectorAll('button, a, span');
            for (const btn of btns) {
                const text = (btn.textContent || '').trim();
                if (text === '确定' || text === '确认群发' || text === '确认') {
                    btn.click();
                    return text;
                }
            }
            return false;
        }''')

        if confirm_clicked:
            logger.info(f"  ✓ 确认弹窗已点击: {confirm_clicked}")
            time.sleep(2)

        self._save_screenshot(page, "wechat_after_publish")
        logger.success("✓ 微信公众号发布完成")
        return True

    # ========== 平台通用填写方法 ==========

    def _fill_platform_title(self, page: Page, title: str, platform: str) -> bool:
        """在平台发布页面填写标题"""
        # 策略1：通过 placeholder 定位标题输入框
        title_input = page.locator('input[placeholder*="标题"], input[placeholder*="请输入"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill(title)
            logger.info(f"  标题已填写: {title[:30]}...")
            return True

        # 策略2：通过 id 或常见选择器
        title_input = page.locator('#title, input[name="title"], input[type="text"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill(title)
            logger.info(f"  标题已填写: {title[:30]}...")
            return True

        # 策略3：JS evaluate 兜底
        filled = page.evaluate('''(title) => {
            const inputs = document.querySelectorAll('input[type="text"]');
            for (const input of inputs) {
                const ph = (input.placeholder || '') + (input.getAttribute('aria-label') || '');
                if (ph.includes('标题') || ph.includes('请输入') || ph === '') {
                    input.focus();
                    input.value = title;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }''', title)

        if filled:
            logger.info(f"  标题已填写 (JS): {title[:30]}...")
        return filled

    def _fill_platform_content(self, page: Page, content: str, platform: str) -> bool:
        """在平台发布页面填写正文（通用方法，非微信）"""
        # 策略1：contenteditable div（今日头条常用）
        content_editor = page.locator('div[contenteditable="true"]')
        if content_editor.count() > 0:
            content_editor.first.click()
            page.keyboard.type(content, delay=5)
            logger.info(f"  正文已填写 (contenteditable): {len(content)} 字")
            return True

        # 策略2：textarea
        textarea = page.locator('textarea[placeholder*="正文"], textarea[placeholder*="内容"], textarea')
        if textarea.count() > 0:
            textarea.first.click()
            textarea.first.fill(content)
            logger.info(f"  正文已填写 (textarea): {len(content)} 字")
            return True

        # 策略3：role="textbox"
        textbox = page.locator('[role="textbox"]')
        if textbox.count() > 0:
            textbox.first.click()
            page.keyboard.type(content, delay=5)
            logger.info(f"  正文已填写 (textbox): {len(content)} 字")
            return True

        # 策略4：JS evaluate 兜底
        filled = page.evaluate('''(content) => {
            const editors = document.querySelectorAll(
                'div[contenteditable="true"], textarea, [role="textbox"]'
            );
            for (const editor of editors) {
                if (editor.tagName === 'TEXTAREA') {
                    editor.value = content;
                } else {
                    editor.innerHTML = content;
                }
                editor.dispatchEvent(new Event('input', { bubbles: true }));
                return true;
            }
            return false;
        }''', content)

        if filled:
            logger.info(f"  正文已填写 (JS): {len(content)} 字")
        return filled

    def _fill_wechat_content(self, page: Page, content: str) -> bool:
        """在微信公众号发布页面填写正文（处理 iframe 编辑器）"""
        # 策略1：iframe 编辑器（微信公众号常用）
        editor_frame = page.locator('iframe[id*="edui"], iframe[class*="editor"], iframe[src*="editor"]')
        if editor_frame.count() > 0:
            try:
                frame = editor_frame.first.content_frame()
                body = frame.locator('body[contenteditable="true"]')
                if body.count() > 0:
                    body.click()
                    page.keyboard.type(content, delay=5)
                    logger.info(f"  正文已填写 (iframe编辑器): {len(content)} 字")
                    return True
            except Exception as e:
                logger.warning(f"  iframe 编辑器填写失败: {e}")

        # 策略2：直接 contenteditable
        content_editor = page.locator('div[contenteditable="true"], [role="textbox"]')
        if content_editor.count() > 0:
            content_editor.first.click()
            page.keyboard.type(content, delay=5)
            logger.info(f"  正文已填写 (contenteditable): {len(content)} 字")
            return True

        # 策略3：JS evaluate 尝试访问 iframe
        filled = page.evaluate('''(content) => {
            // 尝试 iframe
            const iframes = document.querySelectorAll('iframe');
            for (const iframe of iframes) {
                try {
                    const body = iframe.contentDocument && iframe.contentDocument.body;
                    if (body && body.contentEditable === 'true') {
                        body.innerHTML = content;
                        return true;
                    }
                } catch (e) { /* 跨域 iframe */ }
            }
            // 尝试 contenteditable
            const editors = document.querySelectorAll('div[contenteditable="true"], [role="textbox"]');
            for (const editor of editors) {
                editor.focus();
                editor.innerHTML = content;
                editor.dispatchEvent(new Event('input', { bubbles: true }));
                return true;
            }
            return false;
        }''', content)

        if filled:
            logger.info(f"  正文已填写 (JS): {len(content)} 字")
        return filled

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
