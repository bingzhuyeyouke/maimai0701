"""
脉脉自定义帖子发布 —— 指定帖子URL的全流程

功能：
  1. 从用户提供的脉脉帖子详情页URL抓取内容（文字+图片）
  2. 去重判断（已抓取则跳过）
  3. 下载帖子图片
  4. AI全文改写（适配今日头条/公众号风格 + 合规打码）
  5. 图片合规处理（OCR检测公司名+水印打码）
  6. 自动发布到 MultiPost（今日头条 + 公众号）

与 auto_scrape_publish.py 的区别：
  - auto_scrape_publish.py：从话题页自动抓取小渔学姐最新帖子，仅做合规打码
  - custom_post_publish.py：从用户指定的帖子URL抓取，做AI全文改写+合规

触发方式：
  向 Claude 发送脉脉帖子详情页链接即可自动触发

用法：
  终端1：python3 start_chrome.py                    # 先启动 Chrome
  终端2：python3 custom_post_publish.py <post_url>   # 运行全流程
"""

import sys
import re
from loguru import logger

from config import settings, PROJECT_ROOT
from crawler.maimai import MaimaiScraper
from adapter.compliance import text_rewrite, image_compliance
from publisher.multipost import MultiPostPublisher


# ========== 配置 ==========

# 发布平台
PUBLISH_PLATFORMS = ["今日头条", "微信公众号"]

# 脉脉帖子详情页 URL 正则
# 匹配格式：
#   https://maimai.cn/community/gossip-detail/37006232
#   https://maimai.cn/n/content/gossip-detail/37007088?...
#   https://maimai.cn/community/topic-detail/12345678
MAIMAI_DETAIL_URL_PATTERN = re.compile(
    r'https?://maimai\.cn/(?:community|(?:n/content))/(gossip-detail|topic-detail)/\d+',
    re.IGNORECASE,
)


def is_maimai_post_url(text: str) -> bool:
    """判断文本是否包含脉脉帖子详情页URL"""
    return bool(MAIMAI_DETAIL_URL_PATTERN.search(text))


def extract_url(text: str) -> str:
    """从文本中提取脉脉帖子完整URL（保留查询参数）"""
    match = MAIMAI_DETAIL_URL_PATTERN.search(text)
    if match:
        # 找到匹配的结束位置，继续往后取查询参数
        end = match.end()
        rest = text[end:]
        # 如果紧跟 ? 则继续取到空格或字符串结尾
        if rest.startswith('?'):
            query_end = len(text)
            for sep in [' ', '\n', '\t', '"', "'"]:
                idx = rest.find(sep, 1)
                if idx > 0:
                    query_end = min(query_end, end + idx)
            return text[:query_end]
        return match.group(0)
    return text.strip()


# ========== 日志 ==========

def setup_logger():
    """配置日志"""
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
    )
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.add(
        str(log_dir / "custom_post_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{line} | {message}",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
    )


# ========== 主流程 ==========

def run(post_url: str, dry_run: bool = False) -> bool:
    """
    执行自定义帖子发布流程

    参数:
        post_url: 脉脉帖子详情页URL
        dry_run:  如果为 True，只走完抓取+改写+合规流程，不点击最终发布按钮

    返回:
        True 成功，False 失败或无新内容
    """
    # 提取并验证 URL
    post_url = extract_url(post_url)
    if not is_maimai_post_url(post_url):
        logger.error(f"不是有效的脉脉帖子链接: {post_url}")
        return False

    logger.info("=" * 55)
    logger.info("✍️ 自定义帖子发布流程启动")
    logger.info(f"   帖子URL: {post_url}")
    logger.info(f"   发布平台: {PUBLISH_PLATFORMS}")
    logger.info(f"   干跑模式: {dry_run}")
    logger.info("=" * 55)

    # ===== 第1步：从URL抓取帖子 =====
    logger.info("📥 第1步：从URL抓取帖子")
    scraper = MaimaiScraper()

    if not scraper.connect():
        logger.error("❌ 连接 Chrome 失败，请确保已启动 Chrome（python3 start_chrome.py）")
        return False

    try:
        post = scraper.fetch_post_by_url(post_url)
    except Exception as e:
        logger.error(f"❌ 抓取异常: {e}")
        scraper.disconnect()
        return False

    if not post:
        logger.warning("⚠️  未找到帖子内容")
        scraper.disconnect()
        return False

    logger.info(f"   帖子ID: {post['post_id']}")
    logger.info(f"   标题: {post['title']}")
    logger.info(f"   正文长度: {len(post['content'])} 字")
    logger.info(f"   图片数量: {len(post['images'])} 张")

    # ===== 第2步：去重判断 =====
    logger.info("🔍 第2步：去重判断")
    if scraper.is_duplicate(post["post_id"]):
        logger.info(f"⏭️  帖子 {post['post_id']} 已抓取过，跳过")
        scraper.disconnect()
        return False

    logger.success("✓ 新帖子，继续处理")

    # ===== 第3步：下载图片 =====
    logger.info("🖼️  第3步：下载图片")
    local_paths = scraper.download_images(post)
    if not local_paths and post["images"]:
        logger.warning("⚠️  有图片但全部下载失败")

    # 保存帖子记录
    scraper.save_post(post)
    scraper.disconnect()

    # ===== 第4步：AI全文改写 =====
    logger.info("✍️ 第4步：AI全文改写（头条/公众号风格 + 合规）")

    new_title, new_body = text_rewrite(
        content=post["content"],
        title=post["title"],
    )

    logger.info(f"   原标题: {post['title'][:40]}")
    logger.info(f"   新标题: {new_title}")
    logger.info(f"   正文: {len(post['content'])}字 → {len(new_body)}字")

    # ===== 第5步：图片合规处理 =====
    logger.info("🔒 第5步：图片合规处理")

    masked_paths = []
    for path in local_paths:
        masked_path = image_compliance(path)
        masked_paths.append(masked_path)

    logger.success(f"✓ 合规处理完成，{len(masked_paths)} 张图片已处理")

    # ===== 第6步：发布到 MultiPost =====
    logger.info("🚀 第6步：发布到 MultiPost")
    logger.info(f"   平台: {PUBLISH_PLATFORMS}")
    logger.info(f"   图片: {len(masked_paths)} 张待上传")

    publisher = MultiPostPublisher()
    if not publisher.connect():
        logger.error("❌ 连接 Chrome 失败")
        return False

    try:
        result = publisher.publish(
            title=new_title,
            body=new_body,
            platforms=PUBLISH_PLATFORMS,
            image_paths=masked_paths,
            dry_run=dry_run,
        )
    except Exception as e:
        logger.error(f"❌ 发布异常: {e}")
        publisher.disconnect()
        return False

    publisher.disconnect()

    if result:
        logger.success("🎉 自定义帖子发布完成！")
    else:
        logger.error("❌ 发布失败")

    logger.info("=" * 55)
    logger.info("🏁 自定义帖子发布流程结束")
    logger.info("=" * 55)
    return result


# ========== 入口 ==========

if __name__ == "__main__":
    setup_logger()

    import argparse
    parser = argparse.ArgumentParser(description="脉脉自定义帖子发布")
    parser.add_argument("url", help="脉脉帖子详情页URL")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式，不点击发布按钮")
    args = parser.parse_args()

    success = run(post_url=args.url, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
