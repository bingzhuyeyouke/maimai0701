"""
脉脉自动抓取发布 —— 定时全流程

功能：
  1. 抓取脉脉话题页（小渔学姐的第一篇帖子）
  2. 去重判断（已抓取则跳过）
  3. 下载帖子图片
  4. 内容合规处理（文字打码 + 图片公司名打码）
  5. 自动发布到 MultiPost（今日头条 + 公众号）

用法：
  终端1：python3 start_chrome.py          # 先启动 Chrome
  终端2：python3 auto_scrape_publish.py    # 运行一次全流程

也可以通过 cron / 系统定时任务定期调用，实现自动化运营。
"""

import sys
from loguru import logger

from config import settings, PROJECT_ROOT
from crawler.maimai import MaimaiScraper
from adapter.compliance import text_compliance, image_compliance
from publisher.multipost import MultiPostPublisher


# ========== 配置 ==========

# 目标话题页
TOPIC_URL = "https://maimai.cn/community/topic-detail/SxAXPZZ2/hot"

# 目标用户
TARGET_USERNAME = "小渔学姐"

# 发布平台
PUBLISH_PLATFORMS = ["今日头条", "微信公众号"]


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
        str(log_dir / "auto_scrape_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{line} | {message}",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
    )


# ========== 主流程 ==========

def run(dry_run: bool = False) -> bool:
    """
    执行一次完整的自动抓取发布流程

    参数:
        dry_run: 如果为 True，只走完抓取+合规流程，不点击最终发布按钮

    返回:
        True 成功，False 失败或无新内容
    """
    logger.info("=" * 55)
    logger.info("🚀 脉脉自动抓取发布流程启动")
    logger.info(f"   目标用户: {TARGET_USERNAME}")
    logger.info(f"   话题页: {TOPIC_URL}")
    logger.info(f"   发布平台: {PUBLISH_PLATFORMS}")
    logger.info(f"   干跑模式: {dry_run}")
    logger.info("=" * 55)

    # ===== 第1步：抓取脉脉帖子 =====
    logger.info("📥 第1步：抓取脉脉帖子")
    scraper = MaimaiScraper(topic_url=TOPIC_URL)

    if not scraper.connect():
        logger.error("❌ 连接 Chrome 失败，请确保已启动 Chrome（python3 start_chrome.py）")
        return False

    try:
        post = scraper.fetch_post(target_username=TARGET_USERNAME)
    except Exception as e:
        logger.error(f"❌ 抓取异常: {e}")
        scraper.disconnect()
        return False

    if not post:
        logger.warning("⚠️  未找到帖子，可能页面未加载或用户无帖子")
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

    # 保存帖子记录（标记为已抓取）
    scraper.save_post(post)
    scraper.disconnect()

    # ===== 第4步：内容合规处理 =====
    logger.info("🔒 第4步：内容合规处理")

    # 4a. 文字合规改写
    compliant_content = text_compliance(post["content"])
    # 同样对标题做合规
    compliant_title = text_compliance(post["title"])

    logger.info(f"   标题: {post['title']} → {compliant_title}")
    logger.info(f"   正文: {len(post['content'])}字 → {len(compliant_content)}字")

    # 4b. 图片合规打码
    masked_paths = []
    for path in local_paths:
        masked_path = image_compliance(path)
        masked_paths.append(masked_path)

    logger.success(f"✓ 合规处理完成，{len(masked_paths)} 张图片已处理")

    # ===== 第5步：发布到 MultiPost =====
    logger.info("🚀 第5步：发布到 MultiPost")
    logger.info(f"   平台: {PUBLISH_PLATFORMS}")
    logger.info(f"   图片: {len(masked_paths)} 张待上传")

    publisher = MultiPostPublisher()
    if not publisher.connect():
        logger.error("❌ 连接 Chrome 失败")
        return False

    try:
        result = publisher.publish(
            title=compliant_title,
            body=compliant_content,
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
        logger.success("🎉 自动抓取发布完成！")
    else:
        logger.error("❌ 发布失败")

    logger.info("=" * 55)
    logger.info("🏁 脉脉自动抓取发布流程结束")
    logger.info("=" * 55)
    return result


# ========== 入口 ==========

if __name__ == "__main__":
    setup_logger()

    # 支持 --dry-run 参数：只走流程不真正发布
    import argparse
    parser = argparse.ArgumentParser(description="脉脉自动抓取发布")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式，不点击发布按钮")
    args = parser.parse_args()

    success = run(dry_run=args.dry_run)
    sys.exit(0 if success else 1)
