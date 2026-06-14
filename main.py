"""
自媒体创作与多平台分发助手 - 入口文件

完整流程：
  1. 输入热点话题（手动）
  2. AI 生成文章草稿（DeepSeek API）
  3. 自动发布到 MultiPost（Playwright 操作浏览器）

用法：python3 main.py
"""

import sys
from pathlib import Path

from loguru import logger

from config import settings, PROJECT_ROOT
from db.database import init_db, save_topics, get_recent_topics, save_article, get_articles
from crawler.hot_topics import input_topics, save_manual_topics
from generator.content import generate_article
from publisher.multipost import MultiPostPublisher


def setup_logger():
    """配置 loguru 日志"""
    logger.remove()

    # 终端输出
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <level>{message}</level>",
    )

    # 文件输出
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.add(
        str(log_dir / "app_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{line} | {message}",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
    )


def display_topics(topics: list[dict]):
    """美化显示热点话题"""
    if not topics:
        print("\n📭 暂无热点话题\n")
        return
    print("\n" + "=" * 55)
    print("📋 当前热点话题")
    print("=" * 55)
    for topic in topics:
        rank = topic["rank"]
        keyword = topic["keyword"]
        rank_icon = "🔥" if rank <= 3 else "  "
        hot_str = f"  热度:{topic['hot_value']}" if topic.get("hot_value") else ""
        print(f"  {rank_icon} {rank:>2}. {keyword}{hot_str}")
    print("=" * 55 + "\n")


def step_input_topics() -> list[dict]:
    """第1步：输入热点话题"""
    print("\n" + "📝" + "─" * 53)
    print("  步骤 1/3：输入热点话题")
    print("─" * 55)
    topics = input_topics()
    if topics:
        save_manual_topics(topics)
        display_topics(topics)
    else:
        recent = get_recent_topics(limit=20)
        if recent:
            print("📦 使用数据库中的历史话题：")
            display_topics(recent)
            topics = recent
    return topics


def step_generate_article(topics: list[dict]) -> dict | None:
    """第2步：选择话题，AI 生成文章"""
    print("\n" + "🤖" + "─" * 53)
    print("  步骤 2/3：AI 生成文章")
    print("─" * 55)

    if not topics:
        logger.error("没有话题，无法生成文章")
        return None

    # 选择话题
    print("\n请选择要生成文章的话题（输入序号）：")
    for i, t in enumerate(topics[:10], start=1):
        print(f"  {i}. {t['keyword']}")

    try:
        choice = input("\n  序号: ").strip()
        index = int(choice) - 1
        if index < 0 or index >= len(topics):
            logger.error("无效序号")
            return None
    except ValueError:
        logger.error("请输入数字")
        return None

    selected = topics[index]
    keyword = selected["keyword"]
    style = input(f"  已选择: {keyword}\n  写作风格（回车=通用）: ").strip() or "通用"

    # 调用 AI
    print(f"\n  ⏳ 正在调用 DeepSeek 生成文章...")
    article = generate_article(keyword=keyword, style=style)

    if article:
        article_id = save_article(article.model_dump())
        print(f"\n📄 生成完成！")
        print(f"  标题: {article.title}")
        print(f"  正文: {article.body[:100]}...")
        print(f"  ⚠️  请确认内容无误后再发布")
        return article.model_dump()
    else:
        logger.error("文章生成失败")
        return None


def step_publish(article: dict):
    """第3步：通过 MultiPost 发布"""
    print("\n" + "🚀" + "─" * 53)
    print("  步骤 3/3：MultiPost 多平台发布")
    print("─" * 55)

    # 确认发布
    print(f"\n  即将发布到：今日头条、微信公众号")
    print(f"  标题: {article['title']}")
    confirm = input("\n  确认发布？(y/n): ").strip().lower()

    if confirm != 'y':
        logger.info("已取消发布")
        return

    # 连接 Chrome
    publisher = MultiPostPublisher()
    if not publisher.connect():
        logger.error("无法连接 Chrome，请确保已启动带调试端口的 Chrome")
        return

    try:
        # 先干跑一次，确认流程没问题
        logger.info("先进行干跑测试（不实际发布）...")
        dry_ok = publisher.publish(
            title=article["title"],
            body=article["body"],
            dry_run=True,  # 干跑模式，不点最终发布按钮
        )

        if dry_ok:
            real_confirm = input("\n  干跑测试通过！是否真正发布？(y/n): ").strip().lower()
            if real_confirm == 'y':
                # 需要重新走流程（因为干跑后页面状态变了）
                # 实际发布时重新打开编辑器
                result = publisher.publish(
                    title=article["title"],
                    body=article["body"],
                    dry_run=False,
                )
                if result:
                    logger.success("🎉 发布完成！请到各平台确认")
                else:
                    logger.error("发布失败，请手动检查")
            else:
                logger.info("已取消发布")
        else:
            logger.error("干跑测试失败，请检查")

    finally:
        publisher.disconnect()


def main():
    """主流程"""
    setup_logger()
    logger.info("🚀 自媒体助手启动")
    init_db()

    # 第1步：输入话题
    topics = step_input_topics()
    if not topics:
        logger.warning("没有话题，退出")
        return

    # 第2步：AI 生成文章
    article = step_generate_article(topics)
    if not article:
        logger.warning("文章生成失败，退出")
        return

    # 第3步：MultiPost 发布
    step_publish(article)

    logger.info("🏁 本次运行结束")


if __name__ == "__main__":
    main()
