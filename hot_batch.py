"""
热点批量创作 + 发布脚本

专门用于你日常的脉脉内容创作工作流：
  1. 输入今日热点话题（多个）
  2. 用 DeepSeek 网页端 + 你的提示词工作流批量生成吐槽文章
  3. 逐篇预览，确认后发布到 MultiPost（头条/公众号）

用法：
  终端1：python3 start_chrome.py
  终端2：python3 hot_batch.py
"""

import sys
import re
from typing import Optional, List
from pathlib import Path
from loguru import logger

from config import settings, PROJECT_ROOT
from generator.deepseek_web import DeepSeekWeb
from publisher.multipost import MultiPostPublisher


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
        str(log_dir / "app_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{line} | {message}",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
    )


# 提示词模板路径
PROMPT_TEMPLATE = PROJECT_ROOT / "templates" / "hot_topic_roast.md"


def step_input_topics() -> List[str]:
    """第1步：输入今日热点话题"""
    print("\n" + "=" * 55)
    print("📌 输入今日热点话题")
    print("   一行一个，输入空行结束")
    print("=" * 55)

    topics = []
    while True:
        topic = input(f"  话题 {len(topics)+1}: ").strip()
        if not topic:
            break
        topics.append(topic)

    if not topics:
        print("  ⚠️  至少输入一个话题！")
        return step_input_topics()

    return topics


def build_prompt(topics: List[str]) -> str:
    """
    根据模板构建完整提示词
    把话题列表填入模板的 {topics} 占位符
    """
    if not PROMPT_TEMPLATE.exists():
        logger.error(f"❌ 提示词模板不存在: {PROMPT_TEMPLATE}")
        return ""

    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")

    # 把话题列表拼接
    topics_text = "\n".join(topics)

    # 替换占位符
    prompt = template.replace("{topics}", topics_text)

    return prompt


def step_generate(ds: DeepSeekWeb, topics: List[str]) -> List[dict]:
    """
    第2步：逐个话题生成文章（避免一次生成太多被截断）

    返回:
        文章列表 [{"title": ..., "body": ...}, ...]
    """
    print("\n" + "=" * 55)
    print("🤖 DeepSeek 逐个话题生成")
    print("=" * 55)
    print(f"  话题数: {len(topics)}")

    # 询问是否开启深度思考
    think = input("\n  开启深度思考？(y/n，回车=n): ").strip().lower()
    use_deep_think = (think == "y")

    # 打开 DeepSeek
    ds.open_deepseek()

    all_articles = []

    for i, topic in enumerate(topics, 1):
        print(f"\n{'─' * 55}")
        print(f"  📌 话题 {i}/{len(topics)}: {topic}")
        print(f"{'─' * 55}")

        # 每个话题新建对话（避免上下文干扰）
        ds.new_conversation()

        # 构建单话题提示词
        prompt = build_prompt([topic])
        if not prompt:
            continue

        print(f"  ⏳ 生成中...")

        reply = ds.send_and_wait(
            message=prompt,
            max_wait=180,  # 单话题3分钟足够
            use_deep_think=use_deep_think,
        )

        if reply:
            # 解析文章
            articles = parse_articles(reply)
            if articles:
                print(f"  ✓ 解析出 {len(articles)} 篇文章")
                for a in articles:
                    print(f"    • {a['title'][:30]}... ({len(a['body'])} 字)")
                all_articles.extend(articles)
            else:
                # 整段当一篇文章
                title = reply.strip().split('\n')[0][:50]
                all_articles.append({"title": title, "body": reply.strip()})
                print(f"  ✓ 1篇文章 ({len(reply)} 字)")
        else:
            print(f"  ✗ 生成失败")

        # 话题间隔，避免触发限制
        if i < len(topics):
            print(f"  ⏳ 等待 5 秒...")
            import time
            time.sleep(5)

    print(f"\n  总计生成 {len(all_articles)} 篇文章 ✓")
    return all_articles


def parse_articles(reply: str) -> List[dict]:
    """
    解析 DeepSeek 返回的多篇文章

    按常见分隔模式拆分：
    - "第X篇" / "篇X"
    - "话题：XXX" / "话题:XXX"
    - 两个连续空行
    """
    articles = []

    # 尝试按 "第X篇" 分割
    parts = re.split(r'第[一二三四五六七八九十\d]+篇[｜|]?', reply)

    if len(parts) > 1:
        # 成功按"第X篇"分割
        for part in parts[1:]:  # 跳过第一个（是前言或空）
            part = part.strip()
            if len(part) < 50:
                continue
            # 提取标题（第一行）
            lines = [l.strip() for l in part.split('\n') if l.strip()]
            title = lines[0] if lines else "无标题"
            body = '\n'.join(lines)
            articles.append({"title": title, "body": body})
    else:
        # 没有明确分割标记，整篇返回
        if len(reply.strip()) > 50:
            lines = [l.strip() for l in reply.strip().split('\n') if l.strip()]
            title = lines[0] if lines else "无标题"
            articles.append({"title": title, "body": reply.strip()})

    return articles


def step_preview_and_publish(articles: List[dict]):
    """第3步：逐篇预览并发布"""
    print("\n" + "=" * 55)
    print(f"📋 共生成 {len(articles)} 篇文章")
    print("=" * 55)

    if not articles:
        logger.warning("没有可发布的文章")
        return

    publisher = MultiPostPublisher()
    if not publisher.connect():
        logger.error("❌ 无法连接 Chrome")
        return

    try:
        for i, article in enumerate(articles, 1):
            title = article["title"]
            body = article["body"]

            print(f"\n{'─' * 55}")
            print(f"  📄 第 {i}/{len(articles)} 篇")
            print(f"  标题: {title}")
            print(f"  正文 ({len(body)} 字):")
            preview = body[:300] + ("..." if len(body) > 300 else "")
            print(f"  {preview}")
            print(f"{'─' * 55}")

            print(f"\n  选项:")
            print(f"    p = 发布此篇")
            print(f"    s = 跳过此篇")
            print(f"    q = 退出全部")

            choice = input(f"  操作 (p/s/q): ").strip().lower()

            if choice == "q":
                break
            elif choice == "s":
                logger.info(f"跳过第 {i} 篇")
                continue
            elif choice == "p":
                # 确认
                confirm = input(f"  确认发布到 头条/公众号？(y/n): ").strip().lower()
                if confirm != "y":
                    continue

                result = publisher.publish(
                    title=title,
                    body=body,
                    platforms=["微信公众号", "今日头条"],
                    dry_run=False,
                )

                if result:
                    logger.success(f"✅ 第 {i} 篇发布成功")
                else:
                    logger.error(f"❌ 第 {i} 篇发布失败")

                # 发布间隔，避免触发风控
                if i < len(articles):
                    print("\n  ⏳ 等待 10 秒后继续下一篇（避免风控）...")
                    import time
                    time.sleep(10)
    finally:
        publisher.disconnect()


def main():
    """主流程"""
    setup_logger()
    logger.info("🚀 热点批量创作启动")

    # 第1步：输入话题
    topics = step_input_topics()

    # 第2步：DeepSeek 逐个话题生成
    ds = DeepSeekWeb()
    if not ds.connect():
        logger.error("❌ 无法连接 Chrome，请先运行 python3 start_chrome.py")
        return

    try:
        articles = step_generate(ds, topics)
    finally:
        ds.disconnect()

    if not articles:
        logger.error("❌ 没有生成任何文章")
        return

    # 第3步：逐篇预览发布
    step_preview_and_publish(articles)

    logger.info("🏁 热点批量创作结束")


if __name__ == "__main__":
    main()
